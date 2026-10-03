'use strict';

const fs = require('node:fs');

// `.env.local` next to package.json (gitignored) is the development settings file:
// HOLE_URL, HOLE_API_TOKEN, HOLE_USER_DATA. Only HOLE_* names are read, and a
// variable that is already in the environment wins, so an export still overrides it.

function parseEnv(text) {
  const out = {};
  for (const line of text.split(/\r?\n/)) {
    const match = /^\s*(HOLE_[A-Z0-9_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (match) out[match[1]] = match[2].replace(/^(["'])(.*)\1$/, '$2');
  }
  return out;
}

function loadEnvFile(file, env = process.env) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return [];
  }
  const loaded = [];
  for (const [key, value] of Object.entries(parseEnv(text))) {
    if (value && !(key in env)) {
      env[key] = value;
      loaded.push(key);
    }
  }
  return loaded;
}

module.exports = { parseEnv, loadEnvFile };
