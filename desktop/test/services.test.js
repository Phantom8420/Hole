'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { resolveServices } = require('../src/services');

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
