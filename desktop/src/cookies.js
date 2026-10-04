'use strict';

// A site that signs you in with a cookie that has no expiry (a "session cookie") expects the
// browser to forget it on close, and Electron does: the sign-in is gone at the next launch. Here
// that is the wrong default, because you signed in on purpose and want to still be signed in
// tomorrow. So a session cookie is stored again, with an expiry, a few seconds after it appears.
//
// What this keeps is the cookie, not the sign-in it carries: the site's own session timeout still
// applies, and when it has ended the session you sign in again as before. Nothing is typed or
// stored on your behalf. The cookies sit in the service's own profile like any other.

const DAY = 24 * 60 * 60; // seconds; cookie expiry dates are in seconds
const KEEP_DAYS = 30;

// A site often sets a cookie, then sets it again (a new session id after sign-in, a rotated token).
// Storing the first value after the second had been set would put a stale value back, so each
// cookie is left alone until it has been still this long, and is then read afresh.
const SETTLE_MS = 3000;

// What cookies.set() needs to store `cookie` again with an expiry, or null when there is nothing to
// do (it already has one, or it is not a cookie we can place).
function persistentCopy(cookie, nowSeconds, days = KEEP_DAYS) {
  if (!cookie || !cookie.session) return null;
  const domain = String(cookie.domain || '');
  const host = domain.replace(/^\./, '');
  if (!host || !cookie.name) return null;
  const path = cookie.path || '/';
  const details = {
    url: `${cookie.secure ? 'https' : 'http'}://${host}${path}`,
    name: cookie.name,
    value: cookie.value,
    path,
    secure: Boolean(cookie.secure),
    httpOnly: Boolean(cookie.httpOnly),
    sameSite: cookie.sameSite || 'unspecified',
    expirationDate: nowSeconds + days * DAY,
  };
  // A cookie for a whole domain keeps covering its subdomains; a host-only one (a __Host- cookie,
  // for one) must not be given a domain, or it becomes something else.
  const hostOnly = cookie.hostOnly ?? !domain.startsWith('.');
  if (!hostOnly) details.domain = domain;
  return details;
}

const keyOf = (cookie) => `${cookie.domain}\t${cookie.path}\t${cookie.name}`;

class SessionKeeper {
  // `session` is an Electron Session (or anything with the same `cookies`).
  constructor(session, { days = KEEP_DAYS, settleMs = SETTLE_MS, now = () => Date.now() / 1000 } = {}) {
    this.cookies = session.cookies;
    this.days = days;
    this.settleMs = settleMs;
    this.now = now;
    this.waiting = new Map(); // key -> { cookie, timer }
    this.cookies.on('changed', (_event, cookie, _cause, removed) => {
      // Only a cookie being set, and only one with no expiry. The copy this class stores has an
      // expiry, so it does not come back here.
      if (!removed && cookie && cookie.session) this._wait(cookie);
    });
  }

  _wait(cookie) {
    const key = keyOf(cookie);
    const earlier = this.waiting.get(key);
    if (earlier) clearTimeout(earlier.timer);
    const timer = setTimeout(() => this._keep(key).catch(() => {}), this.settleMs);
    if (timer.unref) timer.unref();
    this.waiting.set(key, { cookie, timer });
  }

  // Store the cookie again, from what the browser holds now rather than from the event: it may have
  // been replaced or removed since. False when there was nothing to store.
  async _keep(key) {
    const entry = this.waiting.get(key);
    if (!entry) return false;
    clearTimeout(entry.timer);
    this.waiting.delete(key);
    const { cookie } = entry;
    const live = (await this.cookies.get({ name: cookie.name })).find((c) => c.domain === cookie.domain && c.path === cookie.path);
    const details = persistentCopy(live, this.now(), this.days);
    if (!details) return false;
    await this.cookies.set(details);
    return true;
  }

  // Do now what is waiting for its few seconds, and write the cookie store to disk. For when the app
  // is closing: a sign-in made just before closing is kept too.
  async flush() {
    await Promise.all([...this.waiting.keys()].map((key) => this._keep(key).catch(() => false)));
    await this.cookies.flushStore();
  }
}

module.exports = { KEEP_DAYS, SETTLE_MS, persistentCopy, SessionKeeper };
