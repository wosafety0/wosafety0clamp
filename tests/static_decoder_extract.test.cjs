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
const instrumented = source.replace(anchor, '  return { extractDefinition, findBalancedEnd, scanExpressionEnd, saveSnapshot };\n' + anchor);
const sandbox = { module: { exports: {} }, console: { log() {} }, require, Buffer, URL: require('node:url').URL };
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


test('saves an exact source snapshot with SHA-256 metadata without executing source', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-cdp-snapshot-'));
  const fixture = '/* static fixture */\\nfunction _0x3353(x) { return x; }';
  try {
    const saved = lexer.saveSnapshot(fixture, 'https://example.test/kcs2/js/main.js?session=redacted', '758', dir);
    assert.equal(saved.ok, true);
    assert.equal(fs.readFileSync(saved.sourcePath, 'utf8'), fixture);
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
