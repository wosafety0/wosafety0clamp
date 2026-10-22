'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const plugin = require('../index.js');

const INVALIDATE = '@@KancolleBot/SessionEvidence/INVALIDATE';

function setupPlugin() {
  const dispatched = [];
  global.window = {
    isMain: true,
    dispatch(action) {
      dispatched.push(action);
    },
  };
  plugin.pluginDidLoad();
  assert.equal(dispatched.length, 1);
  const initial = plugin.reducer(undefined, dispatched[0]);
  return { dispatched, initial };
}

test('initial state is invalid until an allowlisted fresh response arrives', () => {
  const { initial } = setupPlugin();
  assert.equal(initial.evidence.responseOk, false);
  assert.equal(initial.evidence.invalidated, true);
  assert.equal(initial.evidence.reason, 'awaiting_fresh_response');
  assert.equal(typeof initial.evidence.bridgeInstanceId, 'string');
  assert.equal(initial.evidence.bridgeInstanceId.length, 32);
  delete global.window;
});

test('allowlisted successful Poi response stores only minimal evidence', () => {
  const { initial } = setupPlugin();
  const timestamp = 1792670370000;
  const evidence = plugin.reducer(initial, {
    type: '@@Response/kcsapi/api_port/port',
    payload: {
      time: timestamp,
      body: { account: 'must-not-leak' },
      postBody: { secret: 'must-not-leak' },
    },
  });
  assert.equal(evidence.evidence.responseOk, true);
  assert.equal(evidence.evidence.responsePath, 'api_port/port');
  assert.equal(evidence.evidence.observedAt, new Date(timestamp).toISOString());
  assert.equal(evidence.evidence.invalidated, false);
  assert.deepEqual(Object.keys(evidence.evidence).sort(), [
    'bridgeInstanceId', 'invalidated', 'observedAt', 'responseOk', 'responsePath',
  ]);
  assert.equal(JSON.stringify(evidence).includes('must-not-leak'), false);
  delete global.window;
});

test('require_info is allowlisted and unrelated actions do not refresh evidence', () => {
  const { initial } = setupPlugin();
  const evidence = plugin.reducer(initial, {
    type: '@@Response/kcsapi/api_get_member/require_info',
    payload: { time: 1792670370000 },
  });
  assert.equal(evidence.evidence.responsePath, 'api_get_member/require_info');
  assert.equal(plugin.reducer(evidence, { type: '@@Request/kcsapi/api_port/port' }), evidence);
  delete global.window;
});

test('invalid timestamps fail closed and invalidate previous evidence', () => {
  const { initial } = setupPlugin();
  const evidence = plugin.reducer(initial, {
    type: '@@Response/kcsapi/api_port/port',
    payload: { time: 1792670370000 },
  });
  for (const time of [null, true, 0, -1, Infinity, NaN, '1792670370000', 1e100]) {
    const rejected = plugin.reducer(evidence, {
      type: '@@Response/kcsapi/api_port/port',
      payload: { time },
    });
    assert.equal(rejected.evidence.responseOk, false);
    assert.equal(rejected.evidence.invalidated, true);
  }
  delete global.window;
});

test('unsupported action types cannot create evidence', () => {
  const { initial } = setupPlugin();
  const state = plugin.reducer(initial, {
    type: '@@Response/kcsapi/api_get_member/ship2',
    payload: { time: 1792670370000 },
  });
  assert.equal(state, initial);
  delete global.window;
});

test('plugin unload emits invalidation and drops the module identity', () => {
  const { dispatched, initial } = setupPlugin();
  plugin.pluginWillUnload();
  assert.equal(dispatched.at(-1).type, INVALIDATE);
  const invalidated = plugin.reducer(initial, dispatched.at(-1));
  assert.equal(invalidated.evidence.invalidated, true);
  assert.equal(invalidated.evidence.responseOk, false);
  delete global.window;
});

test('stale unload from an older bridge cannot invalidate the current session', () => {
  const { dispatched, initial } = setupPlugin();
  const oldId = initial.evidence.bridgeInstanceId;
  plugin.pluginWillUnload();
  const unload = dispatched.at(-1);
  const unloaded = plugin.reducer(initial, unload);
  assert.equal(unloaded.evidence.invalidated, true);

  const nextDispatched = [];
  global.window = { isMain: true, dispatch(action) { nextDispatched.push(action); } };
  plugin.pluginDidLoad();
  const nextInitial = plugin.reducer(unloaded, nextDispatched[0]);
  assert.notEqual(nextInitial.evidence.bridgeInstanceId, oldId);

  const afterStaleUnload = plugin.reducer(nextInitial, unload);
  assert.equal(afterStaleUnload, nextInitial);
  assert.equal(afterStaleUnload.evidence.invalidated, true);
  assert.equal(afterStaleUnload.evidence.reason, 'awaiting_fresh_response');

  const fresh = plugin.reducer(afterStaleUnload, {
    type: '@@Response/kcsapi/api_port/port',
    payload: { time: 1792670370000 },
  });
  assert.equal(fresh.evidence.bridgeInstanceId, nextInitial.evidence.bridgeInstanceId);
  assert.equal(fresh.evidence.responseOk, true);
  assert.equal(fresh.evidence.invalidated, false);
  delete global.window;
});

test('late allowlisted response cannot revive an unloaded bridge session', () => {
  const { dispatched, initial } = setupPlugin();
  plugin.pluginWillUnload();
  const unloaded = plugin.reducer(initial, dispatched.at(-1));
  const late = plugin.reducer(unloaded, {
    type: '@@Response/kcsapi/api_port/port',
    payload: { time: 1792670370000 },
  });
  assert.equal(late, unloaded);
  assert.equal(late.evidence.invalidated, true);
  assert.equal(late.evidence.reason, 'plugin_unloading');
  delete global.window;
});

test('non-main windows do not initialize a bridge identity', () => {
  const dispatched = [];
  global.window = {
    isMain: false,
    dispatch(action) { dispatched.push(action); },
  };
  plugin.pluginDidLoad();
  assert.equal(dispatched.length, 0);
  delete global.window;
});

test('package manifest satisfies Poi package-name requirement', () => {
  const manifestPath = path.join(__dirname, '..', 'package.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.match(manifest.name, /^poi-plugin-.+/);
  assert.equal(manifest.poiPlugin.id, 'kancollebot_poi_session_evidence');
});
