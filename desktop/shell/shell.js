'use strict';

// The shell page: service rail, toolbar, and the inbox where captured items wait
// for you to look at them. Captured text comes from third-party pages, so it is
// only ever written with textContent / .value, never as HTML.

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};

const LEVEL_LABEL = { view: 'view only', capture: 'capture on request', batch: 'scan allowed' };

let state;
let active = null;
let inbox = [];
const lastNav = {};

function setStatus(message, kind = '') {
  $('status').textContent = message;
  $('status').className = kind;
}

const cleanError = (err) => String(err.message || err).replace(/^Error invoking remote method '[^']+': (Error: )?/, '');

async function run(task, busyMessage) {
  for (const id of ['capture', 'scan', 'send']) $(id).disabled = true;
  setStatus(busyMessage);
  try {
    await task();
  } catch (err) {
    setStatus(cleanError(err), 'error');
  } finally {
    updateToolbar();
    $('send').disabled = false;
  }
}

// ---------------------------------------------------------------- rail + stage

// The mark for a service, from icons.js. Built as SVG elements rather than an <img> so it takes
// the button's text colour (the page's CSP allows no images), or null when there is none.
function mark(id) {
  if (!ICONS[id]) return null;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[id]);
  svg.append(path);
  return svg;
}

function renderRail() {
  const buttons = state.services.map((svc) => {
    const button = el('button', { title: `${svc.name} · ${LEVEL_LABEL[svc.level]}` });
    button.setAttribute('aria-label', svc.name);
    // A service without a mark (one added in services.json) shows the letters of its glyph.
    const icon = mark(svc.id);
    if (icon) button.append(icon);
    else button.textContent = svc.glyph;
    button.dataset.level = svc.level;
    button.classList.toggle('active', Boolean(active) && active.id === svc.id);
    button.addEventListener('click', () => open(svc.id));
    return button;
  });
  const settings = el('button', { title: 'Settings' });
  settings.setAttribute('aria-label', 'Settings');
  const gear = mark('settings');
  if (gear) settings.append(gear);
  else settings.textContent = '⚙';
  settings.addEventListener('click', openSettings);
  $('rail').replaceChildren(...buttons, el('div', { className: 'spacer' }), settings);
}

function updateToolbar() {
  if (!active) return;
  const usable = Boolean(active.url);
  $('capture').disabled = active.level === 'view' || !usable;
  $('scan').hidden = !(active.level === 'batch' && active.searches.length);
  $('scan').disabled = !usable;
  const label = LEVEL_LABEL[active.level];
  $('level').textContent = active.level === 'batch' ? `${label} · ${active.used}/${state.perDay} loads today` : label;
}

function renderNav(info) {
  $('address').value = info.url || '';
  $('back').disabled = !info.canGoBack;
  $('forward').disabled = !info.canGoForward;
}

function renderEmpty() {
  const box = $('empty');
  if (!active || active.url) {
    box.replaceChildren();
    return;
  }
  if (active.id === 'hole') {
    box.replaceChildren(el('div', { textContent: 'Set the address of your Hole server in Settings.' }));
    return;
  }
  const input = el('input', { type: 'text', placeholder: `Address for ${active.name}`, spellcheck: false });
  const form = el('form', {}, input, el('button', { type: 'submit', className: 'primary', textContent: 'Open' }));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      state = await hole.setServiceUrl(active.id, /^https?:\/\//i.test(input.value) ? input.value : `https://${input.value}`);
      await open(active.id);
    } catch (err) {
      setStatus(cleanError(err), 'error');
    }
  });
  box.replaceChildren(el('div', { textContent: `${active.name} has no address yet.` }), form);
}

async function open(id) {
  const { service, url } = await hole.show(id);
  active = state.services.find((s) => s.id === id) || service;
  renderRail();
  updateToolbar();
  renderEmpty();
  renderNav(lastNav[id] || { url, canGoBack: false, canGoForward: false });
  pushBounds();
}

function pushBounds() {
  const box = $('stage').getBoundingClientRect();
  hole.bounds({ x: box.left, y: box.top, width: box.width, height: box.height });
}

// ---------------------------------------------------------------- inbox

function strip(item) {
  const { selected, ...rest } = item;
  return rest;
}

function isComplete(item) {
  return Boolean(item.title.trim()) && (item.kind !== 'job' || Boolean(item.company.trim()));
}

function addToInbox(found) {
  const known = new Set(inbox.map((i) => i.url).filter(Boolean));
  let added = 0;
  for (const raw of found) {
    if (raw.url && known.has(raw.url)) continue;
    const item = {
      ...raw,
      kind: raw.kind === 'competition' ? 'competition' : 'job',
      title: raw.title || '',
      company: raw.company || '',
      location: raw.location || '',
      deadline: raw.deadline || '',
    };
    item.selected = isComplete(item);
    inbox.push(item);
    added += 1;
  }
  renderInbox();
  $('inbox').hidden = false;
  setStatus(found.length ? `Found ${found.length}, ${added} new to this list` : 'Nothing recognised on this page', found.length ? 'ok' : '');
}

function field(item, key, placeholder) {
  const input = el('input', { type: 'text', value: item[key], placeholder });
  input.addEventListener('input', () => {
    item[key] = input.value;
  });
  return input;
}

function renderInbox() {
  $('inbox-count').textContent = String(inbox.length);
  if (!inbox.length) {
    $('items').replaceChildren(el('li', { className: 'empty', textContent: 'Nothing captured yet. Open a service and press Capture page.' }));
    return;
  }
  $('items').replaceChildren(...inbox.map((item) => {
    const tick = el('input', { type: 'checkbox', checked: item.selected, title: 'Send this one' });
    tick.addEventListener('change', () => {
      item.selected = tick.checked;
    });
    const kind = el('select', {}, el('option', { value: 'job', textContent: 'Job' }), el('option', { value: 'competition', textContent: 'Competition' }));
    kind.value = item.kind;
    kind.addEventListener('change', () => {
      item.kind = kind.value;
      renderInbox();
    });
    const details = item.kind === 'job'
      ? el('div', { className: 'row' }, field(item, 'company', 'Company'), field(item, 'location', 'Location'))
      : el('div', { className: 'row' }, field(item, 'deadline', 'Deadline'));
    return el('li', {},
      el('div', { className: 'row' }, tick, field(item, 'title', 'Title'), kind),
      details,
      el('div', { className: 'url', title: item.url || '', textContent: `${item.service} · ${item.url || ''}` }));
  }));
}

// ---------------------------------------------------------------- settings

function openSettings() {
  $('hole-url').value = state.holeUrl;
  $('token').value = '';
  $('token').placeholder = state.hasToken ? 'Saved — type to replace' : 'Paste the token';
  $('settings-error').textContent = '';
  hole.overlay(true); // the native page view would otherwise cover the dialog
  $('settings').showModal();
}

$('settings').addEventListener('close', () => hole.overlay(false));
$('settings-cancel').addEventListener('click', () => $('settings').close());
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    state = await hole.saveSettings({ holeUrl: $('hole-url').value.trim(), token: $('token').value.trim() });
    active = state.services.find((s) => s.id === (active && active.id)) || active;
    renderRail();
    updateToolbar();
    $('settings').close();
  } catch (err) {
    $('settings-error').textContent = cleanError(err);
  }
});

// ---------------------------------------------------------------- pipeline

// The server runs the pipeline (daily at 12:00 GMT, and when asked). The toolbar shows how it
// stands and can ask for a run now; asking again while one is going is harmless.

let pollTimer = null;

function showPipeline(text, kind = '') {
  $('pipeline').textContent = text;
  $('pipeline').title = text;
  $('pipeline').className = `pipeline ${kind}`.trim();
}

async function refreshPipeline() {
  let running = false;
  try {
    const result = await hole.runStatus();
    running = result.running;
    showPipeline(result.text, running ? 'running' : '');
  } catch (err) {
    showPipeline(cleanError(err), 'error');
  }
  clearTimeout(pollTimer);
  pollTimer = setTimeout(refreshPipeline, running ? 5000 : 30000);
}

$('update').addEventListener('click', async () => {
  $('update').disabled = true;
  try {
    const result = await hole.runStart();
    showPipeline(result.started ? 'Started on the server; this takes a few minutes' : 'A run is already going', 'running');
    setTimeout(refreshPipeline, 4000);
  } catch (err) {
    showPipeline(cleanError(err), 'error');
  } finally {
    $('update').disabled = false;
  }
});

// ---------------------------------------------------------------- wiring

$('back').addEventListener('click', () => hole.nav('back'));
$('forward').addEventListener('click', () => hole.nav('forward'));
$('reload').addEventListener('click', () => hole.nav('reload'));
$('address-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = $('address').value.trim();
  if (value) hole.nav('go', /^https?:\/\//i.test(value) ? value : `https://${value}`);
});
$('toggle-inbox').addEventListener('click', () => {
  $('inbox').hidden = !$('inbox').hidden;
});
$('capture').addEventListener('click', () => run(async () => addToInbox(await hole.capture()), 'Reading the page…'));
$('scan').addEventListener('click', () => run(async () => {
  addToInbox(await hole.scan());
  state = await hole.state();
  active = state.services.find((s) => s.id === active.id);
}, 'Scanning saved searches…'));
$('clear').addEventListener('click', () => {
  inbox = [];
  renderInbox();
  setStatus('');
});
$('send').addEventListener('click', () => {
  const chosen = inbox.filter((i) => i.selected);
  if (!chosen.length) return setStatus('Tick at least one item first', 'error');
  if (!chosen.every(isComplete)) return setStatus('Jobs need a title and a company', 'error');
  return run(async () => {
    const result = await hole.send(chosen.map(strip));
    inbox = inbox.filter((i) => !i.selected);
    renderInbox();
    const fresh = result.jobs.new + result.competitions.new;
    const seen = result.jobs.duplicate + result.competitions.duplicate;
    setStatus(`Sent: ${fresh} new, ${seen} already in Hole${result.rejected ? `, ${result.rejected} rejected` : ''}`, 'ok');
  }, 'Sending…');
});

hole.onNav((info) => {
  lastNav[info.serviceId] = info;
  if (active && info.serviceId === active.id) renderNav(info);
});
new ResizeObserver(pushBounds).observe($('stage'));
window.addEventListener('resize', pushBounds);

(async () => {
  state = await hole.state();
  renderInbox();
  renderRail();
  await open('hole');
  refreshPipeline();
})();
