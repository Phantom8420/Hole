'use strict';

// The two things the app asks the server about its pipeline: start a run, and how it
// stands. Same address and token as /api/ingest. The run itself happens on the server
// (nothing here fetches postings or applies to them), so closing the app does not stop one
// and the 12:00 GMT run happens whether or not the app is open; the app is a remote for it.

async function call({ holeUrl, token, path, method, fetchImpl = fetch }) {
  if (!holeUrl) throw new Error('Set the Hole address in Settings first');
  if (!token) throw new Error('Set the ingest token in Settings first');
  const response = await fetchImpl(new URL(path, holeUrl).href, {
    method,
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = await response.json().catch(() => ({}));
  // 409 is the server saying a run is already going, which is an answer rather than a failure.
  if (!response.ok && response.status !== 409) throw new Error(body.error || `Hole answered HTTP ${response.status}`);
  return { status: response.status, body };
}

async function startRun(options) {
  const { status, body } = await call({ ...options, path: '/api/run', method: 'POST' });
  return { started: status === 202 && body.started === true, running: Boolean(body.running) };
}

async function fetchStatus(options) {
  return (await call({ ...options, path: '/api/status', method: 'GET' })).body;
}

function ago(iso, now) {
  const then = Date.parse(iso && /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(then)) return '';
  const minutes = Math.max(0, Math.round((now.getTime() - then) / 60000));
  if (minutes < 2) return 'just now';
  if (minutes < 120) return `${minutes} min ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} days ago`;
}

// One line for the toolbar, and whether a run is going (so the shell polls faster).
function describe(status, now = new Date()) {
  const parts = [];
  const run = status && status.run;
  if (status && status.running) {
    parts.push(`Running now${run ? `, started ${ago(run.started_at, now)}` : ''}`);
  } else if (run) {
    parts.push(`Last run ${ago(run.finished_at || run.started_at, now)}: ${run.sourced} new, ${run.tailored} drafted, ${run.sent} sent`);
  } else {
    parts.push('Not run yet');
  }
  if (status && status.run_at) parts.push(`next ${status.run_at} ${status.timezone || 'GMT'}`);
  const counts = status && status.counts;
  if (counts) parts.push(`${counts.remaining} remaining, ${counts.applied} applied`);
  if (status && status.auto_apply) parts.push('auto-apply on');
  return { text: parts.join(' · '), running: Boolean(status && status.running) };
}

module.exports = { startRun, fetchStatus, describe, ago };
