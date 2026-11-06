'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const observerPath = path.resolve(
  __dirname,
  '../tools/hand_sandbox/poi_port_response_observer_20261029.js',
);
const observerSource = fs.readFileSync(observerPath, 'utf8');

async function runObserver({ stdoutWitness }) {
  const logs = [];
  const fileCalls = [];
  let socket;
  const request = new EventEmitter();
  request.destroy = () => {};

  const responseBody = 'svdata=' + JSON.stringify({
    api_result: 1,
    api_data: {
      api_deck_port: [{ api_id: 1, api_mission: [0, 0, 0, 0], api_ship: [11] }],
      api_ship: [{
        api_id: 11,
        api_ship_id: 1,
        api_nowhp: 10,
        api_maxhp: 10,
        api_fuel: 15,
        api_bull: 20,
      }],
      api_material: [
        { api_id: 1, api_value: 100 },
        { api_id: 2, api_value: 100 },
      ],
    },
  });

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
        queueMicrotask(() => this.onmessage({
          data: JSON.stringify({
            id: message.id,
            result: { body: responseBody, base64Encoded: false },
          }),
        }));
      }
    }
    close() {}
  }

  const fakeFs = {
    mkdirSync(...args) { fileCalls.push(['mkdirSync', ...args]); },
    writeFileSync(...args) { fileCalls.push(['writeFileSync', ...args]); },
  };

  const sandbox = {
    module: { exports: {} },
    process: { env: { KCB_POI_FRESHNESS_WITNESS_STDOUT: stdoutWitness ? '1' : '0' } },
    require(name) {
      if (name === 'http') return http;
      if (name === 'fs') return fakeFs;
      if (name === 'path') return path;
      if (name === 'os') return require('node:os');
      if (name === 'crypto') return require('node:crypto');
      throw new Error('Unexpected module: ' + name);
    },
    console: { log(...args) { logs.push(args.join(' ')); } },
    Buffer,
    URL,
    WebSocket: FakeWebSocket,
    setTimeout() { return 1; },
    clearTimeout() {},
  };

  vm.runInNewContext(observerSource, sandbox, { filename: observerPath });
  sandbox.module.exports();
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(socket, 'observer should connect to the discovered game target');
  socket.onopen();
  await new Promise(resolve => setImmediate(resolve));

  socket.onmessage({ data: JSON.stringify({
    method: 'Network.responseReceived',
    params: {
      requestId: 'port-request',
      response: {
        url: 'https://w14h.kancolle-server.com/kcsapi/api_port/port',
        status: 200,
        fromDiskCache: false,
        fromServiceWorker: false,
        fromPrefetchCache: false,
      },
    },
  }) });
  socket.onmessage({ data: JSON.stringify({
    method: 'Network.loadingFinished',
    params: { requestId: 'port-request' },
  }) });
  await new Promise(resolve => setImmediate(resolve));

  return { logs, fileCalls };
}

test('stdout witness mode returns the witness without writing desktop files', async () => {
  const { logs, fileCalls } = await runObserver({ stdoutWitness: true });
  assert.equal(fileCalls.length, 0);
  const line = logs.find(item => item.startsWith('[PortObserver] WITNESS_JSON:'));
  assert.ok(line, 'captured witness should be emitted to stdout');
  const witness = JSON.parse(line.slice('[PortObserver] WITNESS_JSON:'.length));
  assert.equal(witness.source.path, '/kcsapi/api_port/port');
  assert.equal(witness.data.api_deck_port.length, 1);
});

test('standalone snapshot mode keeps its explicit file export behavior', async () => {
  const { logs, fileCalls } = await runObserver({ stdoutWitness: false });
  assert.deepEqual(fileCalls.map(call => call[0]), ['mkdirSync', 'writeFileSync']);
  assert.ok(logs.some(item => item.startsWith('[PortObserver] STATUS: CAPTURED')));
  assert.ok(!logs.some(item => item.startsWith('[PortObserver] WITNESS_JSON:')));
});