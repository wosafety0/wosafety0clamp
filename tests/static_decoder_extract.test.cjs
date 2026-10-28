'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

// Exercise the production lexical functions without starting CDP, HTTP, or game code.
const sourcePath = path.resolve(__dirname, '../tools/hand_sandbox/static_decoder_extract_20261027.js');
const source = fs.readFileSync(sourcePath, 'utf8');
const anchor = '  timeout = setTimeout(function() {';
assert.equal(source.split(anchor).length, 2, 'production extraction hook changed');
const instrumented = source.replace(anchor, '  return { extractDefinition, findBalancedEnd, scanExpressionEnd };\n' + anchor);
const sandbox = { module: { exports: {} }, console: { log() {} }, require };
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
