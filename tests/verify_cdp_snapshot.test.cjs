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
