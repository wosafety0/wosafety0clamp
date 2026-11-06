'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const observerPath = path.resolve(
  __dirname,
  '../tools/hand_sandbox/poi_mission_result_observer_20261105.js',
);
const corePath = path.resolve(
  __dirname,
  '../tools/hand_sandbox/poi_cdp_capture_core.js',
);
const observerSource = fs.readFileSync(observerPath, 'utf8');
const coreSource = fs.readFileSync(corePath, 'utf8');

/**
 * Run the observer against a scripted CDP conversation.
 *
 * `events` are the Network messages to deliver after the socket is armed;
 * `bodies` maps a request id to the body the fake getResponseBody returns.
 */
async function runObserver({ events = [], bodies = {}, postData = 'api_deck_id=4' } = {}) {
  const logs = [];
  let socket;
  const request = new EventEmitter();
  request.destroy = () => {};

  const http = {
    get(_url, _options, callback) {
      const targetResponse = new EventEmitter();
      targetResponse.statusCode = 200;
      queueMicrotask(() => {
        callback(targetResponse);
        targetResponse.emit('data', Buffer.from(JSON.stringify([{
          type: 'webview',
          url: 'https://play.games.dmm.com/game/kancolle',
          webSocketDebuggerUrl: 'ws://127.0.0.1:9222/devtools/page/test',
        }])));
        targetResponse.emit('end');
      });
      return request;
    },
  };

  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      socket = this;
    }
    send(raw) {
      const message = JSON.parse(raw);
      if (message.method === 'Network.enable') {
        queueMicrotask(() => this.onmessage({
          data: JSON.stringify({ id: message.id, result: {} }),
        }));
      } else if (message.method === 'Network.getResponseBody') {
        const body = bodies[message.params.requestId];
        queueMicrotask(() => this.onmessage({
          data: JSON.stringify({
            id: message.id,
            result: body === undefined
              ? { body: '', base64Encoded: false }
              : { body, base64Encoded: false },
          }),
        }));
      }
    }
    close() {}
  }

  const sandbox = {
    module: { exports: {} },
    process: { env: { KCB_MISSION_RESULT_WITNESS_STDOUT: '1' } },
    require(name) {
      if (name === 'http') return http;
      if (name === 'path') return path;
      if (name === 'os') return require('node:os');
      if (name === 'crypto') return require('node:crypto');
      if (name === './poi_cdp_capture_core.js') {
        // The core is loaded in the same sandbox so its own require calls
        // resolve to the fakes above.
        const coreSandbox = { ...sandbox, module: { exports: {} } };
        vm.runInNewContext(coreSource, coreSandbox, { filename: corePath });
        return coreSandbox.module.exports;
      }
      throw new Error('Unexpected module: ' + name);
    },
    console: { log(...args) { logs.push(args.join(' ')); } },
    Buffer,
    URL,
    WebSocket: FakeWebSocket,
    setTimeout(handler) { return { handler }; },
    clearTimeout() {},
  };

  vm.runInNewContext(observerSource, sandbox, { filename: observerPath });
  sandbox.module.exports();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(socket, 'observer should connect to the discovered game target');
  socket.onopen();
  await new Promise(resolve => setImmediate(resolve));

  for (const event of events) {
    socket.onmessage({ data: JSON.stringify(event) });
    await new Promise(resolve => setImmediate(resolve));
  }

  return { logs };
}

function resultEvent(requestId, { status = 200 } = {}) {
  return {
    method: 'Network.responseReceived',
    params: {
      requestId,
      response: {
        url: 'https://w14h.kancolle-server.com/kcsapi/api_req_mission/result',
        status,
        fromDiskCache: false,
        fromServiceWorker: false,
        fromPrefetchCache: false,
      },
    },
  };
}

function requestEvent(requestId, postData = 'api_deck_id=4') {
  return {
    method: 'Network.requestWillBeSent',
    params: {
      requestId,
      wallTime: 1793868719.133,
      request: {
        url: 'https://w14h.kancolle-server.com/kcsapi/api_req_mission/result',
        method: 'POST',
        postData,
      },
    },
  };
}

function loadingFinished(requestId) {
  return {
    method: 'Network.loadingFinished',
    params: { requestId },
  };
}

const RESULT_BODY = 'svdata=' + JSON.stringify({
  api_result: 1,
  api_data: {
    api_clear_result: 2,
    api_quest_name: '東京急行',
    api_ship_id: [-1, 1437, 9885, 96144, 3483, 2158, 95819],
    api_get_material: [10, 20, 30, 40],
    api_useitem_flag: [1, 0],
    api_get_item1: { api_useitem_id: 6, api_useitem_count: 2 },
  },
});

function witnessOf(logs) {
  const line = logs.find(item => item.startsWith('[MissionResultObserver] WITNESS_JSON:'));
  assert.ok(line, 'the observer should emit a witness');
  return JSON.parse(line.slice('[MissionResultObserver] WITNESS_JSON:'.length));
}

test('the result exchange is captured with the fleet id and the ships', async () => {
  const { logs } = await runObserver({
    events: [
      requestEvent('r1'),
      resultEvent('r1'),
      loadingFinished('r1'),
    ],
    bodies: { r1: RESULT_BODY },
  });
  const witness = witnessOf(logs);
  assert.equal(witness.data.apiDeckId, 4);
  assert.equal(witness.data.apiQuestName, '東京急行');
  assert.equal(witness.data.apiClearResult, 2);
  assert.deepEqual(Array.from(witness.data.apiRewardReceipt.material), [10, 20, 30, 40]);
  assert.deepEqual(Array.from(witness.data.apiRewardReceipt.useitemFlags), [1, 0]);
  assert.equal(witness.data.apiRewardReceipt.status, 'complete');
  assert.deepEqual(
    JSON.parse(JSON.stringify(witness.data.apiRewardReceipt.items)),
    [{ slot: 1, useitemId: 6, useitemCount: 2 }],
  );
  // The -1 placeholder is not a ship.
  assert.deepEqual(
    Array.from(witness.data.apiShipId),
    [1437, 9885, 96144, 3483, 2158, 95819],
  );
  assert.equal(witness.source.path, '/kcsapi/api_req_mission/result');
  assert.equal(witness.safety.gameApiRequestInitiated, false);
  assert.equal(witness.safety.gameInputSent, false);
});

test('a response without its request is rejected instead of adopted', async () => {
  const { logs } = await runObserver({
    events: [
      resultEvent('r2'),
      loadingFinished('r2'),
    ],
    bodies: { r2: RESULT_BODY },
  });
  assert.ok(
    logs.some(item => item.startsWith('[MissionResultObserver] STATUS: REJECTED')),
    'a result with no request must be rejected',
  );
  assert.ok(
    !logs.some(item => item.startsWith('[MissionResultObserver] WITNESS_JSON:')),
    'no witness may be emitted for an unattributable result',
  );
});

test('a request without a fleet id is not adopted', async () => {
  const { logs } = await runObserver({
    events: [
      requestEvent('r3', 'api_ship_id=1'),
      resultEvent('r3'),
      loadingFinished('r3'),
    ],
    bodies: { r3: RESULT_BODY },
  });
  assert.ok(
    logs.some(item => item.startsWith('[MissionResultObserver] STATUS: REJECTED')),
    'a request without api_deck_id must be rejected',
  );
  assert.ok(
    !logs.some(item => item.startsWith('[MissionResultObserver] WITNESS_JSON:')),
  );
});

test('a failed api result is rejected', async () => {
  const { logs } = await runObserver({
    events: [
      requestEvent('r4'),
      resultEvent('r4'),
      loadingFinished('r4'),
    ],
    bodies: { r4: JSON.stringify({ api_result: 0, api_data: {} }) },
  });
  assert.ok(
    logs.some(item => item.startsWith('[MissionResultObserver] STATUS: REJECTED')),
  );
  assert.ok(
    !logs.some(item => item.startsWith('[MissionResultObserver] WITNESS_JSON:')),
  );
});

test('a non-200 response is not captured', async () => {
  const { logs } = await runObserver({
    events: [
      requestEvent('r5'),
      resultEvent('r5', { status: 500 }),
      loadingFinished('r5'),
    ],
    bodies: { r5: RESULT_BODY },
  });
  assert.ok(
    !logs.some(item => item.startsWith('[MissionResultObserver] WITNESS_JSON:')),
  );
});

test('the observer reports that it sent no input and no game request', async () => {
  const { logs } = await runObserver({
    events: [
      requestEvent('r6'),
      resultEvent('r6'),
      loadingFinished('r6'),
    ],
    bodies: { r6: RESULT_BODY },
  });
  assert.ok(logs.some(item => item.includes('Game input: NOT SENT')));
  assert.ok(logs.some(item => item.includes('Game API request initiated by observer: NO')));
  assert.ok(logs.some(item => item.includes('Runtime.evaluate: NOT USED')));
});


test('missing reward fields keep identity but mark the receipt incomplete', async () => {
  const body = 'svdata=' + JSON.stringify({
    api_result: 1,
    api_data: {
      api_clear_result: 1,
      api_quest_name: '練習航海',
      api_ship_id: [-1, 10, 11],
    },
  });
  const { logs } = await runObserver({
    events: [requestEvent('r7'), resultEvent('r7'), loadingFinished('r7')],
    bodies: { r7: body },
  });
  const receipt = witnessOf(logs).data.apiRewardReceipt;
  assert.equal(receipt.status, 'incomplete');
  assert.equal(receipt.material, null);
  assert.equal(receipt.useitemFlags, null);
});

test('a failed expedition is explicitly recorded as having no resource reward', async () => {
  const body = 'svdata=' + JSON.stringify({
    api_result: 1,
    api_data: {
      api_clear_result: 0,
      api_quest_name: '練習航海',
      api_ship_id: [-1, 10, 11],
    },
  });
  const { logs } = await runObserver({
    events: [requestEvent('r8'), resultEvent('r8'), loadingFinished('r8')],
    bodies: { r8: body },
  });
  assert.equal(witnessOf(logs).data.apiRewardReceipt.status, 'no_reward');
});

test('an unknown result code never becomes a successful reward receipt', async () => {
  const body = 'svdata=' + JSON.stringify({
    api_result: 1,
    api_data: {
      api_clear_result: 7,
      api_quest_name: '練習航海',
      api_ship_id: [-1, 10, 11],
      api_get_material: [1, 2, 3, 4],
      api_useitem_flag: [0, 0],
    },
  });
  const { logs } = await runObserver({
    events: [requestEvent('r9'), resultEvent('r9'), loadingFinished('r9')],
    bodies: { r9: body },
  });
  assert.equal(witnessOf(logs).data.apiRewardReceipt.status, 'unknown');
});

test('a flagged item without its item payload leaves the receipt incomplete', async () => {
  const body = 'svdata=' + JSON.stringify({
    api_result: 1,
    api_data: {
      api_clear_result: 1,
      api_quest_name: '練習航海',
      api_ship_id: [-1, 10, 11],
      api_get_material: [1, 2, 3, 4],
      api_useitem_flag: [1, 0],
    },
  });
  const { logs } = await runObserver({
    events: [requestEvent('r10'), resultEvent('r10'), loadingFinished('r10')],
    bodies: { r10: body },
  });
  assert.equal(witnessOf(logs).data.apiRewardReceipt.status, 'incomplete');
});
