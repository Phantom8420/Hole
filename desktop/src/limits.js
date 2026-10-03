'use strict';

const fs = require('node:fs');

class LimitError extends Error {}

// A load budget per service: a minimum gap between page loads the app starts
// itself, and a daily cap. Pages you open by clicking around do not count --
// only navigations made through the Driver. The gap is fixed, not randomised:
// the point is to keep the app polite, not to look like someone else.
class Limiter {
  constructor({ minGapMs = 8000, perDay = 60, file = null, now = Date.now, sleep } = {}) {
    this.minGapMs = minGapMs;
    this.perDay = perDay;
    this.file = file;
    this.now = now;
    this.sleep = sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.last = new Map();
    this.usage = this._load();
  }

  _today() {
    return new Date(this.now()).toISOString().slice(0, 10);
  }

  _load() {
    try {
      const saved = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      if (saved.date === this._today() && saved.counts) return saved;
    } catch {
      // first run, or an unreadable file: start the day at zero
    }
    return { date: this._today(), counts: {} };
  }

  _save() {
    if (!this.file) return;
    try {
      fs.writeFileSync(this.file, JSON.stringify(this.usage));
    } catch {
      // the budget still holds in memory for this session
    }
  }

  used(serviceId) {
    if (this.usage.date !== this._today()) this.usage = { date: this._today(), counts: {} };
    return this.usage.counts[serviceId] || 0;
  }

  async beforeNavigation(serviceId) {
    const used = this.used(serviceId);
    if (used >= this.perDay) {
      throw new LimitError(`${serviceId}: ${this.perDay} automated page loads already used today`);
    }
    const wait = (this.last.get(serviceId) || 0) + this.minGapMs - this.now();
    if (wait > 0) await this.sleep(wait);
    this.last.set(serviceId, this.now());
    this.usage.counts[serviceId] = used + 1;
    this._save();
  }
}

module.exports = { Limiter, LimitError };
