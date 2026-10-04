'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { resolveServices, sectionsOf } = require('../src/services');

const byId = (list, id) => list.find((s) => s.id === id);

test('defaults keep LinkedIn, Indeed and Discord at capture', () => {
  const list = resolveServices({}, 'https://hole.example');
  for (const id of ['linkedin', 'indeed', 'discord']) assert.equal(byId(list, id).level, 'capture');
  assert.equal(byId(list, 'hole').url, 'https://hole.example/');
  assert.equal(byId(list, 'hole').level, 'view');
});

test('Proofr has no address until one is supplied', () => {
  assert.equal(byId(resolveServices({}), 'proofr').url, null);
  const list = resolveServices({ proofr: { url: 'https://proofr.example/jobs' } });
  assert.equal(byId(list, 'proofr').url, 'https://proofr.example/jobs');
});

test('levels can be raised or lowered per service; junk falls back', () => {
  const list = resolveServices({ linkedin: { level: 'view' }, indeed: { level: 'turbo' } });
  assert.equal(byId(list, 'linkedin').level, 'view');
  assert.equal(byId(list, 'indeed').level, 'capture');
});

test('saved searches only count for batch services and only on the same host', () => {
  const searches = ['https://devpost.com/hackathons?status[]=open', 'https://evil.example/steal', 'not a url'];
  const asCapture = resolveServices({ devpost: { searches } });
  assert.deepEqual(byId(asCapture, 'devpost').searches, []);
  const asBatch = resolveServices({ devpost: { level: 'batch', searches } });
  assert.deepEqual(byId(asBatch, 'devpost').searches, ['https://devpost.com/hackathons?status[]=open']);
});

test('non-web addresses are refused and custom services are validated', () => {
  const list = resolveServices({
    unstop: { url: 'javascript:alert(1)' },
    custom: [
      { id: 'ok-one', name: 'Okay', url: 'https://okay.example' },
      { id: 'Bad Id', name: 'Nope', url: 'https://nope.example' },
      { id: 'hole', name: 'Dupe', url: 'https://dupe.example' },
    ],
  });
  assert.equal(byId(list, 'unstop').url, null);
  assert.equal(byId(list, 'ok-one').level, 'view');
  assert.equal(byId(list, 'Bad Id'), undefined);
  assert.equal(list.filter((s) => s.id === 'hole').length, 1);
});

test('the rail is three places: Dashboard, Listings and Social', () => {
  const list = resolveServices({}, 'https://hole.example');
  const places = sectionsOf(list);
  assert.deepEqual(places.map((p) => [p.id, p.name, p.icon]), [
    ['dashboard', 'Dashboard', 'hole'],
    ['listings', 'Listings', 'listings'],
    ['social', 'Social', 'social'],
  ]);
  assert.deepEqual(places[0].services, ['hole']);
  assert.deepEqual(places[1].services, ['indeed', 'proofr', 'unstop', 'devfolio', 'devpost', 'mlh']);
  assert.deepEqual(places[2].services, ['linkedin', 'discord']);
  // every site is in exactly one place
  assert.deepEqual(places.flatMap((p) => p.services).sort(), list.map((s) => s.id).sort());
});

test('a place shows the most the app may do on any of its sites', () => {
  const level = (overrides, id) => sectionsOf(resolveServices(overrides)).find((p) => p.id === id).level;
  assert.equal(level({}, 'dashboard'), 'view');
  assert.equal(level({}, 'listings'), 'capture');
  assert.equal(level({ devpost: { level: 'batch' } }, 'listings'), 'batch');
  assert.equal(level({ linkedin: { level: 'view' }, discord: { level: 'view' } }, 'social'), 'view');
  assert.equal(level({ linkedin: { level: 'view' } }, 'social'), 'capture');
});

test('sites can be moved between places in services.json, but Hole stays the dashboard', () => {
  const list = resolveServices({
    discord: { section: 'listings' },
    hole: { section: 'social' },
    mlh: { section: 'nowhere' },
    custom: [
      { id: 'a', name: 'A', url: 'https://a.example', section: 'social' },
      { id: 'b', name: 'B', url: 'https://b.example' },
      { id: 'c', name: 'C', url: 'https://c.example', section: 'dashboard' },
    ],
  });
  const place = (id) => byId(list, id).section;
  assert.equal(place('discord'), 'listings');
  assert.equal(place('hole'), 'dashboard');
  assert.equal(place('mlh'), 'listings');
  assert.equal(place('a'), 'social');
  assert.equal(place('b'), 'listings'); // no place named: Listings
  assert.equal(place('c'), 'listings'); // the dashboard is Hole's alone
});

test('a place with no sites is left off the rail', () => {
  const list = resolveServices({ linkedin: { section: 'listings' }, discord: { section: 'listings' } });
  assert.deepEqual(sectionsOf(list).map((p) => p.id), ['dashboard', 'listings']);
});
