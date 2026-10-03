'use strict';

// Programmatic control of one embedded page. The page is a WebContents the app
// itself created, so there is no remote-debugging port to open, no extension to
// install and nothing to approve: main-process code can navigate it, read it,
// click and type in it directly.
//
// Reading runs in an isolated world (same DOM, separate JS globals), so the
// site's own scripts cannot see or tamper with what the app injects. Clicks and
// keystrokes go through sendInputEvent, i.e. as real input events, not
// element.click() calls.

const WORLD_ID = 1001; // Electron reserves 999 for contextIsolation

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class Driver {
  constructor(webContents, { serviceId = 'page', limiter = null } = {}) {
    this.wc = webContents;
    this.serviceId = serviceId;
    this.limiter = limiter;
  }

  async goto(url, { timeoutMs = 30000 } = {}) {
    if (this.limiter) await this.limiter.beforeNavigation(this.serviceId);
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`timed out loading ${url}`)), timeoutMs);
    });
    try {
      await Promise.race([this.wc.loadURL(url), timeout]);
    } catch (err) {
      // A redirect or a script navigating away aborts the first load; the page
      // that replaces it is the one we want.
      if (err.code !== 'ERR_ABORTED') throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  // Run `fn` inside the page and return its (JSON-serialisable) result.
  // `fn` is serialised with toString(), so it must not close over anything.
  async evaluate(fn, ...args) {
    const code = `(async () => {
      try {
        const out = await (${fn.toString()})(...${JSON.stringify(args)});
        return { ok: true, value: out === undefined ? null : out };
      } catch (e) {
        return { ok: false, error: String((e && e.message) || e) };
      }
    })()`;
    const result = await this.wc.executeJavaScriptInIsolatedWorld(WORLD_ID, [{ code }]);
    if (!result.ok) throw new Error(`page script failed: ${result.error}`);
    return result.value;
  }

  async waitFor(selector, { timeoutMs = 10000, visible = false } = {}) {
    const deadline = Date.now() + timeoutMs;
    const probe = (sel, mustBeVisible) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      if (!mustBeVisible) return true;
      const box = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    };
    while (Date.now() < deadline) {
      if (await this.evaluate(probe, selector, visible)) return;
      await sleep(150);
    }
    throw new Error(`timed out waiting for ${selector}`);
  }

  text(selector) {
    return this.evaluate((sel) => {
      const el = document.querySelector(sel);
      return el ? el.innerText : null;
    }, selector);
  }

  async click(selector) {
    const point = await this.evaluate((sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
      const box = el.getBoundingClientRect();
      return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2) };
    }, selector);
    if (!point) throw new Error(`no element matches ${selector}`);
    const { x, y } = point;
    this.wc.sendInputEvent({ type: 'mouseMove', x, y });
    this.wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
    this.wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
  }

  async type(selector, text) {
    await this.click(selector);
    await this.wc.insertText(text);
  }

  press(key) {
    this.wc.sendInputEvent({ type: 'keyDown', keyCode: key });
    if (key === 'Enter') this.wc.sendInputEvent({ type: 'char', keyCode: '\r' });
    this.wc.sendInputEvent({ type: 'keyUp', keyCode: key });
  }

  scrollBy(deltaY) {
    this.wc.sendInputEvent({ type: 'mouseWheel', x: 10, y: 10, deltaX: 0, deltaY: -deltaY });
  }

  async screenshot() {
    const image = await this.wc.capturePage();
    return image.toPNG();
  }

  url() {
    return this.wc.getURL();
  }

  title() {
    return this.wc.getTitle();
  }
}

module.exports = { Driver, sleep };
