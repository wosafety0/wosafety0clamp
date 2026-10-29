'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { verifySnapshot } = require('../tools/hand_sandbox/verify_cdp_snapshot.cjs');

test('offline snapshot integrity detects changes', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-snapshot-'));
  try {
    const source = 'fixture source';
    const name = 'main-20261029T001122.345Z.js';
    const sourcePath = path.join(dir, name);
    const metadataPath = path.join(dir, 'main-20261029T001122.345Z.metadata.json');
    const metadata = {
      status: 'SAVED',
      sourceFile: name,
      sourceBytes: Buffer.byteLength(source),
      sourceCharacters: source.length,
      sha256: crypto.createHash('sha256').update(source).digest('hex')
    };
    fs.writeFileSync(sourcePath, source);
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.equal(verifySnapshot(metadataPath).verified, true);
    fs.appendFileSync(sourcePath, 'changed');
    assert.equal(verifySnapshot(metadataPath).verified, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('offline verifier checks Unicode length and metadata integrity', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-unicode-'));
  try {
    const source = 'fleet: 日本語 🚢';
    const name = 'main-20261029T001122.345Z.js';
    const metadataPath = path.join(dir, 'main-20261029T001122.345Z.metadata.json');
    const metadata = {
      status: 'SAVED', sourceFile: name,
      sourceBytes: Buffer.byteLength(source, 'utf8'),
      sourceCharacters: source.length,
      sha256: crypto.createHash('sha256').update(source, 'utf8').digest('hex')
    };
    fs.writeFileSync(path.join(dir, name), source, 'utf8');
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.equal(verifySnapshot(metadataPath).verified, true);
    metadata.sourceCharacters += 1;
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.equal(verifySnapshot(metadataPath).verified, false);
    metadata.sourceCharacters -= 1;
    metadata.sourceBytes += 1;
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.equal(verifySnapshot(metadataPath).verified, false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('offline verifier rejects unsafe filename and malformed metadata', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-invalid-'));
  try {
    const metadataPath = path.join(dir, 'main.metadata.json');
    const metadata = {
      status: 'SAVED', sourceFile: '../main-escape.js',
      sourceBytes: 4, sourceCharacters: 4,
      sha256: 'a'.repeat(64)
    };
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.throws(() => verifySnapshot(metadataPath), /filename/);
    metadata.sourceFile = 'main-valid.js';
    metadata.sha256 = 'invalid';
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.throws(() => verifySnapshot(metadataPath), /SHA-256/);
    metadata.sha256 = 'a'.repeat(64);
    metadata.sourceBytes = -1;
    fs.writeFileSync(metadataPath, JSON.stringify(metadata));
    assert.throws(() => verifySnapshot(metadataPath), /byte count/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('offline verifier rejects invalid UTF-8 bytes', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-utf8-'));
  try {
    const name = 'main-20261029T001122.345Z.js';
    const metadataPath = path.join(dir, 'main.metadata.json');
    const bytes = Buffer.from([0xff, 0xfe, 0x00]);
    fs.writeFileSync(path.join(dir, name), bytes);
    fs.writeFileSync(metadataPath, JSON.stringify({
      status: 'SAVED', sourceFile: name, sourceBytes: bytes.length,
      sourceCharacters: 3, sha256: crypto.createHash('sha256').update(bytes).digest('hex')
    }));
    assert.throws(() => verifySnapshot(metadataPath), /valid UTF-8/);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
