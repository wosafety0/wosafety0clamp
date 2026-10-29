'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { verifySnapshot } = require('../tools/hand_sandbox/verify_cdp_snapshot.cjs');

test('offline verifier rejects a snapshot source symlink that points outside its directory', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kcb-snapshot-symlink-'));
  const snapshotDir = path.join(root, 'snapshot');
  const outsideDir = path.join(root, 'outside');
  const sourceName = 'main-20261029T001122.345Z.js';
  const source = 'outside source that must not be accepted through a symlink';
  const sourcePath = path.join(snapshotDir, sourceName);
  const outsidePath = path.join(outsideDir, sourceName);
  const metadataPath = path.join(snapshotDir, 'main-20261029T001122.345Z.metadata.json');
  try {
    fs.mkdirSync(snapshotDir);
    fs.mkdirSync(outsideDir);
    fs.writeFileSync(outsidePath, source, 'utf8');
    try {
      fs.symlinkSync(outsidePath, sourcePath, 'file');
    } catch (error) {
      if (process.platform === 'win32' && ['EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) {
        t.skip('Symlink creation is not permitted in this Windows environment');
        return;
      }
      throw error;
    }
    fs.writeFileSync(metadataPath, JSON.stringify({
      status: 'SAVED',
      sourceFile: sourceName,
      sourceBytes: Buffer.byteLength(source, 'utf8'),
      sourceCharacters: source.length,
      sha256: crypto.createHash('sha256').update(source, 'utf8').digest('hex')
    }));
    assert.throws(() => verifySnapshot(metadataPath), /regular file, not a symbolic link/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
