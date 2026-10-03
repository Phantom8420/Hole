'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Limiter, LimitError } = require('../src/limits');

function clock(start = Date.UTC(2026, 9, 3, 12)) {
  let now = start;
  return { now: () => now, sleep: async (ms) => { now += ms; }, advance: (ms) => { now += ms; } };
}

test('a second load waits out the minimum gap', async () => {
  const c = clock();
  const limiter = new Limiter({ minGapMs: 8000, now: c.now, sleep: c.sleep });
  const start = c.now();
  await limiter.beforeNavigation('devpost');
  assert.equal(c.now() - start, 0);
  await limiter.beforeNavigation('devpost');
  assert.equal(c.now() - start, 8000);
});

test('services are budgeted separately', async () => {
  const c = clock();
  const limiter = new Limiter({ minGapMs: 8000, now: c.now, sleep: c.sleep });
  await limiter.beforeNavigation('a');
  const before = c.now();
  await limiter.beforeNavigation('b');
  assert.equal(c.now(), before);
});

test('the daily cap stops further loads, and the next day starts fresh', async () => {
  const c = clock();
  const limiter = new Limiter({ minGapMs: 0, perDay: 2, now: c.now, sleep: c.sleep });
  await limiter.beforeNavigation('x');
  await limiter.beforeNavigation('x');
  await assert.rejects(limiter.beforeNavigation('x'), LimitError);
  assert.equal(limiter.used('x'), 2);
  c.advance(24 * 60 * 60 * 1000);
  assert.equal(limiter.used('x'), 0);
  await limiter.beforeNavigation('x');
});

test('usage survives a restart on the same day', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hole-limits-'));
  const file = path.join(dir, 'usage.json');
  const c = clock();
  await new Limiter({ minGapMs: 0, now: c.now, sleep: c.sleep, file }).beforeNavigation('x');
  const again = new Limiter({ minGapMs: 0, now: c.now, sleep: c.sleep, file });
  assert.equal(again.used('x'), 1);
  fs.rmSync(dir, { recursive: true, force: true });
});
