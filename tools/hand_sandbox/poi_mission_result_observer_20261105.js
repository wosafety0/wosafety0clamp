'use strict';

/*
 * Passive observer for the Kancolle expedition-result response.
 *
 * The client itself asks the server for an expedition result and tells the
 * server which fleet it is asking about. The request therefore carries the
 * fleet id, and the response carries the expedition name, the clear result
 * and the ships that took part. Watching that exchange says which fleet
 * completed which expedition - a fact the game states, not one inferred from
 * a screen.
 *
 * The observer is passive: it never sends game input, never evaluates page
 * JavaScript and never asks the game for anything. It reads the exchange the
 * client has already made.
 *
 * Only one request field is read - api_deck_id. The response is reduced to
 * identity fields plus a small reward-receipt allowlist. Raw request and
 * response bodies are never written out.
 */

const { createCaptureCore } = require('./poi_cdp_capture_core.js');

const RESULT_PATH = '/kcsapi/api_req_mission/result';
const MAX_BODY_BYTES = 4 * 1024 * 1024;

// How long to stay armed. The caller owns the timeout; this child must not
// outlive it, and the caller must not be surprised by an earlier exit.
const WAIT_MS = (function() {
  const raw = process.env.KCB_MISSION_RESULT_TIMEOUT_MS;
  if (typeof raw !== 'string' || !/^[0-9]{1,9}$/.test(raw)) return 120000;
  const value = Number(raw);
  return value >= 1000 ? value : 120000;
})();

/**
 * Read the one request field this observer is allowed to know.
 * Returns null when the body does not carry a usable fleet id, so the
 * response is then not attributable and must not be adopted.
 */
function readDeckId(params) {
  const request = (params && params.request) || {};
  const entries = Array.isArray(request.postDataEntries) ? request.postDataEntries : [];
  let raw = null;
  for (const entry of entries) {
    if (entry && entry.name === 'api_deck_id') {
      raw = entry.value;
      break;
    }
  }
  if (raw === null && typeof request.postData === 'string') {
    raw = request.postData;
  }
  if (typeof raw !== 'string') return null;
  for (const part of raw.split('&')) {
    const separator = part.indexOf('=');
    if (separator < 0) continue;
    if (part.slice(0, separator) !== 'api_deck_id') continue;
    const value = part.slice(separator + 1);
    if (!/^[0-9]{1,9}$/.test(value)) return null;
    const deckId = Number(value);
    if (!Number.isSafeInteger(deckId) || deckId < 1) return null;
    return deckId;
  }
  return null;
}

/**
 * Read the four response fields the attribution needs. Throws when the
 * payload is not a successful result, so nothing is adopted from it.
 */
function parseResultBody(rawText) {
  let text = String(rawText || '').trim();
  if (text.startsWith('svdata=')) text = text.slice(7);
  let parsed;
  try { parsed = JSON.parse(text); }
  catch (_) { throw new Error('Result response was not valid JSON.'); }
  if (!parsed || parsed.api_result !== 1 || !parsed.api_data ||
      typeof parsed.api_data !== 'object') {
    throw new Error('Result response is not a successful Kancolle API payload.');
  }
  const data = parsed.api_data;
  const questName = typeof data.api_quest_name === 'string' ? data.api_quest_name : '';
  if (!questName) {
    throw new Error('Result response lacks the expedition name.');
  }
  const clearResult = data.api_clear_result;
  if (!Number.isSafeInteger(clearResult)) {
    throw new Error('Result response lacks a usable clear result.');
  }
  if (!Array.isArray(data.api_ship_id)) {
    throw new Error('Result response lacks the expedition ship list.');
  }
  const shipIds = [];
  for (const value of data.api_ship_id) {
    // -1 is the placeholder the game puts in front of the list; it is not a
    // ship and must never be compared against a fleet's composition.
    if (!Number.isSafeInteger(value) || value <= 0) continue;
    if (!shipIds.includes(value)) shipIds.push(value);
  }
  if (!shipIds.length) {
    throw new Error('Result response carries no expedition ship.');
  }

  // Keep only the reward fields needed to verify the settlement receipt.
  // Missing/malformed reward fields do not erase the independently useful
  // fleet-attribution witness; they make the receipt explicitly incomplete.
  const material = Array.isArray(data.api_get_material) &&
      (data.api_get_material.length === 4 || data.api_get_material.length === 8) &&
      data.api_get_material.every(value =>
        Number.isSafeInteger(value) && value >= 0
      )
    ? data.api_get_material.slice()
    : null;
  const useitemFlags = Array.isArray(data.api_useitem_flag) &&
      data.api_useitem_flag.length === 2 &&
      data.api_useitem_flag.every(value =>
        Number.isSafeInteger(value) && value >= 0
      )
    ? data.api_useitem_flag.slice()
    : null;
  const items = [];
  let receiptStatus;
  if (clearResult === 0) {
    receiptStatus = 'no_reward';
  } else if (clearResult !== 1 && clearResult !== 2) {
    receiptStatus = 'unknown';
  } else {
    let complete = material !== null && useitemFlags !== null;
    if (useitemFlags !== null) {
      for (let index = 0; index < useitemFlags.length; index += 1) {
        const flag = useitemFlags[index];
        if (flag <= 0) continue;
        const slot = index + 1;
        const item = data['api_get_item' + slot];
        if (!item || typeof item !== 'object') {
          complete = false;
          continue;
        }
        let useitemId = item.api_useitem_id;
        const useitemCount = item.api_useitem_count;
        // The legacy client parser falls back to the flag when the explicit
        // item id is non-positive. Preserve that documented behavior.
        if (Number.isSafeInteger(useitemId) && useitemId <= 0) {
          useitemId = flag;
        }
        if (!Number.isSafeInteger(useitemId) || useitemId <= 0 ||
            !Number.isSafeInteger(useitemCount) || useitemCount <= 0) {
          complete = false;
          continue;
        }
        items.push({ slot: slot, useitemId: useitemId, useitemCount: useitemCount });
      }
    }
    receiptStatus = complete ? 'complete' : 'incomplete';
  }
  return {
    questName: questName,
    clearResult: clearResult,
    shipIds: shipIds,
    rewardReceipt: {
      status: receiptStatus,
      material: material,
      useitemFlags: useitemFlags,
      items: items
    }
  };
}

module.exports = function() {
  let witness = null;

  createCaptureCore({
    label: 'MissionResultObserver',
    watchPaths: [RESULT_PATH],
    timeoutMs: WAIT_MS,
    maxBodyRetries: 2,
    onArmed: function() {
      console.log(
        '[MissionResultObserver] Network observer armed; waiting for a natural expedition result response.'
      );
    },
    onRequest: function(params) {
      const deckId = readDeckId(params);
      if (deckId === null) return null;
      const wallTime = params && typeof params.wallTime === 'number'
        ? params.wallTime
        : null;
      return {
        apiDeckId: deckId,
        requestedAt: wallTime === null
          ? null
          : new Date(wallTime * 1000).toISOString()
      };
    },
    onResponse: function() {
      // The response counter is the core's; nothing path-specific to count.
    },
    onBody: function(meta, body) {
      if (Buffer.byteLength(body, 'utf8') > MAX_BODY_BYTES) {
        throw new Error('Result response body exceeded the capture size limit.');
      }
      if (!meta.request) {
        // The request was not seen, so the fleet this result belongs to is
        // unknown. Refusing here is the point: an unattributable result must
        // never be adopted.
        throw new Error('Result response arrived without its request.');
      }
      const parsed = parseResultBody(body);
      witness = {
        schemaVersion: 1,
        source: {
          kind: 'cdp-network-mission-result',
          path: meta.path,
          host: meta.host,
          httpStatus: meta.status,
          requestedAt: meta.request.requestedAt,
          responseReceivedAt: meta.receivedAt
        },
        data: {
          apiDeckId: meta.request.apiDeckId,
          apiQuestName: parsed.questName,
          apiClearResult: parsed.clearResult,
          apiShipId: parsed.shipIds,
          apiRewardReceipt: parsed.rewardReceipt
        },
        safety: {
          gameInputSent: false,
          gameApiRequestInitiated: false,
          runtimeEvaluateUsed: false,
          rawRequestBodySaved: false,
          rawResponseBodySaved: false
        }
      };
    },
    shouldStop: function() {
      return witness !== null;
    },
    onStatus: function(status, detail) {
      console.log('[MissionResultObserver] STATUS:', status);
      if (detail) console.log('[MissionResultObserver] DETAIL:', detail);
      if (status === 'CAPTURED' && witness) {
        console.log('[MissionResultObserver] WITNESS_JSON:' + JSON.stringify(witness));
      }
    },
    timeoutDetail: function(counters) {
      return {
        reason: 'No qualifying natural expedition result response was captured within the armed window.',
        networkEnabled: counters.networkEnabled,
        cdpEventMessages: counters.cdpEventMessages,
        networkRequestWillBeSentEvents: counters.networkRequestWillBeSentEvents,
        networkResponseEvents: counters.networkResponseEvents,
        networkLoadingFinishedEvents: counters.networkLoadingFinishedEvents,
        networkLoadingFailedEvents: counters.networkLoadingFailedEvents,
        cachedResponsesSkipped: counters.cachedResponsesSkipped
      };
    }
  });
};
