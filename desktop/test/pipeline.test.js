'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { startRun, fetchStatus, describe, ago } = require('../src/pipeline');

const fakeResponse = (status, body) => ({ ok: status < 400, status, json: async () => body });
const NOW = new Date('2026-10-03T15:00:00Z');

test('starting a run POSTs to /api/run with the bearer token', async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    return fakeResponse(202, { started: true, running: true });
  };
  const result = await startRun({ holeUrl: 'https://hole.example/some/page', token: 'secret', fetchImpl });
  assert.equal(seen.url, 'https://hole.example/api/run');
  assert.equal(seen.init.method, 'POST');
  assert.equal(seen.init.headers.Authorization, 'Bearer secret');
  assert.deepEqual(result, { started: true, running: true });
});

test('a run already going is an answer, not an error', async () => {
  const fetchImpl = async () => fakeResponse(409, { started: false, running: true, error: 'a run is already going' });
  assert.deepEqual(await startRun({ holeUrl: 'https://h.example', token: 'x', fetchImpl }), { started: false, running: true });
});

test('errors carry the server message, and missing settings are caught first', async () => {
  const refused = async () => fakeResponse(401, { error: 'bad token' });
  await assert.rejects(startRun({ holeUrl: 'https://h.example', token: 'x', fetchImpl: refused }), /bad token/);
  await assert.rejects(fetchStatus({ holeUrl: 'https://h.example', token: 'x', fetchImpl: refused }), /bad token/);
  await assert.rejects(startRun({ holeUrl: '', token: 'x' }), /Settings/);
  await assert.rejects(fetchStatus({ holeUrl: 'https://h.example', token: '' }), /Settings/);
  const off = async () => fakeResponse(404, { error: 'not found' });
  await assert.rejects(startRun({ holeUrl: 'https://h.example', token: 'x', fetchImpl: off }), /not found/);
});

test('status is a GET of /api/status', async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    return fakeResponse(200, { running: false });
  };
  assert.deepEqual(await fetchStatus({ holeUrl: 'https://h.example', token: 't', fetchImpl }), { running: false });
  assert.equal(seen.url, 'https://h.example/api/status');
  assert.equal(seen.init.method, 'GET');
});

test('ago reads GMT stamps with or without an offset', () => {
  assert.equal(ago('2026-10-03T14:59:30+00:00', NOW), 'just now');
  assert.equal(ago('2026-10-03T14:30:00+00:00', NOW), '30 min ago');
  assert.equal(ago('2026-10-03T12:00:00', NOW), '3 h ago');
  assert.equal(ago('2026-10-01T15:00:00+00:00', NOW), '2 days ago');
  assert.equal(ago('not a date', NOW), '');
});

test('the toolbar line says what is running, what ran, when next, and what is left', () => {
  const idle = describe({
    running: false,
    run: { started_at: '2026-10-03T12:00:05+00:00', finished_at: '2026-10-03T12:09:00+00:00', sourced: 41, tailored: 8, sent: 0 },
    run_at: '12:00', timezone: 'GMT', auto_apply: false,
    counts: { remaining: 42, applied: 3 },
  }, NOW);
  assert.equal(idle.running, false);
  assert.equal(idle.text, 'Last run 3 h ago: 41 new, 8 drafted, 0 sent · next 12:00 GMT · 42 remaining, 3 applied');

  const going = describe({ running: true, run: { started_at: '2026-10-03T14:50:00+00:00' }, run_at: '12:00', auto_apply: true, counts: { remaining: 5, applied: 0 } }, NOW);
  assert.equal(going.running, true);
  assert.equal(going.text, 'Running now, started 10 min ago · next 12:00 GMT · 5 remaining, 0 applied · auto-apply on');

  assert.equal(describe({ running: false, run: null, run_at: '12:00' }, NOW).text, 'Not run yet · next 12:00 GMT');
  assert.equal(describe(null, NOW).text, 'Not run yet');
});
