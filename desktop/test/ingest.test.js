'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { sendItems, toPayload, tokenStore } = require('../src/ingest');

const fakeResponse = (status, body) => ({ ok: status < 400, status, json: async () => body });

test('payload maps the inbox shape to the API shape', () => {
  const payload = toPayload([
    { kind: 'competition', title: 'Hack', url: 'https://h.example', deadline: '2026-11-01', selected: true, id: 'x' },
    { kind: 'page', title: 'Role', company: 'Acme' },
  ], 'linkedin');
  assert.equal(payload.source, 'linkedin');
  assert.equal(payload.items[0].kind, 'competition');
  assert.equal(payload.items[1].kind, 'job');
  assert.equal('selected' in payload.items[0], false);
});

test('sends a bearer token to /api/ingest and returns the counts', async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    return fakeResponse(200, { jobs: { new: 1, duplicate: 0 }, competitions: { new: 0, duplicate: 0 }, rejected: 0 });
  };
  const result = await sendItems({
    holeUrl: 'https://hole.example/some/path', token: 'secret', items: [{ kind: 'job', title: 'T', company: 'C' }], fetchImpl,
  });
  assert.equal(seen.url, 'https://hole.example/api/ingest');
  assert.equal(seen.init.headers.Authorization, 'Bearer secret');
  assert.equal(result.jobs.new, 1);
});

test('server errors surface their message; missing settings are caught early', async () => {
  const fetchImpl = async () => fakeResponse(401, { error: 'bad token' });
  await assert.rejects(sendItems({ holeUrl: 'https://h.example', token: 'x', items: [], fetchImpl }), /bad token/);
  await assert.rejects(sendItems({ holeUrl: '', token: 'x', items: [] }), /Settings/);
  await assert.rejects(sendItems({ holeUrl: 'https://h.example', token: '', items: [] }), /Settings/);
});

test('the token store encrypts, and refuses to fall back to plain text', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hole-token-'));
  const safe = {
    isEncryptionAvailable: () => true,
    encryptString: (s) => Buffer.from(`enc:${s}`),
    decryptString: (b) => b.toString().replace(/^enc:/, ''),
  };
  const store = tokenStore(dir, safe, {});
  assert.equal(store.present(), false);
  store.save('abc123');
  assert.equal(store.load(), 'abc123');
  assert.equal(fs.readFileSync(path.join(dir, 'ingest-token.bin'), 'utf8').includes('abc123'), true); // fake cipher; real one is opaque
  assert.throws(() => tokenStore(dir, { ...safe, isEncryptionAvailable: () => false }, {}).save('x'), /secure key storage/);
  assert.equal(tokenStore(dir, safe, { HOLE_API_TOKEN: 'from-env' }).load(), 'from-env');
  fs.rmSync(dir, { recursive: true, force: true });
});
