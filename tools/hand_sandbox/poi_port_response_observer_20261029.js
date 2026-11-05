'use strict';

/*
 * Passive Poi port-response observer for the Hand sandbox.
 *
 * Uses only loopback CDP target discovery and the read-only Network domain.
 * It never sends game input, initiates game API requests, or evaluates page JS.
 * It waits for a naturally occurring successful /kcsapi/api_port/port response.
 * Runtime compatibility has not yet been verified on the user's Poi instance.
 *
 * The CDP plumbing - target discovery, the socket, the Network event routing
 * and the response-body read - lives in poi_cdp_capture_core.js, which the
 * expedition-result observer uses as well. There is one Network observation
 * mechanism in this project, not one per endpoint.
 */
module.exports = function() {
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const crypto = require('crypto');
  const { createCaptureCore } = require('./poi_cdp_capture_core.js');

  const PORT_PATH = '/kcsapi/api_port/port';
  const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;
  const OUTPUT_DIR = path.join(os.homedir(), 'Desktop', 'KancolleBot-Port-Snapshots');

  let kancolleHostResponseEvents = 0;
  let portPathResponseEvents = 0;
  let portHttp200ResponseEvents = 0;
  let saved = null;

  function saveSnapshot(meta, rawText, parsed) {
    const data = parsed.api_data;
    const fleets = data.api_deck_port;
    const ships = data.api_ship;
    const materials = data.api_material;
    const compactMaterials = materials.map(m => ({
      api_id: m.api_id,
      api_value: m.api_value
    }));
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
        api_ship: compactShips,
        api_material: compactMaterials
      },
      summary: {
        fleetCount: compactFleets.length,
        shipCount: Object.keys(compactShips).length,
        materialCount: compactMaterials.length,
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
    const stdoutWitness = process.env.KCB_POI_FRESHNESS_WITNESS_STDOUT === '1';
    const summary = {
      sha256: crypto.createHash('sha256').update(serialized, 'utf8').digest('hex'),
      fleetCount: compactFleets.length,
      shipCount: Object.keys(compactShips).length
    };
    // The in-memory production bridge must not create desktop files. Persist
    // only when explicitly running the standalone snapshot-export mode.
    if (!stdoutWitness) {
      fs.mkdirSync(OUTPUT_DIR, { recursive: true });
      const stamp = new Date().toISOString().replace(/[-:]/g, '');
      const filePath = path.join(OUTPUT_DIR, 'poi-port-snapshot-' + stamp + '.json');
      fs.writeFileSync(filePath, serialized, { encoding: 'utf8', flag: 'wx' });
      summary.filePath = filePath;
    }
    return {
      summary,
      witnessJson: stdoutWitness ? JSON.stringify(snapshot) : null
    };  }

  function parseApiResponse(rawText) {
    let text = String(rawText || '').trim();
    if (text.startsWith('svdata=')) text = text.slice(7);
    const parsed = JSON.parse(text);
    if (!parsed || parsed.api_result !== 1 || !parsed.api_data || typeof parsed.api_data !== 'object') {
      throw new Error('Response is not a successful Kancolle API payload.');
    }
    const data = parsed.api_data;
    if (!Array.isArray(data.api_deck_port) || data.api_deck_port.length < 1 ||
        !Array.isArray(data.api_ship) || data.api_ship.length < 1 ||
        !Array.isArray(data.api_material) || data.api_material.length < 2) {
      throw new Error('Port response lacks complete fleet, ship, or material arrays.');
    }
    const materialIds = new Set();
    for (const material of data.api_material) {
      if (!material || typeof material !== 'object' ||
          !Number.isSafeInteger(material.api_id) || material.api_id < 1 ||
          !Number.isSafeInteger(material.api_value) || material.api_value < 0) {
        throw new Error('Port response contains an invalid material record.');
      }
      if (materialIds.has(material.api_id)) throw new Error('Port response contains duplicate material IDs.');
      materialIds.add(material.api_id);
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

  function processBody(meta, rawText) {
    const parsed = parseApiResponse(rawText);
    saved = saveSnapshot(meta, rawText, parsed);
  }

  createCaptureCore({
    label: 'PortObserver',
    watchPaths: [PORT_PATH],
    timeoutMs: 120000,
    maxBodyRetries: 2,
    onArmed: function() {
      console.log('[PortObserver] Network observer armed; waiting for a natural port response.');
    },
    onRequest: function() {
      // The port response needs no request-side field.
      return {};
    },
    onResponse: function(p) {
      const response = p.response || {};
      const url = typeof response.url === 'string' ? response.url : '';
      let parsedUrl;
      try { parsedUrl = new URL(url); } catch (_) { return; }
      if (/(^|\.)kancolle-server\.com$/i.test(parsedUrl.hostname)) {
        kancolleHostResponseEvents++;
      }
      const isPortPath = parsedUrl.pathname.endsWith(PORT_PATH);
      if (isPortPath) portPathResponseEvents++;
      if (isPortPath && response.status === 200) portHttp200ResponseEvents++;
    },
    onBody: function(meta, body) {
      const rawText = body;
      if (Buffer.byteLength(rawText, 'utf8') > MAX_RESPONSE_BYTES) {
        throw new Error('Response body exceeded the capture size limit.');
      }
      processBody(meta, rawText);
    },
    shouldStop: function() {
      return saved !== null;
    },
    captureDetail: function() {
      return saved ? JSON.stringify(saved.summary) : null;
    },
    onStatus: function(status, detail) {
      console.log('[PortObserver] STATUS:', status);
      if (detail) console.log('[PortObserver] DETAIL:', detail);
      if (status === 'CAPTURED' && saved && saved.witnessJson) {
        console.log('[PortObserver] WITNESS_JSON:' + saved.witnessJson);
      }
    },
    timeoutDetail: function(counters) {
      return {
        reason: 'No qualifying natural port response was captured within 120 seconds.',
        networkEnabled: counters.networkEnabled,
        cdpEventMessages: counters.cdpEventMessages,
        networkRequestWillBeSentEvents: counters.networkRequestWillBeSentEvents,
        networkResponseEvents: counters.networkResponseEvents,
        networkLoadingFinishedEvents: counters.networkLoadingFinishedEvents,
        networkLoadingFailedEvents: counters.networkLoadingFailedEvents,
        kancolleHostResponseEvents: kancolleHostResponseEvents,
        portPathResponseEvents: portPathResponseEvents,
        portHttp200ResponseEvents: portHttp200ResponseEvents,
        cachedResponsesSkipped: counters.cachedResponsesSkipped
      };
    }
  });
};
