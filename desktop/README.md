# Hole desktop

An Electron shell for Hole: the web UI, plus an embedded browser for each service you
look for work on (LinkedIn, Indeed, Discord, Devpost, Unstop, ...). The app creates and
owns those browser views, so it can drive them directly -- no extension to install, no
debugging port, nothing to approve -- and sends what you pick to the Hole server.

    npm install        # downloads Electron (~100 MB) on first use
    npm start
    npm test           # unit tests, no Electron needed
    npm run selftest   # drives a real embedded page inside Electron

First run: open **Settings** (gear), set the Hole address and the ingest token. The token
must equal `JOBSEARCH_API_TOKEN` on the server; it is kept in the OS keychain
(`safeStorage`), or read from `HOLE_API_TOKEN` for development. `HOLE_URL` sets the
default address, `HOLE_USER_DATA` points the app at a throwaway profile.

For development, those three can live in `desktop/.env.local` (gitignored), one
`NAME=value` per line; a variable already in the environment wins over the file.

## How it works

- Each service gets a `WebContentsView` with its own persistent session
  (`persist:svc-<id>`). You sign in once, by hand, inside the app; nothing here handles
  your passwords.
- `src/driver.js` is the control surface: `goto`, `waitFor`, `evaluate`, `click`, `type`,
  `press`, `screenshot`. Reads run in an isolated world the site's scripts cannot see;
  clicks and keys are real input events.
- **Capture page** runs the service's extractors (`src/extractors.js`) over the page you
  are looking at: JSON-LD `JobPosting`/`Event`, LinkedIn and Indeed result lists, links
  that look like competitions, and the page itself as a fallback. The results land in the
  inbox, where you edit and tick them. **Send selected to Hole** posts them to
  `/api/ingest`. Nothing is sent in the background.

## What the app may do on each site

Per-service level, in `src/services.js` (override in `<userData>/services.json`):

| level | |
|---|---|
| `view` | the page is displayed; the app never runs script in it |
| `capture` | Capture page reads the page you are on, when you press it |
| `batch` | the app may also visit that service's saved `searches` (Scan), within the daily load budget in `src/limits.js` |

LinkedIn, Indeed and Discord are at `capture`. Their terms forbid automated access (the
rest of Hole already follows that rule: see `sourcing/competitions.py`), and an embedded
browser changes where the code runs, not what the site allows or whether it notices.
Raising a level is an edit you make; reading your own logged-in pages one at a time is
the end of what this tool is built for. The load budget is fixed spacing and a daily
cap, deliberately not randomised: it exists to keep the app polite.

## Notes

- The extractors for LinkedIn and Indeed are best effort -- their markup changes. The
  fixtures in `test/fixtures/` are hand-written to the structure the extractors rely on;
  a miss falls back to the page capture instead of failing.
- The project lives in OneDrive, which will try to sync `node_modules/` (Electron alone
  is several hundred MB). Exclude `desktop/node_modules` from sync.
- Packaging (an installer) is not set up yet.
