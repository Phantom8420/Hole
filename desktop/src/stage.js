'use strict';

const { WebContentsView, shell } = require('electron');
const { Driver } = require('./driver');

// One WebContentsView per service, each with its own persistent partition so a
// LinkedIn login never shares cookies with Discord or Hole. Views are created on
// first use and kept, so switching back does not reload or log you out.

const ALLOWED_PERMISSIONS = new Set(['clipboard-sanitized-write', 'fullscreen']);

class Stage {
  constructor(window, { limiter, onNav }) {
    this.window = window;
    this.limiter = limiter;
    this.onNav = onNav;
    this.views = new Map();
    this.activeId = null;
    this.bounds = { x: 0, y: 0, width: 0, height: 0 };
    this.hidden = false;
  }

  _create(service) {
    const view = new WebContentsView({
      webPreferences: {
        partition: `persist:svc-${service.id}`,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
      },
    });
    const wc = view.webContents;
    wc.session.setPermissionRequestHandler((_wc, permission, callback) => callback(ALLOWED_PERMISSIONS.has(permission)));
    wc.setBackgroundThrottling(false);

    // Sign-in popups (Google, Apple, SSO) need a real child window with the
    // opener intact; everything else that wants a new window is sent to the
    // system browser instead of opening one inside the app.
    wc.setWindowOpenHandler(({ url }) => {
      if (!/^https?:/i.test(url)) return { action: 'deny' };
      if (service.id === 'hole') {
        shell.openExternal(url);
        return { action: 'deny' };
      }
      return { action: 'allow' };
    });

    const report = () => this.onNav({
      serviceId: service.id,
      url: wc.getURL(),
      title: wc.getTitle(),
      loading: wc.isLoading(),
      canGoBack: wc.navigationHistory.canGoBack(),
      canGoForward: wc.navigationHistory.canGoForward(),
    });
    for (const event of ['did-navigate', 'did-navigate-in-page', 'page-title-updated', 'did-start-loading', 'did-stop-loading']) {
      wc.on(event, report);
    }
    const entry = { view, service, driver: new Driver(wc, { serviceId: service.id, limiter: this.limiter }) };
    this.views.set(service.id, entry);
    if (service.url) wc.loadURL(service.url).catch(() => {});
    return entry;
  }

  ensure(service) {
    return this.views.get(service.id) || this._create(service);
  }

  // Returns null for a service with no address yet: nothing is attached, so the
  // shell's own "set an address" prompt stays visible instead of a blank view.
  show(service) {
    if (this.activeId && this.activeId !== service.id) this._detach(this.activeId);
    if (!service.url) {
      this.activeId = null;
      return null;
    }
    const next = this.ensure(service);
    this.activeId = service.id;
    this.window.contentView.addChildView(next.view); // re-adding moves it to the front
    this._place();
    return next;
  }

  _detach(id) {
    const entry = this.views.get(id);
    if (entry) this.window.contentView.removeChildView(entry.view);
  }

  active() {
    return this.activeId ? this.views.get(this.activeId) : null;
  }

  setBounds(rect) {
    this.bounds = {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      width: Math.max(0, Math.round(rect.width)),
      height: Math.max(0, Math.round(rect.height)),
    };
    this._place();
  }

  // Native views paint above the shell's HTML, so a modal has to hide the view.
  setOverlay(on) {
    this.hidden = Boolean(on);
    this._place();
  }

  _place() {
    const entry = this.active();
    if (!entry) return;
    entry.view.setBounds(this.hidden ? { x: 0, y: 0, width: 0, height: 0 } : this.bounds);
  }

  nav(action, arg) {
    const entry = this.active();
    if (!entry) return;
    const wc = entry.view.webContents;
    if (action === 'back') wc.navigationHistory.goBack();
    else if (action === 'forward') wc.navigationHistory.goForward();
    else if (action === 'reload') wc.reload();
    else if (action === 'go' && /^https?:\/\//i.test(arg)) wc.loadURL(arg).catch(() => {});
  }
}

module.exports = { Stage };
