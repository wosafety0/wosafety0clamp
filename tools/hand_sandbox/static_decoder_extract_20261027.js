'use strict';

/*
 * Read-only static source extractor for Poi Hand sandbox.
 * It retrieves main.js through CDP Debugger.getScriptSource only.
 * It never calls Runtime.evaluate, never executes page source, never invokes
 * game functions/getters, and never sends game input.
 */

module.exports = function() {
  console.log('[Sandbox] Starting read-only static decoder extraction.');

  const http = require('http');
  const fs = require('fs');
  const path = require('path');
  const os = require('os');
  const crypto = require('crypto');
  const GAME_PAGE_PATH = '/kcs2/index.php';
  const MAIN_PATH = '/kcs2/js/main.js';
  const TIMEOUT_MS = 45000;
  const MAX_TARGET_LIST_BYTES = 1024 * 1024;
  const MAX_REPORT_CHARS = 180000;
  const BACKTICK = String.fromCharCode(96);

  let request = null;
  let socket = null;
  let timeout = null;
  let finished = false;
  let sourceRequested = false;
  let selectedScriptId = null;
  let selectedScriptUrl = null;

  const scripts = new Map();
  const PAGE_ENABLE_ID = 730;
  const FRAME_TREE_ID = 731;
  const ENABLE_ID = 732;
  const SOURCE_ID = 733;
  const FRAME_TREE_SETTLE_MS = 1000;

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
    try { return new URL(rawUrl).pathname; }
    catch (_) { return ''; }
  }

  function findBalancedEnd(text, openIndex, openChar, closeChar) {
    let depth = 0;
    let quote = '';
    let lineComment = false;
    let blockComment = false;
    let escaped = false;
    for (let i = openIndex; i < text.length; i++) {
      const c = text[i];
      const n = text[i + 1];
      if (lineComment) {
        if (c === '\n' || c === '\r') lineComment = false;
        continue;
      }
      if (blockComment) {
        if (c === '*' && n === '/') { blockComment = false; i++; }
        continue;
      }
      if (quote) {
        if (escaped) { escaped = false; continue; }
        if (c === '\\') { escaped = true; continue; }
        if (quote === BACKTICK) {
          if (c === BACKTICK) quote = '';
        } else if (c === quote) quote = '';
        continue;
      }
      if (c === '/' && n === '/') { lineComment = true; i++; continue; }
      if (c === '/' && n === '*') { blockComment = true; i++; continue; }
      if (c === '"' || c === "'" || c === BACKTICK) { quote = c; continue; }
      if (c === openChar) depth++;
      else if (c === closeChar) {
        depth--;
        if (depth === 0) return i + 1;
      }
    }
    return -1;
  }

  function scanExpressionEnd(text, start) {
    let paren = 0;
    let brace = 0;
    let bracket = 0;
    let quote = '';
    let lineComment = false;
    let blockComment = false;
    let escaped = false;
    for (let i = start; i < text.length; i++) {
      const c = text[i];
      const n = text[i + 1];
      if (lineComment) {
        if (c === '\n' || c === '\r') lineComment = false;
        continue;
      }
      if (blockComment) {
        if (c === '*' && n === '/') { blockComment = false; i++; }
        continue;
      }
      if (quote) {
        if (escaped) { escaped = false; continue; }
        if (c === '\\') { escaped = true; continue; }
        if (quote === BACKTICK) {
          if (c === BACKTICK) quote = '';
        } else if (c === quote) quote = '';
        continue;
      }
      if (c === '/' && n === '/') { lineComment = true; i++; continue; }
      if (c === '/' && n === '*') { blockComment = true; i++; continue; }
      if (c === '"' || c === "'" || c === BACKTICK) { quote = c; continue; }
      if (c === '(') paren++;
      else if (c === ')') {
        if (paren === 0 && brace === 0 && bracket === 0) return i;
        paren--;
      } else if (c === '{') brace++;
      else if (c === '}') {
        if (brace === 0 && paren === 0 && bracket === 0) return i;
        brace--;
      } else if (c === '[') bracket++;
      else if (c === ']') bracket--;
      if (c === ';' && paren === 0 && brace === 0 && bracket === 0) return i + 1;
    }
    return text.length;
  }

  function extractDefinition(source, name) {
    const functionRe = new RegExp('\\bfunction\\s*' + name + '\\s*\\(');
    const functionMatch = functionRe.exec(source);
    if (functionMatch) {
      const start = functionMatch.index;
      const bodyOpen = source.indexOf('{', functionMatch.index + functionMatch[0].length);
      if (bodyOpen < 0) return null;
      const end = findBalancedEnd(source, bodyOpen, '{', '}');
      if (end < 0) return null;
      let finish = end;
      if (source[finish] === ';') finish++;
      return { name, start, end: finish, text: source.slice(start, finish), kind: 'function' };
    }
    const assignRe = new RegExp('(?:\\bvar|\\blet|\\bconst)\\s+' + name + '\\s*=');
    const assignMatch = assignRe.exec(source);
    if (!assignMatch) return null;
    const start = assignMatch.index;
    const expressionStart = assignMatch.index + assignMatch[0].length;
    const finish = scanExpressionEnd(source, expressionStart);
    return { name, start, end: finish, text: source.slice(start, finish), kind: 'assignment' };
  }

  function findOccurrences(source, token, limit) {
    const out = [];
    let at = 0;
    while ((at = source.indexOf(token, at)) >= 0 && out.length < limit) {
      out.push(at);
      at += token.length;
    }
    return out;
  }

  function boundedContext(source, offset, before, after) {
    const start = Math.max(0, offset - before);
    const end = Math.min(source.length, offset + after);
    return { offset, start, end, text: source.slice(start, end) };
  }

  function extractCalls(functionText, alias) {
    const calls = [];
    const pattern = new RegExp('\\b' + alias + '\\s*\\(', 'g');
    let match;
    while ((match = pattern.exec(functionText)) !== null && calls.length < 300) {
      const open = functionText.indexOf('(', match.index);
      const end = findBalancedEnd(functionText, open, '(', ')');
      if (end < 0) break;
      calls.push({ offset: match.index, expression: functionText.slice(match.index, end) });
      pattern.lastIndex = end;
    }
    return calls;
  }

  function saveSnapshot(source, scriptUrl, scriptId, outputDirOverride) {
    const capturedAt = new Date().toISOString();
    const stamp = capturedAt.replace(/[-:]/g, '');
    const outputDir = outputDirOverride || path.join(os.homedir(), 'Desktop', 'KancolleBot-CDP-Snapshots');
    const base = 'main-' + stamp;
    const sourcePath = path.join(outputDir, base + '.js');
    const metadataPath = path.join(outputDir, base + '.metadata.json');
    const sourceBytes = Buffer.byteLength(source, 'utf8');
    const sha256 = crypto.createHash('sha256').update(source, 'utf8').digest('hex');
    let safeUrl = '';
    try {
      const parsed = new URL(scriptUrl || '');
      safeUrl = parsed.origin + parsed.pathname;
    } catch (_) {
      safeUrl = safePath(scriptUrl || '');
    }
    try {
      fs.mkdirSync(outputDir, { recursive: true });
      // Exclusive creation prevents accidental overwrites if timestamps collide.
      fs.writeFileSync(sourcePath, source, { encoding: 'utf8', flag: 'wx' });
    } catch (error) {
      return { ok: false, sourcePath, metadataPath, sha256, sourceBytes, error: 'Source snapshot write failed: ' + (error.message || String(error)) };
    }
    const metadata = {
      status: 'SAVED',
      capturedAt,
      sourceFile: path.basename(sourcePath),
      sourceUrl: safeUrl,
      scriptId: String(scriptId),
      retrievalMethod: 'Debugger.getScriptSource',
      sourceCharacters: source.length,
      sourceBytes,
      sha256,
      safety: {
        runtimeEvaluateUsed: false,
        pageSourceExecuted: false,
        gameFunctionsCalled: false,
        gettersInvoked: false,
        gameInputSent: false
      }
    };
    try {
      fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2) + String.fromCharCode(10), { encoding: 'utf8', flag: 'wx' });
    } catch (error) {
      return { ok: false, sourcePath, metadataPath, sha256, sourceBytes, error: 'Source saved but metadata write failed: ' + (error.message || String(error)) };
    }
    console.log('[Sandbox] SNAPSHOT_FILE:', sourcePath);
    console.log('[Sandbox] SNAPSHOT_METADATA:', metadataPath);
    console.log('[Sandbox] SNAPSHOT_SHA256:', sha256);
    console.log('[Sandbox] SNAPSHOT_BYTES:', sourceBytes);
    return { ok: true, sourcePath, metadataPath, sha256, sourceBytes, sourceCharacters: source.length };
  }

  function analyzeSource(source, scriptUrl, scriptId) {
    const snapshot = saveSnapshot(source, scriptUrl, scriptId);
    const decoder = extractDefinition(source, '_0x3353');
    const table = extractDefinition(source, '_0x3f23');
    const appFactory = extractDefinition(source, '_0x466acb');
    const aliasDecoder = extractDefinition(source, '_0x460358');

    const tableOffsets = findOccurrences(source, '_0x3f23', 120);
    const rotationContexts = [];
    for (const offset of tableOffsets) {
      if (table && offset >= table.start && offset < table.end) continue;
      rotationContexts.push(boundedContext(source, offset, 900, 1800));
    }

    const factoryCalls = appFactory ? extractCalls(appFactory.text, '_0x460358') : [];
    const report = {
      status: 'STATIC_EXTRACTION_COMPLETE',
      snapshot,
      pageSource: {
        url: scriptUrl,
        scriptId,
        sourceLength: source.length,
        sourceRetrievedBy: 'Debugger.getScriptSource',
        sourceExecuted: false
      },
      safety: {
        RuntimeEvaluateUsed: false,
        pageSourceExecuted: false,
        gameFunctionsCalled: false,
        gettersInvoked: false,
        gameInputSent: false,
        recursiveObjectTraversal: false
      },
      definitions: {
        decoder_0x3353: decoder,
        stringTable_0x3f23: table,
        factory_0x466acb: appFactory,
        localAlias_0x460358: aliasDecoder
      },
      stringTableReferencesOutsideDefinition: rotationContexts,
      factoryDecoderCalls: factoryCalls,
      notes: [
        'This file extracts source text only; it does not run the decoder.',
        'Use the saved decoder/table/rotation text for offline analysis outside the game page.',
        'If a definition is null, inspect the reported contexts and update the lexical matcher rather than evaluating page code.'
      ]
    };

    let serialized = JSON.stringify(report, null, 2);
    if (serialized.length > MAX_REPORT_CHARS) {
      report.stringTableReferencesOutsideDefinition = rotationContexts.slice(0, 12);
      report.factoryDecoderCalls = factoryCalls.slice(0, 100);
      report.outputTruncated = true;
      report.outputLimitChars = MAX_REPORT_CHARS;
      serialized = JSON.stringify(report, null, 2);
    }
    console.log('[Sandbox] STATIC_SOURCE_REPORT_BEGIN');
    console.log(serialized);
    console.log('[Sandbox] STATIC_SOURCE_REPORT_END');

    const missing = [];
    if (!decoder) missing.push('_0x3353');
    if (!table) missing.push('_0x3f23');
    if (!appFactory) missing.push('_0x466acb');
    const extractionStatus = missing.length ? 'PARTIAL_EXTRACTION' : 'STATIC_EXTRACTION_COMPLETE';
    cleanup(
      snapshot.ok ? extractionStatus : 'SNAPSHOT_WRITE_ERROR',
      snapshot.ok
        ? (missing.length ? 'Definition not found or lexical boundary unresolved: ' + missing.join(', ') : 'Snapshot saved; definitions extracted; no page execution or game input.')
        : snapshot.error
    );
  }

  timeout = setTimeout(function() {
    cleanup('TIMEOUT', 'Static source extraction exceeded 45 seconds.');
  }, TIMEOUT_MS);

  request = http.get('http://127.0.0.1:9222/json/list', { timeout: 5000 }, function(response) {
    if (response.statusCode !== 200) {
      return cleanup('HTTP_ERROR', 'Unexpected target-list status: ' + response.statusCode);
    }
    let body = '';
    response.on('data', function(chunk) {
      if (finished) return;
      body += chunk;
      if (Buffer.byteLength(body, 'utf8') > MAX_TARGET_LIST_BYTES) {
        cleanup('HTTP_ERROR', 'Target list exceeded 1 MiB.');
      }
    });
    response.on('end', function() {
      if (finished) return;
      let targets;
      try { targets = JSON.parse(body); }
      catch (error) { return cleanup('HTTP_PARSE_ERROR', error.message); }

      const candidates = targets.filter(function(target) {
        return !!target && typeof target.url === 'string' &&
          !!target.webSocketDebuggerUrl &&
          safePath(target.url).indexOf('/game/kancolle') >= 0 &&
          /play\.games\.dmm\.com$/i.test((function() {
            try { return new URL(target.url).hostname; } catch (_) { return ''; }
          })());
      });
      if (candidates.length !== 1) {
        return cleanup('LAUNCHER_TARGET_ERROR', JSON.stringify({
          targetCount: targets.length,
          launcherMatchCount: candidates.length,
          hint: 'Expected exactly one debuggable DMM KanColle launcher target.'
        }));
      }

      const target = candidates[0];
      let gameFrameCount = 0;
      let gameFrameId = null;
      let gameFrameUrl = '';
      let enableSettleTimer = null;

      function requestDebuggerEnable() {
        if (finished) return;
        console.log('[Sandbox] Game frame confirmed; enabling read-only debugger metadata.');
        socket.send(JSON.stringify({ id: ENABLE_ID, method: 'Debugger.enable' }));
      }

      function requestSourceIfReady() {
        if (finished || sourceRequested) return;
        const candidates = Array.from(scripts.values());
        if (candidates.length === 0) {
          return cleanup('MAIN_SCRIPT_NOT_FOUND', 'No parsed script matched /kcs2/js/main.js after the metadata collection window. No source was evaluated.');
        }
        const chosen = candidates[candidates.length - 1];
        selectedScriptId = chosen.scriptId;
        selectedScriptUrl = chosen.url;
        sourceRequested = true;
        socket.send(JSON.stringify({
          id: SOURCE_ID,
          method: 'Debugger.getScriptSource',
          params: { scriptId: selectedScriptId }
        }));
      }

      socket = new WebSocket(target.webSocketDebuggerUrl);
      socket.onopen = function() {
        if (finished) return;
        console.log('[Sandbox] CDP connected; requesting frame metadata.');
        socket.send(JSON.stringify({ id: PAGE_ENABLE_ID, method: 'Page.enable' }));
      };
      socket.onmessage = function(event) {
        if (finished) return;
        let message;
        try { message = JSON.parse(event.data); }
        catch (error) { return cleanup('CDP_PARSE_ERROR', error.message); }

        if (message.method === 'Debugger.scriptParsed' && message.params) {
          const item = message.params;
          const path = safePath(item.url || '');
          if (path.endsWith(MAIN_PATH)) {
            scripts.set(String(item.scriptId), {
              scriptId: String(item.scriptId),
              url: item.url || '',
              path: path
            });
          }
          return;
        }

        if (message.id === PAGE_ENABLE_ID) {
          if (message.error) return cleanup('CDP_ERROR', message.error.message || 'Page.enable failed.');
          socket.send(JSON.stringify({ id: FRAME_TREE_ID, method: 'Page.getFrameTree' }));
          return;
        }

        if (message.id === FRAME_TREE_ID) {
          if (message.error) return cleanup('CDP_ERROR', message.error.message || 'Page.getFrameTree failed.');
          const frames = [];
          function collectFrame(node) {
            if (!node || !node.frame) return;
            const frame = node.frame;
            let path = '';
            try { path = new URL(frame.url || '').pathname; } catch (_) {}
            frames.push({
              id: frame.id || '',
              name: frame.name || '',
              path: path,
              parentId: frame.parentId || ''
            });
            (node.childFrames || []).forEach(collectFrame);
          }
          collectFrame(message.result && message.result.frameTree);
          const gameFrames = frames.filter(function(frame) {
            return frame.path === GAME_PAGE_PATH;
          });
          gameFrameCount = gameFrames.length;
          if (gameFrameCount !== 1) {
            return cleanup('GAME_FRAME_ERROR', JSON.stringify({
              frameCount: frames.length,
              gameFrameCount: gameFrameCount,
              framePaths: frames.filter(function(frame) {
                return /kcs2|kancolle/i.test(frame.path);
              }).map(function(frame) { return { name: frame.name, path: frame.path }; })
            }));
          }
          gameFrameId = gameFrames[0].id;
          gameFrameUrl = gameFrames[0].path;
          console.log('[Sandbox] Game frame found; frameId=' + gameFrameId + '; path=' + gameFrameUrl);
          socket.send(JSON.stringify({ id: ENABLE_ID, method: 'Debugger.enable' }));
          return;
        }

        if (message.id === ENABLE_ID) {
          if (message.error) return cleanup('CDP_ERROR', message.error.message || 'Debugger.enable failed.');
          if (enableSettleTimer) clearTimeout(enableSettleTimer);
          enableSettleTimer = setTimeout(requestSourceIfReady, FRAME_TREE_SETTLE_MS);
          return;
        }

        if (message.id === SOURCE_ID) {
          if (message.error) return cleanup('SOURCE_ERROR', message.error.message || 'Debugger.getScriptSource failed.');
          const source = message.result && message.result.scriptSource;
          if (typeof source !== 'string' || source.length === 0) {
            return cleanup('EMPTY_SOURCE', 'Debugger returned empty source text.');
          }
          console.log('[Sandbox] Read-only source retrieved; length=' + source.length);
          try { analyzeSource(source, selectedScriptUrl, selectedScriptId); }
          catch (error) { cleanup('ANALYSIS_ERROR', error && error.stack ? error.stack : String(error)); }
        }
      };
      socket.onerror = function() { cleanup('WS_ERROR', 'CDP WebSocket error.'); };
      socket.onclose = function() {
        if (!finished) cleanup('WS_CLOSED', 'Connection closed before extraction completed.');
      };
    });
  });
  request.on('timeout', function() { cleanup('HTTP_TIMEOUT', 'CDP target discovery timed out.'); });
  request.on('error', function(error) {
    if (!finished) cleanup('HTTP_ERROR', error.message || 'CDP target discovery failed.');
  });
};
