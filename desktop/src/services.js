'use strict';

// What the shell can open, and how much the app may do on each service.
//
//   view     the page is only displayed; the app never runs script in it
//   capture  "Capture page" reads the page you are looking at, when you press it
//   batch    the app may also visit the service's saved `searches` by itself
//            ("Scan"), within the load budget in limits.js
//
// Defaults are the ones the rest of Hole already follows: LinkedIn and Indeed
// forbid automated access and Discord bans automating a user account, so none of
// them gets more than `capture`. Raising a level is a per-service edit in
// services.json, on purpose, so it is something you did rather than something
// the app did.

const LEVELS = ['view', 'capture', 'batch'];

const BUILTIN = [
  { id: 'hole', name: 'Hole', glyph: 'H', url: null, level: 'view', extractors: [] },
  { id: 'linkedin', name: 'LinkedIn', glyph: 'in', url: 'https://www.linkedin.com/jobs/', level: 'capture', extractors: ['linkedin', 'jsonld'] },
  { id: 'indeed', name: 'Indeed', glyph: 'Id', url: 'https://www.indeed.com/', level: 'capture', extractors: ['indeed', 'jsonld'] },
  { id: 'discord', name: 'Discord', glyph: 'Dc', url: 'https://discord.com/app', level: 'capture', extractors: ['links'] },
  { id: 'proofr', name: 'Proofr', glyph: 'Pr', url: null, level: 'capture', extractors: ['jsonld', 'links'] },
  { id: 'unstop', name: 'Unstop', glyph: 'Un', url: 'https://unstop.com/hackathons', level: 'capture', extractors: ['jsonld', 'links'] },
  { id: 'devfolio', name: 'Devfolio', glyph: 'Df', url: 'https://devfolio.co/hackathons/upcoming', level: 'capture', extractors: ['jsonld', 'links'] },
  { id: 'devpost', name: 'Devpost', glyph: 'Dp', url: 'https://devpost.com/hackathons', level: 'capture', extractors: ['links'] },
  { id: 'mlh', name: 'MLH', glyph: 'M', url: 'https://mlh.com/events', level: 'capture', extractors: ['jsonld', 'links'] },
];

function webUrl(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function sameHost(a, b) {
  try {
    return new URL(a).hostname === new URL(b).hostname;
  } catch {
    return false;
  }
}

// `overrides` is the parsed services.json:
//   { "linkedin": { "level": "view" },
//     "proofr":   { "url": "https://..." },
//     "devpost":  { "level": "batch", "searches": ["https://devpost.com/hackathons?status[]=open"] },
//     "custom":   [ { "id": "x", "name": "X", "url": "https://x.example", "level": "capture" } ] }
// Anything malformed is ignored rather than trusted: a bad entry should not be
// able to change where the app navigates on its own.
function resolveServices(overrides = {}, holeUrl = null) {
  const custom = Array.isArray(overrides.custom) ? overrides.custom : [];
  const merged = [...BUILTIN.map((s) => ({ ...s })), ...custom.map((c) => ({ glyph: '·', extractors: ['jsonld', 'links'], ...c }))];
  const seen = new Set();
  const out = [];
  for (const svc of merged) {
    if (!svc || typeof svc.id !== 'string' || !/^[a-z0-9_-]{1,32}$/.test(svc.id) || seen.has(svc.id)) continue;
    seen.add(svc.id);
    const o = (BUILTIN.some((b) => b.id === svc.id) && overrides[svc.id]) || {};
    const url = svc.id === 'hole' ? webUrl(holeUrl) : webUrl(o.url ?? svc.url);
    const level = LEVELS.includes(o.level) ? o.level : LEVELS.includes(svc.level) ? svc.level : 'view';
    const searches = (Array.isArray(o.searches) ? o.searches : Array.isArray(svc.searches) ? svc.searches : [])
      .map(webUrl)
      .filter((u) => u && url && sameHost(u, url));
    out.push({
      id: svc.id,
      name: String(svc.name || svc.id).slice(0, 40),
      glyph: String(svc.glyph || svc.name || svc.id).slice(0, 2),
      url,
      level,
      extractors: Array.isArray(svc.extractors) ? svc.extractors : [],
      searches: level === 'batch' ? searches : [],
    });
  }
  return out;
}

module.exports = { LEVELS, BUILTIN, resolveServices, webUrl };
