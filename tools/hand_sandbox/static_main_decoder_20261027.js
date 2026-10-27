'use strict';

module.exports = function() {
  console.log('[Sandbox] Starting static main.js decoder analysis...');

  const http = require('http');
  const TARGET = 'play.games.dmm.com/game/kancolle';
  const MAIN_PATH = '/kcs2/js/main.js';
  const TIMEOUT_MS = 30000;

  let request = null;
  let socket = null;
  let timeout = null;
  let finished = false;
  let sourceRequested = false;
  let mainScriptId = null;
  let mainScriptUrl = null;

  const scripts = new Map();
  const ENABLE_ID = 201;
  const SOURCE_ID = 202;

  function cleanup(status, detail) {
    if (finished) return;
    finished = true;

    if (timeout) clearTimeout(timeout);
    timeout = null;

    if (request) {
      try { request.destroy(); } catch (_) {}
      request = null;
    }

    const current = socket;
    socket = null;

    if (current) {
      try { current.close(); } catch (_) {}
    }

    console.log('[Sandbox] STATUS:', status);
    if (detail) console.log('[Sandbox] DETAIL:', detail);
  }

  function safePath(rawUrl) {
    try {
      const parsed = new URL(rawUrl);
      return parsed.pathname;
    } catch (_) {
      return '';
    }
  }

  function analyzeSource(source) {
    const patterns = [
      {
        name: '_0x460358 definition',
        regex: /(?:function\s+_0x460358\b|(?:var|let|const)\s+_0x460358\s*=|_0x460358\s*=\s*function\b)/g,
        radius: 1400,
        limit: 3
      },
      {
        name: '_0x3b2028 definition',
        regex: /(?:function\s+_0x3b2028\b|(?:var|let|const)\s+_0x3b2028\s*=|_0x3b2028\s*=\s*function\b)/g,
        radius: 1400,
        limit: 3
      },
      {
        name: '_0x466acb definition',
        regex: /(?:function\s+_0x466acb\b|(?:var|let|const)\s+_0x466acb\s*=|_0x466acb\s*=\s*(?:\([^)]*\)\s*=>|function\b))/g,
        radius: 2200,
        limit: 2
      },
      {
        name: '_0x3f23 definition',
        regex: /(?:function\s+_0x3f23\b|(?:var|let|const)\s+_0x3f23\s*=|_0x3f23\s*=\s*function\b)/g,
        radius: 900,
        limit: 2
      },
      {
        name: 'Decoder references',
        regex: /_0x460358\s*\(/g,
        radius: 240,
        limit: 6
      }
    ];

    const excerpts = [];
    const used = new Set();

    for (const pattern of patterns) {
      pattern.regex.lastIndex = 0;
      let match;
      let count = 0;

      while (
        (match = pattern.regex.exec(source)) !== null &&
        count < pattern.limit
      ) {
        const position = match.index;
        const start = Math.max(0, position - Math.floor(pattern.radius * 0.35));
        const end = Math.min(source.length, position + pattern.radius);

        const key = start + ':' + end;
        if (!used.has(key)) {
          used.add(key);
          excerpts.push({
            name: pattern.name,
            offset: position,
            start,
            end,
            text: source.slice(start, end)
          });
        }

        count++;

        if (match[0].length === 0) {
          pattern.regex.lastIndex++;
        }
      }
    }

    const report = {
      sourceLength: source.length,
      sourceReadOnly: true,
      sourceExecuted: false,
      gameFunctionsCalled: false,
      gameInputSent: false,
      excerptCount: excerpts.length,
      excerpts
    };

    console.log(
      '[Sandbox] STATIC SOURCE FINDINGS:',
      JSON.stringify(report, null, 2)
    );

    if (excerpts.length === 0) {
      cleanup(
        'NO_MATCHES',
        'Source loaded, but decoder declarations were not matched.'
      );
    } else {
      cleanup(
        'STATIC_SOURCE_ANALYSIS_SUCCESS',
        'Source text inspected only; no page code was evaluated or executed.'
      );
    }
  }

  timeout = setTimeout(() => {
    cleanup('TIMEOUT', 'Static source analysis exceeded 30 seconds.');
  }, TIMEOUT_MS);

  request = http.get(
    'http://127.0.0.1:9222/json/list',
    { timeout: 5000 },
    response => {
      if (response.statusCode !== 200) {
        return cleanup(
          'HTTP_ERROR',
          'Unexpected target-list status: ' + response.statusCode
        );
      }

      let body = '';

      response.on('data', chunk => {
        if (finished) return;
        body += chunk;

        if (Buffer.byteLength(body, 'utf8') > 1024 * 1024) {
          cleanup('HTTP_ERROR', 'Target list exceeded 1 MiB.');
        }
      });

      response.on('end', () => {
        if (finished) return;

        let targets;
        try {
          targets = JSON.parse(body);
        } catch (error) {
          return cleanup('HTTP_PARSE_ERROR', error.message);
        }

        const matches = targets.filter(target =>
          target &&
          target.type === 'webview' &&
          typeof target.url === 'string' &&
          target.url.includes(TARGET)
        );

        if (matches.length !== 1) {
          return cleanup(
            'TARGET_ERROR',
            'Matching game WebView count: ' + matches.length
          );
        }

        const debuggerUrl = matches[0].webSocketDebuggerUrl;
        if (typeof debuggerUrl !== 'string') {
          return cleanup('TARGET_ERROR', 'Debugger URL missing.');
        }

        socket = new WebSocket(debuggerUrl);

        socket.onopen = () => {
          if (finished) return;

          console.log('[Sandbox] CDP connected for static source retrieval.');

          socket.send(JSON.stringify({
            id: ENABLE_ID,
            method: 'Debugger.enable'
          }));
        };

        socket.onmessage = event => {
          if (finished) return;

          let message;
          try {
            message = JSON.parse(event.data);
          } catch (error) {
            return cleanup('CDP_PARSE_ERROR', error.message);
          }

          if (
            message.method === 'Debugger.scriptParsed' &&
            message.params
          ) {
            const item = message.params;
            const path = safePath(item.url || '');

            if (path.endsWith(MAIN_PATH)) {
              scripts.set(String(item.scriptId), {
                scriptId: String(item.scriptId),
                path,
                url: item.url || ''
              });
            }
            return;
          }

          if (message.id === ENABLE_ID) {
            if (message.error) {
              return cleanup(
                'CDP_ERROR',
                message.error.message || 'Debugger.enable failed.'
              );
            }

            const candidates = Array.from(scripts.values());

            if (candidates.length === 0) {
              return cleanup(
                'MAIN_SCRIPT_NOT_FOUND',
                'No parsed script matched /kcs2/js/main.js.'
              );
            }

            const chosen = candidates[candidates.length - 1];
            mainScriptId = chosen.scriptId;
            mainScriptUrl = chosen.path;

            console.log(
              '[Sandbox] Main script located:',
              mainScriptUrl,
              'scriptId=' + mainScriptId
            );

            if (sourceRequested) return;
            sourceRequested = true;

            socket.send(JSON.stringify({
              id: SOURCE_ID,
              method: 'Debugger.getScriptSource',
              params: { scriptId: mainScriptId }
            }));
            return;
          }

          if (message.id === SOURCE_ID) {
            if (message.error) {
              return cleanup(
                'SOURCE_ERROR',
                message.error.message || 'getScriptSource failed.'
              );
            }

            const source =
              message.result && message.result.scriptSource;

            if (typeof source !== 'string' || source.length === 0) {
              return cleanup(
                'EMPTY_SOURCE',
                'Debugger returned an empty script source.'
              );
            }

            console.log(
              '[Sandbox] Source text retrieved; length:',
              source.length
            );

            try {
              analyzeSource(source);
            } catch (error) {
              cleanup('ANALYSIS_ERROR', error.message || String(error));
            }
          }
        };

        socket.onerror = () => {
          cleanup('WS_ERROR', 'CDP WebSocket error.');
        };

        socket.onclose = () => {
          if (!finished) {
            cleanup('WS_CLOSED', 'Connection closed before completion.');
          }
        };
      });
    }
  );

  request.on('timeout', () => {
    cleanup('HTTP_TIMEOUT', 'Target discovery timed out.');
  });

  request.on('error', error => {
    if (!finished) {
      cleanup('HTTP_ERROR', error.message || 'Target discovery failed.');
    }
  });
};
