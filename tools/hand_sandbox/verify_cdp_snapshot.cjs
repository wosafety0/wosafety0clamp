'use strict';
// Offline-only integrity verifier. Does not connect to Poi, CDP or the game.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function verifySnapshot(metadataPath) {
  const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
  if (!metadata || metadata.status !== 'SAVED') throw new Error('Metadata status is not SAVED');
  if (typeof metadata.sourceFile !== 'string' || !metadata.sourceFile.startsWith('main-') || !metadata.sourceFile.endsWith('.js') || path.basename(metadata.sourceFile) !== metadata.sourceFile || path.win32.basename(metadata.sourceFile) !== metadata.sourceFile || metadata.sourceFile.includes('\\\\')) {
    throw new Error('Unexpected snapshot filename');
  }
  if (typeof metadata.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(metadata.sha256)) {
    throw new Error('Invalid expected SHA-256');
  }
  if (!Number.isSafeInteger(metadata.sourceBytes) || metadata.sourceBytes < 1) {
    throw new Error('Invalid expected byte count');
  }
  if (!Number.isSafeInteger(metadata.sourceCharacters) || metadata.sourceCharacters < 1) {
    throw new Error('Invalid expected character count');
  }
  const sourcePath = path.join(path.dirname(path.resolve(metadataPath)), metadata.sourceFile);
  const sourceStat = fs.lstatSync(sourcePath);
  if (!sourceStat.isFile() || sourceStat.isSymbolicLink()) {
    throw new Error('Snapshot source must be a regular file, not a symbolic link');
  }
  const bytes = fs.readFileSync(sourcePath);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  const decoded = bytes.toString('utf8');
  if (!Buffer.from(decoded, 'utf8').equals(bytes)) throw new Error('Source is not valid UTF-8');
  const result = {
    verified: digest === metadata.sha256 &&
      bytes.length === metadata.sourceBytes &&
      decoded.length === metadata.sourceCharacters,
    sourceFile: metadata.sourceFile,
    sourceBytes: bytes.length,
    sourceCharacters: decoded.length,
    sha256: digest,
    expectedSha256: metadata.sha256,
    expectedBytes: metadata.sourceBytes,
    expectedCharacters: metadata.sourceCharacters
  };
  return result;
}

if (require.main === module) {
  const metadataPath = process.argv[2];
  if (!metadataPath) {
    console.error('Usage: node verify_cdp_snapshot.cjs <snapshot.metadata.json>');
    process.exitCode = 2;
  } else {
    try {
      const result = verifySnapshot(metadataPath);
      console.log(JSON.stringify(result, null, 2));
      if (!result.verified) process.exitCode = 1;
    } catch (error) {
      console.error('SNAPSHOT_VERIFY_ERROR:', error.message);
      process.exitCode = 1;
    }
  }
}
module.exports = { verifySnapshot };
