'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const vm = require('node:vm');
const test = require('node:test');

// Exercise the production lexical functions without starting CDP, HTTP, or game code.
const sourcePath = path.resolve(__dirname, '../tools/hand_sandbox/static_decoder_extract_20261027.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const anchor = '  timeout = setTimeout(function() {';
assert.equal(source.split(anchor).length, 2, 'production extraction hook changed');
const instrumented = source.replace(anchor, '  return { extractDefinition, findBalancedEnd, scanExpressionEnd, extractAliasCalls, extractTableRotationBootstrap, extractStringTableArray, saveSnapshot, sanitizeScriptUrl, analyzeSource };\n' + anchor);
const logMessages = [];
const sandbox = { module: { exports: {} }, console: { log(...parts) { logMessages.push(parts.map(String).join(' ')); } }, require, Buffer, URL: require('node:url').URL };
vm.runInNewContext(instrumented, sandbox, { filename: sourcePath, timeout: 1000 });
const lexer = sandbox.module.exports();

test('extracts three named decoder definitions with exact lexical boundaries', () => {
  const fixture = [
    'function _0x3353(x) { const a = "}" ; return (x + a); }',
    'const _0x3f23 = function () { return ["a;b", "c"]; };',
    'function _0x466acb() { /* } */ return { nested: { ok: true } }; }',
    'const _0x460358 = _0x3353;'
  ].join('\n');
  for (const name of ['_0x3353', '_0x3f23', '_0x466acb', '_0x460358']) {
    const result = lexer.extractDefinition(fixture, name);
    assert.ok(result, name);
    assert.equal(result.text, fixture.slice(result.start, result.end));
    assert.ok(result.text.includes(name));
  }
  assert.equal(lexer.extractDefinition(fixture, '_0x000000'), null);
});

test('balances comments, quoted braces and nested delimiters', () => {
  const fixture = 'function _0x466acb() { /* } */ const s = "}"; return { a: [1, 2] }; }\nconst next = 1;';
  const result = lexer.extractDefinition(fixture, '_0x466acb');
  assert.ok(result);
  assert.equal(result.text, fixture.slice(0, fixture.indexOf('\n')));
  assert.ok(!result.text.includes('const next'));
});


test('extracts comma-separated arrow declarators without consuming the next initializer', () => {
  const fixture = "const alias = decode, _0x466acb = () => { const obj = { nested: [1, 2], text: '},;' }; return helper(obj, (x, y) => ({ x, y })); }, next = 3;";
  const result = lexer.extractDefinition(fixture, '_0x466acb');
  assert.ok(result);
  assert.equal(result.kind, 'assignment');
  assert.equal(result.start, fixture.indexOf('_0x466acb'));
  assert.ok(result.text.startsWith('_0x466acb = () => {'));
  assert.ok(result.text.endsWith('}'));
  assert.equal(fixture.slice(result.end, result.end + 1), ',');
  assert.ok(result.text.includes("text: '},;'"));
  assert.ok(result.text.includes('(x, y) => ({ x, y })'));
  const following = lexer.extractDefinition(fixture, 'next');
  assert.ok(following);
  assert.equal(following.text, 'next = 3;');

  const commented = "const before = 1, /* split declaration */ _0x466acb = () => ({ value: 'a,b' }), // next declarator\nnext = 4;";
  const commentedResult = lexer.extractDefinition(commented, '_0x466acb');
  assert.ok(commentedResult);
  assert.equal(commentedResult.start, commented.indexOf('_0x466acb'));
  assert.equal(commented.slice(commentedResult.end, commentedResult.end + 1), ',');
  assert.ok(commentedResult.text.includes("value: 'a,b'"));
  const commentedNext = lexer.extractDefinition(commented, 'next');
  assert.ok(commentedNext);
  assert.equal(commentedNext.text, 'next = 4;');
});

test('follows chained local decoder aliases and preserves exact factory call offsets', () => {
  const fixture = [
    'const _0x460358 = _0x3353;',
    'function _0x466acb() {',
    '  const _0x1298ca = _0x460358;',
    '  let _0x55fc9b = _0x1298ca;',
    '  return _0x55fc9b(0x1) + _0x1298ca(0x2) + _0x460358(0x3);',
    '}'
  ].join('\n');
  const factory = lexer.extractDefinition(fixture, '_0x466acb');
  assert.ok(factory);
  const result = lexer.extractAliasCalls(factory.text, '_0x460358');
  assert.deepEqual(Array.from(result.aliases).sort(), ['_0x1298ca', '_0x460358', '_0x55fc9b'].sort());
  assert.equal(result.calls.length, 3);
  assert.deepEqual(Array.from(result.calls, call => call.alias), ['_0x55fc9b', '_0x1298ca', '_0x460358']);
  for (const call of result.calls) {
    assert.equal(factory.text.slice(call.offset, call.offset + call.expression.length), call.expression);
    assert.ok(call.offset >= 0 && call.offset + call.expression.length <= factory.text.length);
  }
  assert.ok(result.calls.every(call => call.expression.endsWith(')')));
});

test('alias analysis ignores declaration-like text inside comments and quoted strings', () => {
  const fixture = [
    'function _0x466acb() {',
    '  const text = "const fakeStringAlias = _0x460358; fakeStringAlias(0x1)";',
    '  /* const fakeCommentAlias = _0x460358; fakeCommentAlias(0x2); */',
    '  return _0x460358(0x3);',
    '}'
  ].join('\n');
  const factory = lexer.extractDefinition(fixture, '_0x466acb');
  assert.ok(factory);
  const result = lexer.extractAliasCalls(factory.text, '_0x460358');
  assert.deepEqual(Array.from(result.aliases), ['_0x460358']);
  assert.equal(result.calls.length, 1);
  assert.equal(result.calls[0].expression, '_0x460358(0x3)');
});

test('extracts the string-table rotation bootstrap with exact source bounds', () => {
  const fixture = 'function decoder() { return 0; }(function(tableFactory, target) { while (true) { try { if (parseInt(tableFactory(0x11))/0x1 + parseInt(tableFactory(0x22))/0x2 === target) break; } catch (error) {} } }(table, 0xd12ca), next());';
  const decoder = lexer.extractDefinition(fixture, 'decoder');
  assert.ok(decoder);
  const result = lexer.extractTableRotationBootstrap(fixture, 'table', decoder.end);
  assert.ok(result);
  assert.equal(result.tableName, 'table');
  assert.equal(result.invocationTarget, '0xd12ca');
  assert.deepEqual(Array.from(result.decoderTerms, term => [term.decoderAlias, term.index, term.divisor]), [['tableFactory', '0x11', '0x1'], ['tableFactory', '0x22', '0x2']]);
  assert.equal(result.text, fixture.slice(result.start, result.end));
  assert.ok(result.text.includes('parseInt(tableFactory(0x11))'));
  assert.equal(lexer.extractTableRotationBootstrap(fixture, 'otherTable', decoder.end), null);
});

test('alias call extraction handles nested arguments and ignores alias declarations', () => {
  const fixture = 'function _0x466acb() { const _0x1298ca = _0x460358; return _0x1298ca(0x1, helper({ value: [1, 2] })) + _0x460358(0x2); }';
  const factory = lexer.extractDefinition(fixture, '_0x466acb');
  const result = lexer.extractAliasCalls(factory.text, '_0x460358');
  assert.equal(result.calls.length, 2);
  assert.ok(result.calls[0].expression.includes('helper({ value: [1, 2] })'));
  assert.equal(result.calls[1].alias, '_0x460358');
});

test('saves an exact source snapshot with SHA-256 metadata without executing source', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-cdp-snapshot-'));
  const fixture = '/* static fixture */\\nfunction _0x3353(x) { return x; }';
  try {
    const saved = lexer.saveSnapshot(fixture, 'https://example.test/kcs2/js/main.js?session=redacted', '758', dir);
    assert.equal(saved.ok, true);
    assert.equal(fs.readFileSync(saved.sourcePath, 'utf8'), fixture);
    assert.match(path.basename(saved.sourcePath), /^main-\d{8}T\d{6}\.\d{3}Z\.js$/);
    assert.equal(saved.sourceBytes, Buffer.byteLength(fixture, 'utf8'));
    assert.equal(saved.sha256, crypto.createHash('sha256').update(fixture, 'utf8').digest('hex'));
    const metadata = JSON.parse(fs.readFileSync(saved.metadataPath, 'utf8'));
    assert.equal(metadata.status, 'SAVED');
    assert.equal(metadata.sourceUrl, 'https://example.test/kcs2/js/main.js');
    assert.equal(metadata.scriptId, '758');
    assert.equal(metadata.sha256, saved.sha256);
    assert.equal(metadata.safety.pageSourceExecuted, false);
    assert.equal(metadata.safety.runtimeEvaluateUsed, false);
    assert.ok(!JSON.stringify(metadata).includes('session=redacted'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});



test('redacts credentials, query and fragment in snapshot metadata and printed source report', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-cdp-report-'));
  const unsafeUrl = 'https://user:private-credential@w14h.kancolle-server.com/kcs2/js/main.js?api_token=super-secret#fragment';
  const safeUrl = 'https://w14h.kancolle-server.com/kcs2/js/main.js';
  const fixture = [
    'function _0x3353(x) { return x; }',
    'function _0x3f23() { return []; }',
    'function _0x466acb() { return 1; }'
  ].join('\n');
  try {
    assert.equal(lexer.sanitizeScriptUrl(unsafeUrl), safeUrl);
    assert.equal(lexer.sanitizeScriptUrl('not a URL'), '');
    logMessages.length = 0;
    lexer.analyzeSource(fixture, unsafeUrl, '758', dir);
    const reportText = logMessages.join('\n');
    assert.ok(reportText.includes('STATIC_SOURCE_REPORT_BEGIN'));
    assert.ok(reportText.includes(safeUrl));
    assert.ok(!reportText.includes('api_token'));
    assert.ok(!reportText.includes('super-secret'));
    assert.ok(!reportText.includes('private-credential'));
    const metadataFile = fs.readdirSync(dir).find(name => name.endsWith('.metadata.json'));
    assert.ok(metadataFile);
    const metadata = JSON.parse(fs.readFileSync(path.join(dir, metadataFile), 'utf8'));
    assert.equal(metadata.sourceUrl, safeUrl);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('reports a clear failure when the snapshot destination is not writable', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-cdp-snapshot-blocked-'));
  const blocker = path.join(dir, 'not-a-directory');
  fs.writeFileSync(blocker, 'file');
  try {
    const saved = lexer.saveSnapshot('source', 'https://example.test/main.js', '1', path.join(blocker, 'snapshots'));
    assert.equal(saved.ok, false);
    assert.match(saved.error, /Source snapshot write failed/);
    assert.ok(saved.sha256);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});


test('analyzeSource reports partial extraction when a required definition is missing', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-partial-report-'));
  const fixture = [
    'function _0x3353(x) { return x; }',
    'function _0x3f23() { return []; }'
  ].join('\n');
  try {
    logMessages.length = 0;
    lexer.analyzeSource(fixture, 'https://example.test/kcs2/js/main.js', '759', dir);
    const output = logMessages.join('\n');
    const start = output.indexOf('STATIC_SOURCE_REPORT_BEGIN');
    const end = output.indexOf('STATIC_SOURCE_REPORT_END');
    assert.ok(start >= 0 && end > start);
    const jsonStart = output.indexOf('{', start + 'STATIC_SOURCE_REPORT_BEGIN'.length);
    const reportEnd = output.indexOf('\n[Sandbox] STATIC_SOURCE_REPORT_END', jsonStart);
    const report = JSON.parse(output.slice(jsonStart, reportEnd));
    assert.equal(report.status, 'PARTIAL_EXTRACTION');
    assert.equal(report.definitions.factory_0x466acb, null);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('analyzeSource reports complete extraction only when all required definitions exist', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-complete-report-'));
  const fixture = [
    'function _0x3f23() { const _0x23d509 = ["alpha", "b,c", "gamma"]; return _0x23d509; }',
    'function _0x3353(x) { x = x - 0x1; return _0x3f23()[x]; }',
    '(function(tableFactory, target) { while (true) { try { if (parseInt(tableFactory(0x11))/0x1 + parseInt(tableFactory(0x22))/0x2 === target) break; } catch (error) {} } }(_0x3f23, 0xd12ca));',
    'function _0x466acb() { return _0x460358(0x1); }',
    'const _0x460358 = _0x3353;'
  ].join('\n');
  try {
    logMessages.length = 0;
    lexer.analyzeSource(fixture, 'https://example.test/kcs2/js/main.js', '760', dir);
    const output = logMessages.join('\n');
    const start = output.indexOf('STATIC_SOURCE_REPORT_BEGIN');
    const end = output.indexOf('STATIC_SOURCE_REPORT_END');
    assert.ok(start >= 0 && end > start);
    const jsonStart = output.indexOf('{', start + 'STATIC_SOURCE_REPORT_BEGIN'.length);
    const reportEnd = output.indexOf('\n[Sandbox] STATIC_SOURCE_REPORT_END', jsonStart);
    const report = JSON.parse(output.slice(jsonStart, reportEnd));
    assert.equal(report.status, 'STATIC_EXTRACTION_COMPLETE');
    assert.equal(report.stringTableArray.name, '_0x23d509');
    assert.equal(report.stringTableArray.itemCount, 3);
    assert.equal(report.stringTableArray.characters, fixture.slice(report.stringTableArray.start, report.stringTableArray.end).length);
    assert.equal(report.stringTableRotationBootstrap.tableName, '_0x3f23');
    assert.equal(report.stringTableRotationBootstrap.invocationTarget, '0xd12ca');
    assert.equal(report.stringTableRotationBootstrap.decoderTermCount, 2);
    assert.deepEqual(Array.from(report.stringTableRotationBootstrap.decoderAliases), ['tableFactory']);
    assert.equal(
      fixture.slice(report.stringTableRotationBootstrap.start, report.stringTableRotationBootstrap.end).startsWith('(function(tableFactory'),
      true
    );
    assert.equal(report.definitions.decoder_0x3353.kind, 'function');
    assert.equal(report.definitions.stringTable_0x3f23.kind, 'function');
    assert.equal(report.definitions.factory_0x466acb.kind, 'function');
    assert.equal(report.definitions.localAlias_0x460358.kind, 'assignment');
    assert.equal(report.factoryDecoderCalls.length, 1);
    assert.equal(report.factoryDecoderIndexSummary.decoderShift, '0x1');
    assert.equal(report.factoryDecoderIndexSummary.tableEntryCount, 3);
    assert.equal(report.factoryDecoderIndexSummary.inBoundsCount, 1);
    assert.equal(report.factoryDecoderCalls[0].indexInfo.tableIndex, 0);
    assert.ok(report.factoryDecoderAliases.includes('_0x460358'));
    assert.equal(
      report.factoryDecoderCalls[0].sourceOffset,
      report.definitions.factory_0x466acb.start + report.factoryDecoderCalls[0].offset
    );
    assert.equal(
      report.factoryDecoderCalls[0].expression,
      fixture.slice(report.factoryDecoderCalls[0].sourceOffset, report.factoryDecoderCalls[0].sourceOffset + report.factoryDecoderCalls[0].expression.length)
    );
    assert.equal(report.safety.pageSourceExecuted, false);
    assert.equal(report.safety.gameFunctionsCalled, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});


test('writes full extracted definitions to a sidecar while keeping console output bounded', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-extraction-artifact-'));
  const largeLiteral = 'A'.repeat(190000);
  const fixture = [
    'function _0x3353(x) { return x; }',
    'function _0x3f23() { return ["small-table"]; }',
    'function _0x466acb() { const big = "' + largeLiteral + '"; return _0x460358(0x1); }',
    'const _0x460358 = _0x3353;'
  ].join('\n');
  try {
    logMessages.length = 0;
    lexer.analyzeSource(fixture, 'https://example.test/kcs2/js/main.js', '761', dir);
    const output = logMessages.join('\n');
    const start = output.indexOf('STATIC_SOURCE_REPORT_BEGIN');
    const end = output.indexOf('\n[Sandbox] STATIC_SOURCE_REPORT_END', start);
    assert.ok(start >= 0 && end > start);
    const jsonStart = output.indexOf('{', start + 'STATIC_SOURCE_REPORT_BEGIN'.length);
    const displayed = JSON.parse(output.slice(jsonStart, end));
    assert.ok(JSON.stringify(displayed).length < 180000);
    assert.equal(typeof displayed.definitions.factory_0x466acb.text, 'undefined');
    const artifactName = fs.readdirSync(dir).find(name => name.endsWith('.extraction.json'));
    assert.ok(artifactName);
    const artifact = JSON.parse(fs.readFileSync(path.join(dir, artifactName), 'utf8'));
    assert.equal(artifact.extractionArtifact.status, 'SAVED');
    assert.equal(artifact.definitions.factory_0x466acb.text.length > 190000, true);
    assert.ok(artifact.definitions.factory_0x466acb.text.includes(largeLiteral));
    assert.equal(artifact.safety.pageSourceExecuted, false);
    assert.equal(artifact.safety.gameFunctionsCalled, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
