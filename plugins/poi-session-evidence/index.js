'use strict';

const { randomBytes } = require('node:crypto');

const ACTION_PREFIX = '@@KancolleBot/SessionEvidence/';
const ACTION_INIT = ACTION_PREFIX + 'INIT';
const ACTION_INVALIDATE = ACTION_PREFIX + 'INVALIDATE';
const ALLOWED_RESPONSE_ACTIONS = new Set([
  '@@Response/kcsapi/api_port/port',
  '@@Response/kcsapi/api_get_member/require_info',
]);

let bridgeInstanceId = null;

function invalidEvidence(instanceId, reason) {
  return {
    bridgeInstanceId: instanceId,
    responsePath: null,
    responseOk: false,
    observedAt: null,
    invalidated: true,
    reason,
  };
}

function reducer(state = null, action = {}) {
  if (!action || typeof action.type !== 'string') {
    return state;
  }

  if (action.type === ACTION_INIT) {
    return { evidence: invalidEvidence(action.bridgeInstanceId, 'awaiting_fresh_response') };
  }

  if (action.type === ACTION_INVALIDATE) {
    return { evidence: invalidEvidence(action.bridgeInstanceId || (state && state.evidence && state.evidence.bridgeInstanceId) || null, 'plugin_unloading') };
  }

  if (!ALLOWED_RESPONSE_ACTIONS.has(action.type)) {
    return state;
  }

  if (!bridgeInstanceId || !action.payload || typeof action.payload !== 'object') {
    return { evidence: invalidEvidence(bridgeInstanceId, 'invalid_action_payload') };
  }

  const timestamp = action.payload.time;
  if (typeof timestamp !== 'number' || !Number.isFinite(timestamp) || timestamp <= 0) {
    return { evidence: invalidEvidence(bridgeInstanceId, 'invalid_response_timestamp') };
  }

  let observedAt;
  try {
    observedAt = new Date(timestamp).toISOString();
  } catch (_) {
    return invalidEvidence(bridgeInstanceId, 'invalid_response_timestamp');
  }

  const responsePath = action.type.slice('@@Response/kcsapi/'.length);
  return {
    evidence: {
      bridgeInstanceId,
      responsePath,
      responseOk: true,
      observedAt,
      invalidated: false,
    },
  };
}

function pluginDidLoad() {
  if (typeof window === 'undefined' || window.isMain !== true || typeof window.dispatch !== 'function') {
    bridgeInstanceId = null;
    return;
  }

  bridgeInstanceId = randomBytes(16).toString('hex');
  window.dispatch({
    type: ACTION_INIT,
    bridgeInstanceId,
  });
}

function pluginWillUnload() {
  const currentId = bridgeInstanceId;
  if (currentId && typeof window !== 'undefined' && typeof window.dispatch === 'function') {
    window.dispatch({
      type: ACTION_INVALIDATE,
      bridgeInstanceId: currentId,
    });
  }
  bridgeInstanceId = null;
}

module.exports = {
  name: 'KancolleBot Session Evidence',
  reducer,
  pluginDidLoad,
  pluginWillUnload,
};
