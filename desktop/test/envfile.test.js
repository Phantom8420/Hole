'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { loadEnvFile, parseEnv } = require('../src/envfile');

test('parseEnv reads HOLE_ settings and ignores comments, other names and junk', () => {
  const parsed = parseEnv([
    '# development settings',
    'HOLE_URL=https://hole.example',
    '  HOLE_API_TOKEN = "abc def"  ',
    "HOLE_USER_DATA='some dir'",
    'PATH=/not/loaded',
    'JOBSEARCH_PASSWORD=not-loaded-either',
    'not a setting',
  ].join('\r\n'));
  assert.deepEqual(parsed, {
    HOLE_URL: 'https://hole.example',
    HOLE_API_TOKEN: 'abc def',
    HOLE_USER_DATA: 'some dir',
  });
});

test('loadEnvFile fills only what the environment has not already set', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hole-env-'));
  const file = path.join(dir, '.env.local');
  fs.writeFileSync(file, 'HOLE_URL=https://from-file.example\nHOLE_API_TOKEN=from-file\nHOLE_USER_DATA=\n');
  const env = { HOLE_URL: 'https://from-shell.example' };
  assert.deepEqual(loadEnvFile(file, env), ['HOLE_API_TOKEN']);
  assert.deepEqual(env, { HOLE_URL: 'https://from-shell.example', HOLE_API_TOKEN: 'from-file' });
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a missing file is not an error', () => {
  assert.deepEqual(loadEnvFile(path.join(os.tmpdir(), 'hole-no-such-dir', '.env.local'), {}), []);
});
