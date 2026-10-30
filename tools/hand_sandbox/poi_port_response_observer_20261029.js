'use strict';

/*
 * Passive Poi port-response observer for the Hand sandbox.
 *
 * Uses only loopback CDP target discovery and the read-only Network domain.
 * It never sends game input, initiates game API requests, or evaluates page JS.
 * It waits for a naturally occurring successful /kcsapi/api_port/port response.
 * Runtime compatibility has not yet been verified on the user's Poi instance.
 */
module.exports = function() {
  const http = require('http');
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const crypto = require('crypto');

  const TARGETS_URL = 'http://127.0.0.1:9222/json/list';
  const MAX_TARGET_LIST_BYTES = 1024 * 1024;
  const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
  const WAIT_MS = 120000;
  const OUTPUT_DIR = path.join(os.homedir(), 'Desktop', 'KancolleBot-Port-Snapshots');

  let request = null;
  let socket = null;
  let timer = null;
  let finished = false;
  let nextId = 1;
  let networkEnabled = false;
  let cdpEventMessages = 0;
  let networkRequestWillBeSentEvents = 0;
  let networkResponseEvents = 0;
  let networkLoadingFinishedEvents = 0;
  let networkLoadingFailedEvents = 0;
  let kancolleHostResponseEvents = 0;
  let portPathResponseEvents = 0;
  let portHttp200ResponseEvents = 0;
  let cachedResponsesSkipped = 0;
  const responses = new Map();
  const commands = new Map();

  function safePath(raw) {
    try { return new URL(raw).pathname; } catch (_) { return ''; }
  }

  function safeGameTarget(target) {
    try {
      const u = new URL(target.url);
      return u.protocol === 'https:' &&
        u.hostname === 'play.games.dmm.com' &&
        u.pathname.indexOf('/game/kancolle') >= 0 &&
        typeof target.webSocketDebuggerUrl === 'string';
    } catch (_) {
      return false;
    }
  }

  function closeSocket() {
    const current = socket;
    socket = null;
    if (current) {
      try { current.close(); } catch (_) {}
    }
  }

  function cleanup(status, detail) {
    if (finished) return;
    finished = true;
    if (timer) clearTimeout(timer);
    timer = null;
    if (request) {
      try { request.destroy(); } catch (_) {}
      request = null;
    }
    closeSocket();
    console.log('[PortObserver] STATUS:', status);
    if (detail) console.log('[PortObserver] DETAIL:', detail);
    console.log('[PortObserver] Game input: NOT SENT');
    console.log('[PortObserver] Game API request initiated by observer: NO');
    console.log('[PortObserver] Runtime.evaluate: NOT USED');
    console.log('[PortObserver] Cached responses skipped:', cachedResponsesSkipped);
  }

  function send(method, params, kind, meta) {
    if (!socket || finished) return;
    const id = nextId++;
    if (kind) commands.set(id, { kind, meta });
    socket.send(JSON.stringify({ id, method, params: params || {} }));
  }

  function saveSnapshot(meta, rawText, parsed) {
    const data = parsed.api_data;
    const fleets = data.api_deck_port;
    const ships = data.api_ship;
    const shipFields = [
      'api_id', 'api_ship_id', 'api_nowhp', 'api_maxhp',
      'api_fuel', 'api_bull', 'api_cond'
    ];
    const compactFleets = fleets.map(f => ({
      api_id: f.api_id,
      api_name: typeof f.api_name === 'string' ? f.api_name : undefined,
      api_mission: f.api_mission,
      api_ship: f.api_ship
    }));
    const compactShips = {};
    for (const ship of ships) {
      const record = {};
      for (const field of shipFields) {
        if (Object.prototype.hasOwnProperty.call(ship, field)) record[field] = ship[field];
      }
      compactShips[String(ship.api_id)] = record;
    }
    const digest = crypto.createHash('sha256').update(rawText, 'utf8').digest('hex');
    const snapshot = {
      schemaVersion: 1,
      verificationStatus: 'CAPTURED_UNDER_STATICALLY_REVIEWED_PROBE',
      source: {
        kind: 'cdp-network-response',
        path: meta.path,
        host: meta.host,
        httpStatus: meta.status,
        responseReceivedAt: meta.receivedAt,
        bodyCapturedAt: new Date().toISOString(),
        fromDiskCache: false,
        fromServiceWorker: false,
        fromPrefetchCache: false,
        responseBodySha256: digest,
        responseBodyBytes: Buffer.byteLength(rawText, 'utf8')
      },
      data: {
        api_deck_port: compactFleets,
        api_ship: compactShips
      },
      summary: {
        fleetCount: compactFleets.length,
        shipCount: Object.keys(compactShips).length,
        fleetFields: ['api_id', 'api_mission', 'api_ship'],
        shipFields
      },
      safety: {
        gameInputSent: false,
        gameApiRequestInitiated: false,
        runtimeEvaluateUsed: false,
        fullRawResponseSaved: false,
        productionFreshnessGateChanged: false
      }
    };
    const serialized = JSON.stringify(snapshot, null, 2);
    if (Buffer.byteLength(serialized, 'utf8') > MAX_RESPONSE_BYTES) {
      throw new Error('Compact snapshot exceeded the local output size limit.');
    }
    const witnessMode = process.env.KCB_POI_FRESHNESS_WITNESS_STDOUT === '1';
    let summary;
    if (witnessMode) {
      // The parent process consumes this witness in memory; do not persist fleet/ship state.
      summary = {
        persisted: false,
        fleetCount: compactFleets.length,
        shipCount: Object.keys(compactShips).length
      };
    } else {
      fs.mkdirSync(OUTPUT_DIR, { recursive: true });
      const stamp = new Date().toISOString().replace(/[-:]/g, '');
      const filePath = path.join(OUTPUT_DIR, 'poi-port-snapshot-' + stamp + '.json');
      fs.writeFileSync(filePath, serialized, { encoding: 'utf8', flag: 'wx' });
      summary = {
        persisted: true,
        filePath,
        sha256: crypto.createHash('sha256').update(serialized, 'utf8').digest('hex'),
        fleetCount: compactFleets.length,
        shipCount: Object.keys(compactShips).length
      };
    }
    return {
      summary,
      witnessJson: witnessMode ? JSON.stringify(snapshot) : null
    };
  }

  function parseApiResponse(rawText) {
    let text = String(rawText || '').trim();
    if (text.startsWith('svdata=')) text = text.slice(7);
    const parsed = JSON.parse(text);
    if (!parsed || parsed.api_result !== 1 || !parsed.api_data || typeof parsed.api_data !== 'object') {
      throw new Error('Response is not a successful Kancolle API payload.');
    }
    const data = parsed.api_data;
    if (!Array.isArray(data.api_deck_port) || data.api_deck_port.length < 1 ||
        !Array.isArray(data.api_ship) || data.api_ship.length < 1) {
      throw new Error('Port response lacks complete fleet or ship arrays.');
    }
    const fleetIds = new Set();
    for (const fleet of data.api_deck_port) {
      if (!fleet || typeof fleet !== 'object' ||
          !Number.isSafeInteger(fleet.api_id) || fleet.api_id < 1 ||
          !Array.isArray(fleet.api_mission) || fleet.api_mission.length !== 4 ||
          !Array.isArray(fleet.api_ship) || fleet.api_ship.length > 6) {
        throw new Error('Port response contains an invalid fleet record.');
      }
      if (fleetIds.has(fleet.api_id)) throw new Error('Port response contains duplicate fleet IDs.');
      fleetIds.add(fleet.api_id);
    }
    const shipIds = new Set();
    for (const ship of data.api_ship) {
      if (!ship || typeof ship !== 'object' ||
          !Number.isSafeInteger(ship.api_id) || ship.api_id < 1 ||
          !Number.isSafeInteger(ship.api_ship_id) || ship.api_ship_id < 1 ||
          !Number.isSafeInteger(ship.api_nowhp) || !Number.isSafeInteger(ship.api_maxhp) ||
          !Number.isSafeInteger(ship.api_fuel) || !Number.isSafeInteger(ship.api_bull)) {
        throw new Error('Port response contains a ship record with missing or invalid required fields.');
      }
      if (shipIds.has(ship.api_id)) throw new Error('Port response contains duplicate ship instance IDs.');
      shipIds.add(ship.api_id);
    }
    return parsed;
  }

  function processBody(commandResult, meta) {
    try {
      if (!commandResult || !commandResult.result ||
          typeof commandResult.result.body !== 'string') {
        throw new Error('CDP did not return a response body.');
      }
      const body = commandResult.result.body;
      const rawText = commandResult.result.base64Encoded
        ? Buffer.from(body, 'base64').toString('utf8')
        : body;
      if (Buffer.byteLength(rawText, 'utf8') > MAX_RESPONSE_BYTES) {
        throw new Error('Response body exceeded the capture size limit.');
      }
      const parsed = parseApiResponse(rawText);
      const saved = saveSnapshot(meta, rawText, parsed);
      cleanup('CAPTURED', JSON.stringify(saved.summary));
      if (saved.witnessJson) {
        console.log('[PortObserver] WITNESS_JSON:' + saved.witnessJson);
      }
    } catch (error) {
      cleanup('REJECTED', error && error.message ? error.message : String(error));
    }
  }

  timer = setTimeout(function() {
    cleanup('TIMEOUT', JSON.stringify({
      reason: 'No qualifying natural port response was captured within 120 seconds.',
      networkEnabled,
      cdpEventMessages,
      networkRequestWillBeSentEvents,
      networkResponseEvents,
      networkLoadingFinishedEvents,
      networkLoadingFailedEvents,
      kancolleHostResponseEvents,
      portPathResponseEvents,
      portHttp200ResponseEvents,
      cachedResponsesSkipped
    }));
  }, WAIT_MS);

  request = http.get(TARGETS_URL, { timeout: 5000, agent: false }, function(response) {
    if (response.statusCode !== 200) {
      return cleanup('TARGET_DISCOVERY_FAILED', 'Unexpected local CDP target-list status.');
    }
    let body = '';
    response.on('data', function(chunk) {
      if (finished) return;
      body += chunk;
      if (Buffer.byteLength(body, 'utf8') > MAX_TARGET_LIST_BYTES) {
        cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target list exceeded the size limit.');
      }
    });
    response.on('end', function() {
      if (finished) return;
      let targets;
      try { targets = JSON.parse(body); }
      catch (_) { return cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target list was not valid JSON.'); }
      if (!Array.isArray(targets)) {
        return cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target list must be an array.');
      }
      const matches = targets.filter(safeGameTarget);
      if (matches.length !== 1) {
        return cleanup('TARGET_DISCOVERY_FAILED', 'Expected exactly one Kancolle game page target; found ' + matches.length + '.');
      }
      let wsUrl;
      try { wsUrl = new URL(matches[0].webSocketDebuggerUrl); }
      catch (_) { return cleanup('TARGET_DISCOVERY_FAILED', 'CDP WebSocket URL was invalid.'); }
      if (wsUrl.protocol !== 'ws:' ||
          !['127.0.0.1', 'localhost', '[::1]', '::1'].includes(wsUrl.hostname) ||
          wsUrl.port !== '9222' || wsUrl.username || wsUrl.password || wsUrl.hash) {
        return cleanup('TARGET_DISCOVERY_FAILED', 'Rejected non-loopback or unexpected CDP WebSocket URL.');
      }

      socket = new WebSocket(wsUrl.toString());
      socket.onopen = function() {
        send('Network.enable', {
          maxTotalBufferSize: 16 * 1024 * 1024,
          maxResourceBufferSize: 8 * 1024 * 1024
        }, 'enable');
      };
      socket.onmessage = function(event) {
        if (finished) return;
        let message;
        try { message = JSON.parse(event.data); }
        catch (_) { return cleanup('CDP_PROTOCOL_ERROR', 'Received a non-JSON CDP message.'); }

        if (message.id && commands.has(message.id)) {
          const command = commands.get(message.id);
          commands.delete(message.id);
          if (message.error) {
            return cleanup('CDP_COMMAND_FAILED', command.kind + ' failed.');
          }
          if (command.kind === 'enable') {
            networkEnabled = true;
            console.log('[PortObserver] Network observer armed; waiting for a natural port response.');
          } else if (command.kind === 'body') {
            processBody(message, command.meta);
          }
          return;
        }

        if (!networkEnabled || !message.method || !message.params) return;
        cdpEventMessages++;
        const p = message.params;
        if (message.method === 'Network.requestWillBeSent') {
          networkRequestWillBeSentEvents++;
        } else if (message.method === 'Network.responseReceived') {
          networkResponseEvents++;
          const response = p.response || {};
          const url = typeof response.url === 'string' ? response.url : '';
          let parsedUrl;
          try { parsedUrl = new URL(url); } catch (_) { return; }
          if (/(^|\.)kancolle-server\.com$/i.test(parsedUrl.hostname)) {
            kancolleHostResponseEvents++;
          }
          const pathname = parsedUrl.pathname;
          const isPortPath = pathname.endsWith('/kcsapi/api_port/port');
          if (isPortPath) portPathResponseEvents++;
          if (isPortPath && response.status === 200) portHttp200ResponseEvents++;
          if (!isPortPath || response.status !== 200) return;
          if (!/(^|\.)kancolle-server\.com$/i.test(parsedUrl.hostname)) return;
          const fromDiskCache = response.fromDiskCache === true;
          const fromServiceWorker = response.fromServiceWorker === true;
          const fromPrefetchCache = response.fromPrefetchCache === true;
          if (fromDiskCache || fromServiceWorker || fromPrefetchCache) {
            cachedResponsesSkipped++;
            return;
          }
          responses.set(p.requestId, {
            path: pathname,
            host: parsedUrl.hostname,
            status: response.status,
            receivedAt: new Date().toISOString(),
            fromDiskCache,
            fromServiceWorker,
            fromPrefetchCache
          });
        } else if (message.method === 'Network.loadingFailed') {
          networkLoadingFailedEvents++;
          responses.delete(p.requestId);
        } else if (message.method === 'Network.loadingFinished') {
          networkLoadingFinishedEvents++;
          const meta = responses.get(p.requestId);
          if (!meta) return;
          responses.delete(p.requestId);
          send('Network.getResponseBody', { requestId: p.requestId }, 'body', meta);
        }
      };
      socket.onerror = function() {
        cleanup('CDP_CONNECTION_FAILED', 'Loopback CDP WebSocket reported an error.');
      };
      socket.onclose = function() {
        if (!finished) cleanup('CDP_CONNECTION_CLOSED', 'CDP WebSocket closed before a valid snapshot was captured.');
      };
    });
  });
  request.on('timeout', function() {
    cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target discovery timed out.');
  });
  request.on('error', function(error) {
    if (!finished) cleanup('TARGET_DISCOVERY_FAILED', error && error.message ? error.message : 'Local CDP target discovery failed.');
  });
};
