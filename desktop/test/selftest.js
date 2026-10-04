'use strict';

// `npm run selftest` -- runs inside Electron, against a real embedded page.
//
// This is the proof of the premise: the app creates a browser view and drives it
// (navigate, read, click, type, screenshot) with no extension, no debugging
// port and no permission prompt, then runs the extractors over fixture pages in
// real Chromium, where innerText and layout behave as they do on a live site.
// With HOLE_E2E_URL and HOLE_E2E_TOKEN set it also posts what it captured to a
// running Hole server (see tests/test_desktop_e2e.py).

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { WebContentsView, session } = require('electron');

const { SessionKeeper } = require('../src/cookies');
const { Driver } = require('../src/driver');
const { EXTRACTORS, extract } = require('../src/extractors');
const { sendItems } = require('../src/ingest');
const { Limiter, LimitError } = require('../src/limits');
const { resolveServices } = require('../src/services');

const FIXTURES = path.join(__dirname, 'fixtures');

const INTERACT = `<!doctype html><title>interact</title>
<script>window.leak = 'page global';</script>
<input id="q" placeholder="search">
<button id="btn">Click me</button>
<div id="out"></div><div id="entered"></div>
<script>
  let clicks = 0;
  btn.addEventListener('click', (e) => { clicks += 1; out.textContent = 'clicked ' + clicks + ' trusted=' + e.isTrusted; });
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') entered.textContent = q.value + ' trusted=' + e.isTrusted; });
</script>`;

function startServer() {
  const server = http.createServer((req, res) => {
    const name = req.url.split('?')[0].slice(1);
    if (name === 'interact') {
      res.writeHead(200, { 'Content-Type': 'text/html' }).end(INTERACT);
      return;
    }
    const file = path.join(FIXTURES, path.basename(name));
    if (!name || !fs.existsSync(file)) {
      res.writeHead(404).end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html' }).end(fs.readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

// Load the real shell page with the real preload, answering the few channels it asks about at
// start-up, and describe what the rail drew. What this shows that icons.test.js cannot: each
// path parses (an invalid one measures 0 x 0) and is painted in the button's own colour.
async function drawnRail({ BrowserWindow, ipcMain }) {
  const services = resolveServices({}, 'http://127.0.0.1:9');
  const handlers = {
    'app:state': () => ({ holeUrl: 'http://127.0.0.1:9', hasToken: false, perDay: 100, services: services.map((s) => ({ ...s, used: 0 })) }),
    'stage:show': (_event, id) => ({ service: services.find((s) => s.id === id), url: '' }),
    'pipeline:status': () => ({ text: 'selftest', running: false }),
  };
  for (const [channel, handler] of Object.entries(handlers)) ipcMain.handle(channel, handler);
  const shell = new BrowserWindow({
    width: 900, height: 700, x: -3000, y: -3000, skipTaskbar: true,
    webPreferences: { preload: path.join(__dirname, '..', 'src', 'preload.js'), contextIsolation: true, sandbox: true },
  });
  try {
    await shell.loadFile(path.join(__dirname, '..', 'shell', 'index.html'));
    const read = () => shell.webContents.executeJavaScript(`(() => {
      const buttons = [...document.querySelectorAll('#rail button')];
      return buttons.map((button) => {
        const path = button.querySelector('svg path');
        const box = path ? path.getBBox() : null;
        return {
          label: button.getAttribute('aria-label'),
          marked: Boolean(path),
          box: box && { x: box.x, y: box.y, width: box.width, height: box.height },
          fill: path ? getComputedStyle(path).fill : null,
          colour: getComputedStyle(button).color,
          text: button.textContent,
        };
      });
    })()`);
    for (let i = 0; i < 50; i += 1) {
      const rail = await read();
      if (rail.length) return { services, rail };
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('the shell never drew a rail');
  } finally {
    shell.destroy();
    for (const channel of Object.keys(handlers)) ipcMain.removeHandler(channel);
  }
}

async function run({ BrowserWindow, ipcMain, limiter }) {
  const server = await startServer();
  const base = `http://127.0.0.1:${server.address().port}`;
  // A hidden (or near-transparent) window gets no input and has no surface to
  // capture, so the test window is a real, visible one parked off-screen.
  const win = new BrowserWindow({ width: 1000, height: 800, x: -3000, y: -3000, skipTaskbar: true });
  const view = new WebContentsView({ webPreferences: { partition: 'selftest', sandbox: true, contextIsolation: true } });
  win.contentView.addChildView(view);
  view.setBounds({ x: 0, y: 0, width: 1000, height: 800 });
  const driver = new Driver(view.webContents, { serviceId: 'selftest', limiter: new Limiter({ minGapMs: 0, perDay: 1000 }) });

  const results = [];
  const check = async (name, fn) => {
    try {
      await fn();
      results.push({ name, ok: true });
      console.log(`ok - ${name}`);
    } catch (err) {
      results.push({ name, ok: false, detail: err.message });
      console.log(`not ok - ${name}\n    ${String(err.message).split('\n').join('\n    ')}`);
    }
  };
  const titles = (items) => items.map((i) => i.title);

  await check('drives a page: click and typing arrive as trusted input', async () => {
    await driver.goto(`${base}/interact`);
    await driver.waitFor('#btn', { visible: true });
    await driver.click('#btn');
    assert.equal(await driver.text('#out'), 'clicked 1 trusted=true');
    await driver.type('#q', 'hello');
    driver.press('Enter');
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(await driver.text('#entered'), 'hello trusted=true');
  });

  await check("injected code runs in an isolated world the page's scripts cannot see", async () => {
    assert.equal(await driver.evaluate(() => typeof window.leak), 'undefined');
    assert.equal(await view.webContents.executeJavaScript('window.leak'), 'page global');
  });

  await check('screenshots the embedded page', async () => {
    const png = await driver.screenshot();
    assert.ok(png.length > 500, `only ${png.length} bytes`);
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
  });

  // Electron forgets a cookie with no expiry when the app closes; the keeper stores it again with one.
  // One process cannot restart itself, so this shows the cookie Chromium would write to disk: no
  // longer a session cookie, with its flags and value intact.
  await check('a session cookie is stored again with an expiry, so a sign-in outlives the app', async () => {
    const ses = session.fromPartition('persist:selftest-sessions');
    const keeper = new SessionKeeper(ses, { settleMs: 60_000 }); // stored by hand below
    const soon = Math.floor(Date.now() / 1000) + 3600;
    await ses.cookies.set({ url: 'https://login.example/', name: 'sid', value: 'abc', secure: true, httpOnly: true, sameSite: 'lax' });
    await ses.cookies.set({ url: 'https://login.example/', name: 'dated', value: 'x', expirationDate: soon });
    // the cookie store reports a change a moment after set() resolves
    for (let i = 0; i < 50 && !keeper.waiting.size; i += 1) await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(keeper.waiting.size, 1, 'only the cookie with no expiry should be waiting');
    await keeper.flush();
    const [kept] = await ses.cookies.get({ name: 'sid' });
    assert.equal(kept.session, false);
    assert.ok(kept.expirationDate > Date.now() / 1000 + 29 * 24 * 3600, `expires at ${kept.expirationDate}`);
    assert.deepEqual([kept.value, kept.secure, kept.httpOnly, kept.sameSite], ['abc', true, true, 'lax']);
    const [dated] = await ses.cookies.get({ name: 'dated' });
    assert.equal(Math.round(dated.expirationDate), soon, 'a cookie that already expires is left as it was');
    await ses.clearStorageData();
  });

  await check('jsonld: JobPosting and Event, tolerating a broken block', async () => {
    await driver.goto(`${base}/jobposting.html`);
    const items = await driver.evaluate(EXTRACTORS.jsonld);
    assert.deepEqual(titles(items), ['Data Science Intern', 'Backend Intern', 'Spring Datathon']);
    assert.equal(items[0].company, 'Acme Analytics');
    assert.equal(items[0].location, 'Pune, MH, IN');
    assert.match(items[0].description, /forecasting/);
    assert.doesNotMatch(items[0].description, /<b>/);
    assert.equal(items[1].location, 'Remote');
    assert.equal(items[2].kind, 'competition');
    assert.equal(items[2].deadline, '2026-11-15');
  });

  await check('linkedin: one item per posting id, with company and location', async () => {
    await driver.goto(`${base}/linkedin-list.html`);
    const items = await driver.evaluate(EXTRACTORS.linkedin);
    assert.deepEqual(items.map((i) => i.url), [
      'https://www.linkedin.com/jobs/view/4012345678/',
      'https://www.linkedin.com/jobs/view/4098765432/',
    ]);
    assert.deepEqual([items[0].title, items[0].company, items[0].location], ['Data Intern', 'Acme Analytics', 'Bengaluru, Karnataka, India (Hybrid)']);
    assert.deepEqual([items[1].title, items[1].company, items[1].location], ['Software Engineering Intern', 'Beta Labs', 'Remote']);
  });

  await check('indeed: title link, company and location from either markup variant', async () => {
    await driver.goto(`${base}/indeed-list.html`);
    const items = await driver.evaluate(EXTRACTORS.indeed);
    assert.equal(items.length, 2);
    assert.deepEqual([items[0].title, items[0].company, items[0].location], ['Machine Learning Intern', 'Gamma Corp', 'Remote']);
    assert.deepEqual([items[1].title, items[1].company, items[1].location], ['Analyst Intern', 'Delta Capital', 'Mumbai, MH']);
    assert.equal(items[0].url, `${base}/viewjob?jk=a1b2c3d4e5f60718`);
  });

  await check('links: competition-looking links only, deduplicated, absolute only', async () => {
    await driver.goto(`${base}/links.html`);
    const items = await driver.evaluate(EXTRACTORS.links);
    assert.deepEqual(titles(items), ['Global AI Hack 2026 is open for registration', 'Case study competition for finalists']);
  });

  await check('extract() merges a service\'s extractors and falls back to the page', async () => {
    await driver.goto(`${base}/jobposting.html`);
    assert.equal((await extract(driver, ['linkedin', 'jsonld'])).length, 3);
    await driver.goto(`${base}/interact`);
    const [fallback] = await extract(driver, ['linkedin', 'jsonld']);
    assert.equal(fallback.kind, 'page');
    assert.equal(fallback.title, 'interact');
  });

  // The phone runs android/app/src/main/assets/extractors.js in the page's own world, wrapped as
  // Capture.script() in Capture.kt wraps it. Here the same text runs in real Chromium, and has to
  // find what the desktop extractors find on the same pages.
  await check('the Android capture script finds what the desktop extractors find', async () => {
    const library = fs.readFileSync(path.join(__dirname, '..', '..', 'android', 'app', 'src', 'main', 'assets', 'extractors.js'), 'utf8');
    const script = (names) => `(function(){try{${library}\nreturn JSON.stringify(holeExtract(${JSON.stringify(names)}));}`
      + 'catch(e){return JSON.stringify({error:String((e&&e.message)||e)});}})()';
    for (const [page, names] of [
      ['jobposting.html', ['linkedin', 'jsonld']],
      ['linkedin-list.html', ['linkedin', 'jsonld']],
      ['indeed-list.html', ['indeed', 'jsonld']],
      ['links.html', ['links']],
      ['interact', ['linkedin', 'jsonld']], // nothing recognised: both fall back to the page itself
    ]) {
      await driver.goto(`${base}/${page}`);
      const onPhone = JSON.parse(await view.webContents.executeJavaScript(script(names)));
      assert.ok(Array.isArray(onPhone), `${page}: ${JSON.stringify(onPhone)}`);
      assert.ok(onPhone.length > 0, `${page}: found nothing`);
      assert.deepEqual(onPhone, await extract(driver, names), page);
    }
  });

  await check('the rail draws a mark for every service, in the button\'s own colour', async () => {
    const { services, rail } = await drawnRail({ BrowserWindow, ipcMain });
    assert.deepEqual(rail.map((b) => b.label), [...services.map((s) => s.name), 'Settings']);
    for (const button of rail) {
      assert.ok(button.marked, `${button.label} has no mark`);
      assert.equal(button.text, '', `${button.label} still shows letters`);
      const { x, y, width, height } = button.box;
      assert.ok(width > 4 && height > 4, `${button.label}'s path measures ${width} x ${height}`);
      assert.ok(x >= -0.5 && y >= -0.5 && x + width <= 24.5 && y + height <= 24.5, `${button.label} leaves its 24 x 24 box`);
      assert.equal(button.fill, button.colour, `${button.label} is not painted in the button's colour`);
    }
  });

  await check('the load budget stops automated navigation', async () => {
    const capped = new Driver(view.webContents, { serviceId: 'capped', limiter: new Limiter({ minGapMs: 0, perDay: 2 }) });
    await capped.goto(`${base}/links.html`);
    await capped.goto(`${base}/links.html`);
    await assert.rejects(capped.goto(`${base}/links.html`), LimitError);
  });

  if (process.env.HOLE_E2E_URL && process.env.HOLE_E2E_TOKEN) {
    await check('posts captured items to a running Hole server', async () => {
      const items = [];
      for (const [file, names] of [['linkedin-list.html', ['linkedin']], ['jobposting.html', ['jsonld']], ['links.html', ['links']]]) {
        await driver.goto(`${base}/${file}`);
        items.push(...(await extract(driver, names)));
      }
      const result = await sendItems({
        holeUrl: process.env.HOLE_E2E_URL,
        token: process.env.HOLE_E2E_TOKEN,
        items,
        source: 'selftest',
      });
      console.log(`RESULT ${JSON.stringify(result)}`);
      assert.equal(result.jobs.new, 4);
      assert.equal(result.competitions.new, 3);
    });
  }

  server.close();
  win.destroy();
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  return failed ? 1 : 0;
}

module.exports = { run };
