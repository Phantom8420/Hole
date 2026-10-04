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
- You stay signed in between launches. A site that signs you in with a cookie that has no
  expiry (a session cookie) would normally sign you out when the app closes, so
  `src/cookies.js` stores such a cookie again, with a 30-day expiry, a few seconds after it
  appears and once more as the app closes. The site's own session timeout still applies: this
  keeps the cookie, not the sign-in, so when the site has ended the session you sign in again.
- `src/driver.js` is the control surface: `goto`, `waitFor`, `evaluate`, `click`, `type`,
  `press`, `screenshot`. Reads run in an isolated world the site's scripts cannot see;
  clicks and keys are real input events.
- **Capture page** runs the service's extractors (`src/extractors.js`) over the page you
  are looking at: JSON-LD `JobPosting`/`Event`, LinkedIn and Indeed result lists, links
  that look like competitions, and the page itself as a fallback. The results land in the
  inbox, where you edit and tick them. **Send selected to Hole** posts them to
  `/api/ingest`. Nothing is sent in the background.
- The rail is three places: **Dashboard** (Hole), **Listings** (Indeed, Proofr, Unstop, Devfolio,
  Devpost, MLH) and **Social** (LinkedIn, Discord). A place with more than one site shows them as a
  strip of tabs under the toolbar; every site keeps its own page and its own sign-in, and a place
  remembers the site you were last on. The dot on a place is the most the app may do on any site in
  it. In `services.json` a site can say `"section": "listings"` or `"social"` to move (Hole stays the
  dashboard); a site you add goes in Listings unless it says otherwise.
- Places and sites are drawn by their marks (`shell/icons.js`: one 24 x 24 path each, painted in the
  button's own colour). A site you add in `services.json` shows its name alone on its tab. The Android
  app draws the same paths. The window icon is `assets/icon.ico`; `python tools/make-icon.py` redraws it.

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
