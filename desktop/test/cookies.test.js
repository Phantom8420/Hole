'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');

const { KEEP_DAYS, persistentCopy, SessionKeeper } = require('../src/cookies');

const NOW = 1_800_000_000;
const DAY = 24 * 60 * 60;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const session = (over = {}) => ({
  name: 'sid', value: 'abc', domain: 'www.example.com', hostOnly: true, path: '/', secure: true, httpOnly: true,
  session: true, sameSite: 'lax', ...over,
});

test('a session cookie is stored again with an expiry and everything else it had', () => {
  assert.deepEqual(persistentCopy(session(), NOW), {
    url: 'https://www.example.com/',
    name: 'sid',
    value: 'abc',
    path: '/',
    secure: true,
    httpOnly: true,
    sameSite: 'lax',
    expirationDate: NOW + KEEP_DAYS * DAY,
  });
  assert.equal(persistentCopy(session(), NOW, 7).expirationDate, NOW + 7 * DAY);
});

test('a cookie that already expires, or is not one, is left alone', () => {
  assert.equal(persistentCopy(session({ session: false, expirationDate: NOW + 5 }), NOW), null);
  assert.equal(persistentCopy(undefined, NOW), null);
  assert.equal(persistentCopy(session({ domain: '' }), NOW), null);
  assert.equal(persistentCopy(session({ name: '' }), NOW), null);
});

test('a domain cookie keeps its domain and a host-only one gets none', () => {
  const wide = persistentCopy(session({ domain: '.example.com', hostOnly: false }), NOW);
  assert.equal(wide.domain, '.example.com');
  assert.equal(wide.url, 'https://example.com/');
  const narrow = persistentCopy(session({ name: '__Host-id' }), NOW);
  assert.equal('domain' in narrow, false);
  // when Electron does not say, a leading dot does
  assert.equal(persistentCopy(session({ domain: '.example.com', hostOnly: undefined }), NOW).domain, '.example.com');
  assert.equal('domain' in persistentCopy(session({ hostOnly: undefined }), NOW), false);
});

test('the address follows the cookie\'s scheme and path, and its flags carry over', () => {
  const plain = persistentCopy(session({ secure: false, httpOnly: false, path: '/app', sameSite: undefined }), NOW);
  assert.equal(plain.url, 'http://www.example.com/app');
  assert.equal(plain.path, '/app');
  assert.equal(plain.secure, false);
  assert.equal(plain.httpOnly, false);
  assert.equal(plain.sameSite, 'unspecified');
});

// The part of Electron's cookie jar the keeper uses: the 'changed' event, get, set and flushStore.
class FakeCookies extends EventEmitter {
  constructor() {
    super();
    this.live = [];
    this.stored = [];
    this.flushes = 0;
    this.refuseNext = false;
  }

  async get({ name }) {
    return this.live.filter((c) => c.name === name);
  }

  async set(details) {
    if (this.refuseNext) {
      this.refuseNext = false;
      throw new Error('refused');
    }
    this.stored.push(details);
    this.live = this.live.filter((c) => c.name !== details.name);
    this.live.push(session({ name: details.name, value: details.value, session: false, expirationDate: details.expirationDate }));
  }

  async flushStore() {
    this.flushes += 1;
  }

  // The site sets (or, with `removed`, deletes) a cookie.
  arrive(cookie, removed = false) {
    this.live = this.live.filter((c) => c.name !== cookie.name);
    if (!removed) this.live.push(cookie);
    this.emit('changed', {}, cookie, 'explicit', removed);
  }
}

const keeper = (cookies, over = {}) => new SessionKeeper({ cookies }, { settleMs: 20, now: () => NOW, ...over });

test('a session cookie is stored again once it has been still a moment', async () => {
  const cookies = new FakeCookies();
  keeper(cookies);
  cookies.arrive(session());
  assert.equal(cookies.stored.length, 0, 'it should wait for the cookie to settle');
  await sleep(150);
  assert.equal(cookies.stored.length, 1);
  assert.equal(cookies.stored[0].value, 'abc');
  assert.equal(cookies.stored[0].expirationDate, NOW + KEEP_DAYS * DAY);
});

test('a cookie set twice in a row is stored once, with its latest value', async () => {
  const cookies = new FakeCookies();
  keeper(cookies);
  cookies.arrive(session({ value: 'first' }));
  cookies.arrive(session({ value: 'second' }));
  await sleep(150);
  assert.deepEqual(cookies.stored.map((c) => c.value), ['second']);
});

test('what is stored is what the browser holds when the moment is up, not what the event said', async () => {
  const cookies = new FakeCookies();
  keeper(cookies);
  cookies.arrive(session({ value: 'old' }));
  cookies.live = [session({ value: 'rotated' })]; // replaced meanwhile, without the keeper hearing of it
  await sleep(150);
  assert.deepEqual(cookies.stored.map((c) => c.value), ['rotated']);
});

test('nothing is stored for a cookie that was removed, or already expires', async () => {
  const cookies = new FakeCookies();
  keeper(cookies);
  cookies.arrive(session({ name: 'gone' }));
  cookies.live = []; // the site deleted it before the moment was up
  cookies.arrive(session({ name: 'dropped' }), true);
  cookies.arrive(session({ name: 'dated', session: false, expirationDate: NOW + 9 }));
  await sleep(150);
  assert.deepEqual(cookies.stored, []);
});

test('the copy it stores does not start another round', async () => {
  const cookies = new FakeCookies();
  keeper(cookies);
  cookies.arrive(session());
  await sleep(150);
  cookies.emit('changed', {}, cookies.live[0], 'explicit', false); // the event for the stored copy
  await sleep(150);
  assert.equal(cookies.stored.length, 1);
});

test('flush stores what is waiting at once and writes the cookie store', async () => {
  const cookies = new FakeCookies();
  const kept = keeper(cookies, { settleMs: 60_000 });
  cookies.arrive(session({ name: 'a' }));
  cookies.arrive(session({ name: 'b' }));
  assert.equal(cookies.stored.length, 0);
  await kept.flush();
  assert.deepEqual(cookies.stored.map((c) => c.name).sort(), ['a', 'b']);
  assert.equal(cookies.flushes, 1);
  await kept.flush(); // nothing left waiting
  assert.equal(cookies.stored.length, 2);
});

test('a cookie the browser refuses does not stop the others or throw', async () => {
  const cookies = new FakeCookies();
  const kept = keeper(cookies, { settleMs: 60_000 });
  cookies.arrive(session({ name: 'one' }));
  cookies.arrive(session({ name: 'two' }));
  cookies.refuseNext = true;
  await kept.flush();
  assert.equal(cookies.stored.length, 1, 'one was refused, the other stored');
  assert.equal(cookies.flushes, 1);
});
