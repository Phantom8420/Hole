'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { app, BrowserWindow, ipcMain, safeStorage } = require('electron');

const { sleep } = require('./driver');
const { loadEnvFile } = require('./envfile');
const { dedupe, extract } = require('./extractors');
const { sendItems, tokenStore } = require('./ingest');
const { describe, fetchStatus, startRun } = require('./pipeline');
const { Limiter } = require('./limits');
const { resolveServices, sectionsOf, webUrl } = require('./services');
const { Stage } = require('./stage');

const SELF_TEST = process.argv.includes('--self-test');
const DEFAULT_HOLE_URL = 'http://127.0.0.1:8765';

if (!SELF_TEST) loadEnvFile(path.join(__dirname, '..', '.env.local'));

// Keep rendering and input working when the window is covered or minimised --
// a scan should not stall because something was dragged in front of it.
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion');
app.setName('Hole Desktop');
if (SELF_TEST) app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'hole-selftest-')));
else if (process.env.HOLE_USER_DATA) app.setPath('userData', process.env.HOLE_USER_DATA); // a throwaway profile for testing
if (!SELF_TEST && !app.requestSingleInstanceLock()) app.quit();

const userFile = (name) => path.join(app.getPath('userData'), name);

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

const settings = {
  load: () => ({ holeUrl: process.env.HOLE_URL || DEFAULT_HOLE_URL, ...readJson(userFile('settings.json'), {}) }),
  save: (next) => fs.writeFileSync(userFile('settings.json'), JSON.stringify(next, null, 2)),
};
// services.json is edited by hand (levels, saved searches, extra services); the
// shell only ever writes the one thing it asks for, a service's address.
const serviceOverrides = {
  load: () => readJson(userFile('services.json'), {}),
  save: (next) => fs.writeFileSync(userFile('services.json'), JSON.stringify(next, null, 2)),
};

let win;
let stage;
let limiter;
let tokens;

const services = () => resolveServices(serviceOverrides.load(), settings.load().holeUrl);

function appState() {
  const list = services();
  return {
    holeUrl: settings.load().holeUrl,
    hasToken: tokens.present(),
    perDay: limiter.perDay,
    services: list.map((s) => ({ ...s, used: limiter.used(s.id) })),
    sections: sectionsOf(list),
  };
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#14110f',
    title: 'Hole',
    icon: path.join(__dirname, '..', 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.loadFile(path.join(__dirname, '..', 'shell', 'index.html'));

  stage = new Stage(win, {
    limiter,
    onNav: (info) => {
      if (!win.isDestroyed()) win.webContents.send('nav:event', info);
    },
  });
}

function registerIpc() {
  const guarded = (handler) => (event, ...args) => {
    if (!win || event.sender !== win.webContents) throw new Error('untrusted sender');
    return handler(...args);
  };

  ipcMain.handle('app:state', guarded(appState));

  ipcMain.handle('stage:show', guarded((id) => {
    const service = services().find((s) => s.id === id);
    if (!service) throw new Error(`unknown service ${id}`);
    const entry = stage.show(service);
    return { service, url: entry ? entry.view.webContents.getURL() : '' };
  }));

  ipcMain.on('stage:bounds', (event, rect) => {
    if (win && event.sender === win.webContents) stage.setBounds(rect);
  });
  ipcMain.on('stage:overlay', (event, on) => {
    if (win && event.sender === win.webContents) stage.setOverlay(on);
  });

  ipcMain.handle('nav', guarded((action, arg) => stage.nav(action, arg)));

  const tag = (items, service) => items.map((item) => ({ ...item, id: crypto.randomUUID(), service: service.id }));

  ipcMain.handle('capture:run', guarded(async () => {
    const entry = stage.active();
    if (!entry) throw new Error('Open a service first');
    if (entry.service.level === 'view') {
      throw new Error(`${entry.service.name} is view-only. Set its level to "capture" in services.json to read pages.`);
    }
    return tag(await extract(entry.driver, entry.service.extractors), entry.service);
  }));

  ipcMain.handle('scan:run', guarded(async () => {
    const entry = stage.active();
    if (!entry || entry.service.level !== 'batch' || !entry.service.searches.length) {
      throw new Error('This service has no saved searches to scan');
    }
    const found = [];
    for (const url of entry.service.searches) {
      await entry.driver.goto(url);
      await sleep(2500); // listings render client-side after load
      found.push(...(await extract(entry.driver, entry.service.extractors)));
    }
    return tag(dedupe(found), entry.service);
  }));

  ipcMain.handle('ingest:send', guarded(async (items) => {
    const { holeUrl } = settings.load();
    const token = tokens.load();
    const bySource = new Map();
    for (const item of items) bySource.set(item.service || 'desktop', [...(bySource.get(item.service || 'desktop') || []), item]);
    const total = { jobs: { new: 0, duplicate: 0 }, competitions: { new: 0, duplicate: 0 }, rejected: 0 };
    for (const [source, group] of bySource) {
      const result = await sendItems({ holeUrl, token, items: group, source });
      for (const kind of ['jobs', 'competitions']) {
        total[kind].new += result[kind].new;
        total[kind].duplicate += result[kind].duplicate;
      }
      total.rejected += result.rejected;
    }
    return total;
  }));

  // The pipeline runs on the server; these only ask it to start and how it stands.
  const serverOptions = () => ({ holeUrl: settings.load().holeUrl, token: tokens.load() });
  ipcMain.handle('pipeline:start', guarded(() => startRun(serverOptions())));
  ipcMain.handle('pipeline:status', guarded(async () => {
    const status = await fetchStatus(serverOptions());
    return { ...describe(status), status };
  }));

  ipcMain.handle('settings:save', guarded(({ holeUrl, token }) => {
    const url = webUrl(holeUrl);
    if (!url) throw new Error('The Hole address must start with http:// or https://');
    settings.save({ ...settings.load(), holeUrl: new URL(url).origin });
    if (token) tokens.save(token);
    // Hole's view keeps whatever address it was created with; reload it there.
    const hole = stage.views.get('hole');
    if (hole) hole.view.webContents.loadURL(new URL(url).origin).catch(() => {});
    return appState();
  }));

  ipcMain.handle('services:setUrl', guarded((id, url) => {
    const clean = webUrl(url);
    if (!clean) throw new Error('That is not a web address');
    const next = serviceOverrides.load();
    next[id] = { ...(next[id] || {}), url: clean };
    serviceOverrides.save(next);
    return appState();
  }));
}

app.whenReady().then(async () => {
  limiter = new Limiter({ file: userFile('usage.json') });
  tokens = tokenStore(app.getPath('userData'), safeStorage);
  if (SELF_TEST) {
    const code = await require('../test/selftest').run({ BrowserWindow, ipcMain, limiter }).catch((err) => {
      console.error(err);
      return 1;
    });
    app.exit(code);
    return;
  }
  registerIpc();
  createWindow();
});

app.on('window-all-closed', () => app.quit());

// A sign-in made just before closing is still waiting for its expiry (src/cookies.js): store it
// first, but never let that hold the app open for more than a moment.
let leaving = false;
app.on('before-quit', (event) => {
  if (leaving || !stage) return;
  leaving = true;
  event.preventDefault();
  Promise.race([stage.keepSessions(), new Promise((resolve) => setTimeout(resolve, 3000))]).finally(() => app.quit());
});
