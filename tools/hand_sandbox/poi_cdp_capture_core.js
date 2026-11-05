'use strict';

/*
 * Shared, read-only CDP Network capture core.
 *
 * Both passive observers use exactly this machinery: loopback-only target
 * discovery, one WebSocket to the game page, Network.enable, path/host/status
 * filtering, and Network.getResponseBody for a request the game client has
 * already made.
 *
 * The core never sends input, never evaluates page JavaScript and never
 * initiates a game API request. It observes requests the client made on its
 * own. Everything path-specific - which URL to watch, how to read the
 * request, how to parse the body, what to do with it - belongs to the
 * observer that configures the core, not to the core itself.
 */

const http = require('http');

const TARGETS_URL = 'http://127.0.0.1:9222/json/list';
const MAX_TARGET_LIST_BYTES = 1024 * 1024;
const KANCOLLE_HOST = /(^|\.)kancolle-server\.com$/i;

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

function pathOf(raw) {
  try { return new URL(raw).pathname; } catch (_) { return ''; }
}

function matchesWatchPath(pathname, watchPaths) {
  for (const watched of watchPaths) {
    if (pathname === watched || pathname.endsWith(watched)) return true;
  }
  return false;
}

/**
 * Create a capture session.
 *
 * Options:
 *   label            stdout tag, e.g. 'PortObserver'
 *   watchPaths       absolute URL paths to follow
 *   timeoutMs        how long to wait before giving up
 *   onArmed          called once Network.enable is acknowledged
 *   onRequest        (params) -> object|null; the returned object is carried
 *                    to onBody as meta.request
 *   onResponse       (params) -> void; called for every responseReceived
 *   onBody           (meta, bodyText) -> void
 *   onBodyError      (meta, error) -> void
 *   shouldStop       (meta) -> bool; whether to finish after this body
 *   captureDetail    () -> string|null; the detail reported on capture
 *   onStatus         (status, detail) -> void; the terminal outcome
 *   timeoutDetail    (counters) -> object; the detail reported on timeout
 *   maxBodyRetries   how often a body read may be retried
 */
function createCaptureCore(options) {
  const label = options.label;
  const watchPaths = new Set(options.watchPaths || []);
  const timeoutMs = options.timeoutMs;
  const onArmed = options.onArmed || function() {};
  const onRequest = options.onRequest || function() { return null; };
  const onResponse = options.onResponse || function() {};
  const onBody = options.onBody || function() {};
  const onBodyError = options.onBodyError || function() {};
  const shouldStop = options.shouldStop || function() { return true; };
  const captureDetail = options.captureDetail || function() { return null; };
  const onStatus = options.onStatus || function() {};
  const timeoutDetail = options.timeoutDetail || function() { return {}; };
  const maxBodyRetries = Number.isSafeInteger(options.maxBodyRetries)
    ? Math.max(0, options.maxBodyRetries)
    : 2;

  let request = null;
  let socket = null;
  let timer = null;
  let finished = false;
  let nextId = 1;
  let networkEnabled = false;

  const commands = new Map();
  const requests = new Map();
  const responses = new Map();

  const counters = {
    networkEnabled: false,
    cdpEventMessages: 0,
    networkRequestWillBeSentEvents: 0,
    networkResponseEvents: 0,
    networkLoadingFinishedEvents: 0,
    networkLoadingFailedEvents: 0,
    cachedResponsesSkipped: 0,
  };

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
    onStatus(status, detail);
    console.log('[' + label + '] Game input: NOT SENT');
    console.log('[' + label + '] Game API request initiated by observer: NO');
    console.log('[' + label + '] Runtime.evaluate: NOT USED');
    console.log('[' + label + '] Cached responses skipped:', counters.cachedResponsesSkipped);
  }

  function send(method, params, kind, meta) {
    if (!socket || finished) return;
    const id = nextId++;
    if (kind) commands.set(id, { kind, meta });
    socket.send(JSON.stringify({ id, method, params: params || {} }));
  }

  function readBody(requestId, meta, attempt) {
    if (finished) return;
    send('Network.getResponseBody', { requestId }, 'body', {
      meta: meta,
      attempt: attempt,
    });
  }

  function onBodyCommand(command, message) {
    const meta = command.meta.meta;
    if (message.error) {
      // A body that cannot be read is retried a bounded number of times: the
      // buffer may not have been flushed yet. This never re-sends the game's
      // request - only the read of a response that already arrived.
      const attempt = command.meta.attempt;
      if (attempt < maxBodyRetries) {
        setTimeout(function() { readBody(meta.requestId, meta, attempt + 1); }, 250);
        return;
      }
      onBodyError(meta, new Error('CDP did not return a response body.'));
      cleanup('REJECTED', 'CDP did not return a response body.');
      return;
    }
    const result = message.result || {};
    if (typeof result.body !== 'string') {
      const attempt = command.meta.attempt;
      if (attempt < maxBodyRetries) {
        setTimeout(function() { readBody(meta.requestId, meta, attempt + 1); }, 250);
        return;
      }
      onBodyError(meta, new Error('CDP did not return a response body.'));
      cleanup('REJECTED', 'CDP did not return a response body.');
      return;
    }
    const body = result.base64Encoded
      ? Buffer.from(result.body, 'base64').toString('utf8')
      : result.body;

    let stop = false;
    try {
      onBody(meta, body);
      stop = shouldStop(meta);
    } catch (error) {
      onBodyError(meta, error);
      cleanup('REJECTED', error && error.message ? error.message : String(error));
      return;
    }
    if (stop) cleanup('CAPTURED', captureDetail());
  }

  timer = setTimeout(function() {
    cleanup('TIMEOUT', JSON.stringify(timeoutDetail(counters)));
  }, timeoutMs);

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
        return cleanup(
          'TARGET_DISCOVERY_FAILED',
          'Expected exactly one Kancolle game page target; found ' + matches.length + '.'
        );
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
            counters.networkEnabled = true;
            onArmed();
          } else if (command.kind === 'body') {
            onBodyCommand(command, message);
          }
          return;
        }

        if (!networkEnabled || !message.method || !message.params) return;
        counters.cdpEventMessages++;
        const p = message.params;

        if (message.method === 'Network.requestWillBeSent') {
          counters.networkRequestWillBeSentEvents++;
          const requestInfo = p.request || {};
          const url = typeof requestInfo.url === 'string' ? requestInfo.url : '';
          const pathname = pathOf(url);
          if (!matchesWatchPath(pathname, watchPaths)) return;
          let host = '';
          try { host = new URL(url).hostname; } catch (_) { host = ''; }
          if (!KANCOLLE_HOST.test(host)) return;
          const meta = onRequest(p);
          if (meta === null || meta === undefined) return;
          requests.set(p.requestId, meta);
          return;
        }

        if (message.method === 'Network.responseReceived') {
          counters.networkResponseEvents++;
          onResponse(p);
          const response = p.response || {};
          const url = typeof response.url === 'string' ? response.url : '';
          let parsedUrl;
          try { parsedUrl = new URL(url); } catch (_) { return; }
          const pathname = parsedUrl.pathname;
          if (!matchesWatchPath(pathname, watchPaths)) return;
          if (response.status !== 200) return;
          if (!KANCOLLE_HOST.test(parsedUrl.hostname)) return;
          const fromDiskCache = response.fromDiskCache === true;
          const fromServiceWorker = response.fromServiceWorker === true;
          const fromPrefetchCache = response.fromPrefetchCache === true;
          if (fromDiskCache || fromServiceWorker || fromPrefetchCache) {
            counters.cachedResponsesSkipped++;
            return;
          }
          responses.set(p.requestId, {
            requestId: p.requestId,
            path: pathname,
            host: parsedUrl.hostname,
            status: response.status,
            receivedAt: new Date().toISOString(),
            request: requests.get(p.requestId) || null,
          });
          return;
        }

        if (message.method === 'Network.loadingFailed') {
          counters.networkLoadingFailedEvents++;
          requests.delete(p.requestId);
          responses.delete(p.requestId);
          return;
        }

        if (message.method === 'Network.loadingFinished') {
          counters.networkLoadingFinishedEvents++;
          const meta = responses.get(p.requestId);
          if (!meta) return;
          responses.delete(p.requestId);
          readBody(p.requestId, meta, 0);
        }
      };
      socket.onerror = function() {
        cleanup('CDP_CONNECTION_FAILED', 'Loopback CDP WebSocket reported an error.');
      };
      socket.onclose = function() {
        if (!finished) cleanup('CDP_CONNECTION_CLOSED', 'CDP WebSocket closed before a valid capture was made.');
      };
    });
  });
  request.on('timeout', function() {
    cleanup('TARGET_DISCOVERY_FAILED', 'Local CDP target discovery timed out.');
  });
  request.on('error', function(error) {
    if (!finished) cleanup('TARGET_DISCOVERY_FAILED', error && error.message ? error.message : 'Local CDP target discovery failed.');
  });

  return { cleanup: cleanup };
}

module.exports = { createCaptureCore: createCaptureCore };
