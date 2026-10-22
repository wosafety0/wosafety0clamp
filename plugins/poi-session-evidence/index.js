'use strict';

const { randomBytes } = require('crypto');

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
    if (typeof action.bridgeInstanceId !== 'string' || action.bridgeInstanceId.length === 0) {
      return { evidence: invalidEvidence(null, 'invalid_bridge_identity') };
    }
    // A delayed INIT from an older module instance must not roll state backward.
    if (bridgeInstanceId && action.bridgeInstanceId !== bridgeInstanceId) {
      return state;
    }
    return { evidence: invalidEvidence(action.bridgeInstanceId, 'awaiting_fresh_response') };
  }

  if (action.type === ACTION_INVALIDATE) {
    const stateId = state && state.evidence && state.evidence.bridgeInstanceId;
    // An unload from an older plugin instance must not invalidate a newer one.
    if (!stateId || action.bridgeInstanceId !== stateId) {
      return state;
    }
    return { evidence: invalidEvidence(stateId, 'plugin_unloading') };
  }

  if (!ALLOWED_RESPONSE_ACTIONS.has(action.type)) {
    return state;
  }

  const stateEvidence = state && state.evidence;
  if (stateEvidence && stateEvidence.reason === 'plugin_unloading') {
    return state;
  }
  if (!bridgeInstanceId || !stateEvidence || stateEvidence.bridgeInstanceId !== bridgeInstanceId) {
    return { evidence: invalidEvidence(bridgeInstanceId, 'session_identity_mismatch') };
  }
  if (!action.payload || typeof action.payload !== 'object') {
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
    return { evidence: invalidEvidence(bridgeInstanceId, 'invalid_response_timestamp') };
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
