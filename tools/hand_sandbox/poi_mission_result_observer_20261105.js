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
 * Only one request field is read - api_deck_id - and only the four response
 * fields the attribution needs. The raw request body and the raw response
 * body are never written out.
 */

const { createCaptureCore } = require('./poi_cdp_capture_core.js');

const RESULT_PATH = '/kcsapi/api_req_mission/result';
const MAX_BODY_BYTES = 4 * 1024 * 1024;

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
  return {
    questName: questName,
    clearResult: clearResult,
    shipIds: shipIds
  };
}

module.exports = function() {
  let witness = null;

  createCaptureCore({
    label: 'MissionResultObserver',
    watchPaths: [RESULT_PATH],
    timeoutMs: 120000,
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
          apiShipId: parsed.shipIds
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
        reason: 'No qualifying natural expedition result response was captured within 120 seconds.',
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
