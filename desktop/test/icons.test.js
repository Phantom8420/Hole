'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { BUILTIN } = require('../src/services');
const { ICONS } = require('../shell/icons');

test('every built-in service has a mark, and so does the settings button', () => {
  for (const id of [...BUILTIN.map((s) => s.id), 'settings']) {
    assert.ok(ICONS[id], `no mark for ${id}`);
  }
});

test('a mark is one plain SVG path, small enough to be a rail icon', () => {
  for (const [id, d] of Object.entries(ICONS)) {
    assert.match(d, /^M[MmLlHhVvCcSsQqTtAaZz0-9eE.,\s-]+$/, `${id} holds characters an SVG path does not`);
    for (const number of d.match(/-?\d*\.?\d+(?:e-?\d+)?/gi)) assert.ok(Number.isFinite(Number(number)), `${id}: ${number}`);
    assert.ok(d.length < 4000, `${id} is ${d.length} characters`);
  }
});

test('the app icon is a real PNG and an ICO of PNGs at every size Windows asks for', () => {
  const dir = path.join(__dirname, '..', 'assets');
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const png = fs.readFileSync(path.join(dir, 'icon.png'));
  assert.ok(png.subarray(0, 8).equals(signature));
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [256, 256]);

  const ico = fs.readFileSync(path.join(dir, 'icon.ico'));
  assert.deepEqual([ico.readUInt16LE(0), ico.readUInt16LE(2)], [0, 1]);
  const sizes = [];
  for (let i = 0; i < ico.readUInt16LE(4); i += 1) {
    const entry = 6 + 16 * i;
    sizes.push(ico[entry] || 256);
    assert.ok(ico.subarray(ico.readUInt32LE(entry + 12), ico.readUInt32LE(entry + 12) + 8).equals(signature), `image ${i} is not a PNG`);
  }
  for (const wanted of [16, 32, 48, 256]) assert.ok(sizes.includes(wanted), `no ${wanted}px image in ${sizes}`);
});

test('the shell loads the marks before the script that draws them', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'shell', 'index.html'), 'utf8');
  const icons = html.indexOf('src="icons.js"');
  assert.ok(icons > 0, 'icons.js is not loaded');
  assert.ok(icons < html.indexOf('src="shell.js"'), 'icons.js must come first');
});
