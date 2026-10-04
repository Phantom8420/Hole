'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { generate, normalisePath, MAIN } = require('../../android/tools/sync');

// A Windows checkout may turn the generated files' line ends into CRLF.
const lf = (text) => text.replace(/\r\n/g, '\n');

test('the Android files taken from the desktop app are up to date', () => {
  for (const [file, content] of Object.entries(generate())) {
    const target = path.join(MAIN, file);
    assert.ok(fs.existsSync(target), `${file} is missing: run node android/tools/sync.js`);
    assert.equal(lf(fs.readFileSync(target, 'utf8')), content, `${file} is out of date: run node android/tools/sync.js`);
  }
});

test('the palette is the one the desktop shell is drawn in', () => {
  const colours = generate()['res/values/hole_colors.xml'];
  for (const hex of ['#14110F', '#1D1815', '#322A25', '#F2EBE4', '#8D8178', '#F26A2E', '#E5584B', '#5FB37C', '#1A0E07']) {
    assert.ok(colours.includes(hex), `${hex} is not in the Android palette`);
  }
});

test('normalisePath writes every segment out in full', () => {
  assert.equal(normalisePath('M1 1l2 2 3 3z'), 'M1 1l2 2l3 3z');
  assert.equal(normalisePath('M1 1 2 2'), 'M1 1L2 2');
  assert.equal(normalisePath('m1 1 2 2'), 'm1 1l2 2');
  assert.equal(normalisePath('H5 6 7'), 'H5H6H7');
  assert.equal(normalisePath('M0 0z'), 'M0 0z');
  // numbers run together, either by a sign or by a second decimal point
  assert.equal(normalisePath('M1-2.5.5.5'), 'M1 -2.5L.5 .5');
  assert.equal(normalisePath('M1e1 2E-1'), 'M1e1 2E-1');
});

test('normalisePath pulls the flags of an arc apart', () => {
  assert.equal(normalisePath('M0 0a1 1 0 011 1'), 'M0 0a1 1 0 0 1 1 1');
  assert.equal(normalisePath('M0 0A2 2 0 00-1.5-1.5'), 'M0 0A2 2 0 0 0 -1.5 -1.5');
  assert.equal(normalisePath('M0 0a1 1 0 111 1 1 1 0 001 1'), 'M0 0a1 1 0 1 1 1 1a1 1 0 0 0 1 1');
});

test('normalisePath refuses what it cannot read rather than guess', () => {
  assert.throws(() => normalisePath('X1 2'), /unexpected/);
  assert.throws(() => normalisePath('M1'), /expected a number/);
  assert.throws(() => normalisePath('M'), /no numbers/);
  assert.throws(() => normalisePath('M0 0a1 1 0 21 1 1'), /expected a number/);
});
