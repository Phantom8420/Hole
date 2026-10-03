'use strict';

const fs = require('node:fs');
const path = require('node:path');

// Hand reviewed items to the Hole backend (POST /api/ingest). Only items you
// ticked in the inbox ever get here; nothing is sent in the background.

function toPayload(items, source) {
  return {
    source,
    items: items.map((item) => ({
      kind: item.kind === 'competition' ? 'competition' : 'job',
      title: item.title,
      company: item.company,
      location: item.location,
      url: item.url,
      description: item.description,
      deadline: item.deadline,
      category: item.category,
    })),
  };
}

async function sendItems({ holeUrl, token, items, source = 'desktop', fetchImpl = fetch }) {
  if (!holeUrl) throw new Error('Set the Hole address in Settings first');
  if (!token) throw new Error('Set the ingest token in Settings first');
  const response = await fetchImpl(new URL('/api/ingest', holeUrl).href, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(toPayload(items, source)),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Hole answered HTTP ${response.status}`);
  return body;
}

// The token lives in the OS keychain (DPAPI / Keychain / libsecret) via
// Electron's safeStorage, never in settings.json. If the OS cannot provide that,
// refuse to store it rather than fall back to plain text.
function tokenStore(dir, safeStorage, env = process.env) {
  const file = path.join(dir, 'ingest-token.bin');
  return {
    load() {
      if (env.HOLE_API_TOKEN) return env.HOLE_API_TOKEN;
      try {
        return safeStorage.decryptString(fs.readFileSync(file));
      } catch {
        return '';
      }
    },
    save(token) {
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error('This system has no secure key storage, so the token was not saved');
      }
      fs.writeFileSync(file, safeStorage.encryptString(token));
    },
    present() {
      return Boolean(this.load());
    },
  };
}

module.exports = { sendItems, toPayload, tokenStore };
