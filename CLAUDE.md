# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hole (package name `jobsearch`) turns a career history into a queryable graph and
generates resumes from it as a query, not a rewrite: pick the positions that matter for
a posting, pick the strongest bullets under each, list only skills that have evidence.
Three interfaces — CLI, a local web UI, and an MCP server — share one backend
(`jobsearch/*.py`); none of them contain business logic the others don't already call.

Two rules the whole codebase is organized around:

- **Selection is deterministic; only phrasing is generative.** `retrieval.py` (code)
  decides which positions/bullets/skills go into a `ResumePlan`. `generate.py` (the
  model) only writes prose for that plan — it never chooses what is true.
- **A skill with no evidence cannot reach a resume.** `skill_evidence` rows link a
  claimed skill to the experience/project/achievement/certification that proves it.
  Unevidenced skills are excluded in code (`retrieval.py`), not by prompting the model
  to behave.

## Commands

```bash
# Setup
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Gemini key (free tier, default provider): $env:GEMINI_API_KEY = "..." or .env

python -m jobsearch config          # effective settings + what would block a run
python -m jobsearch init            # create the database

# Tests -- no API key, no network needed; both LLM SDKs are faked
python -m unittest discover -s tests -v
python -m unittest tests.test_pipeline -v                          # one file
python -m unittest tests.test_pipeline.PipelineTests.test_x -v     # one test

# The tests never touch Turso: a path passed to db.connect()/--db always means that
# SQLite file, and cli.main(argv) does not read .env (only a real invocation does).
# Keep it that way -- a run that inherited TURSO_DATABASE_URL once wrote fixtures into
# the production database. To be certain on a machine whose shell exports it, blank it:
#   TURSO_DATABASE_URL= TURSO_AUTH_TOKEN= python -m unittest discover -s tests

# Frontend (React, mounted by jobsearch/web/pages.py -- rebuild after any change
# under frontend/src, the Python server reads the built bundle, not source)
cd frontend && npm install && npm run build

# Run
python -m jobsearch run [--dry-run]      # source -> score -> screen -> tailor -> verify -> decide -> dispatch
python -m jobsearch web                  # loopback-only review UI at :8765
python -m jobsearch mcp [--db path]      # MCP server over stdio (needs `pip install mcp`)
python -m jobsearch tailor --job-file posting.txt --company X --role Y [--dry-run]
python -m jobsearch match --job-file posting.txt --company X   # scoring only, no API call
```

Pass `--db` *after* the subcommand, or set `$JOBSEARCH_DB`, to point any command at a
different SQLite file (e.g. `demo.db` for fixture/demo data instead of the real
`jobsearch.db`).

## Architecture

**The graph, not a document.** `schema.py` models a person the way a LinkedIn profile
actually is structured: positions belong to organizations, achievements attach to
*exactly one* parent (a position, project, or degree — enforced by a CHECK constraint,
so a bullet can never float free of the dates/employer that place it), and
`skill_evidence` requires exactly one target. Every row carries `source_id` (which
import produced it) and `verified` (has a human confirmed it), so a bad import can be
undone wholesale (`jobsearch sources undo <id>`) and autonomous sending can be
restricted to confirmed rows only.

**Pipeline stages are separate modules, composed in `pipeline.py`:** `sourcing/` finds
postings (Greenhouse/Lever/Ashby + several remote-job aggregators — no LinkedIn or
Indeed connector, both forbid automated access), `matching.py` scores fit with no API
call, `retrieval.py` turns a job description + the graph into a `ResumePlan`,
`generate.py` calls the model, `verify.py` grounds the output against the plan's facts
(catches invented metrics/employers before a human ever sees the draft), `policy.py`
holds the only two functions allowed to authorize sending or skipping, `dispatch/`
actually sends (Gmail API fully automated; ATS forms via Playwright, aborts rather than
guessing; LinkedIn is draft-only, permanently, by design).

**The autonomy gate is one boolean with real teeth.** `config.toml`'s `autonomous`
defaults `false`; `policy.decide_dispatch()` is the only function that can authorize a
send, and it checks that flag first, before anything else. Even with it on, sending
still requires a clean grounding check, complete profile fields, and passing per-run /
per-day / per-company-per-week caps (`LimitsConfig`). Every skip and every send reason
is recorded on the row it applies to.

**Three interfaces, one backend, no duplicated logic.** `cli.py`, `web/server.py`, and
`mcp/server.py` all call the same functions in `pipeline.py`/`db.py`/etc. When the same
sequence of calls was needed in two places (e.g. tailoring a single job on demand), it
was pulled into a shared function (`pipeline.tailor_one`) rather than copied — if you
find yourself duplicating a multi-step sequence across two of these interfaces, that's
the pattern to follow instead.

**Frontend is a React SPA per page, not client-side routed.** Every route in
`jobsearch/web/pages.py` returns a full HTML document embedding the page's data as JSON
(`_react_page()`); `frontend/src/main.jsx` picks the right page component by route and
mounts it fresh. There is no client-side router — navigating to a different page is a
real page load. Design tokens live in `frontend/src/tokens.js` as a single `C` object;
shared primitives (`Panel`, `DataTable`, `Avatar`, `RowIdentity`, buttons, etc.) live in
`frontend/src/components.jsx` — changing a token value or a shared component recolors/
reshapes every page at once, which is the intended way to reskin the whole app rather
than editing each page.

**Two-tier migrations.** `schema.py`'s `DDL` is `CREATE TABLE IF NOT EXISTS` (safe on a
fresh db); columns added to an existing table after that DDL shipped go in
`db.ADDITIVE_COLUMNS` and get `ALTER TABLE`'d in on connect (`db.add_missing_columns`).
Adding a column to an existing table needs *both* — the DDL for a fresh install, the
additive entry for an existing one.

## Deployment

Production is Vercel (a stable URL that only proxies) -> Caddy on an Oracle Always Free
VM -> `python -m jobsearch web` -> Turso; the files and the traps are in `deploy/`. The UI
is rendered by the Python server, so there is no separate frontend deploy (the React bundle
in `frontend/` is no longer routed). `POST /api/ingest` takes opportunities from a client
that captured them elsewhere; it is off unless `JOBSEARCH_API_TOKEN` is set and needs
`Authorization: Bearer <token>`.

## Desktop shell

`desktop/` is an Electron app (`npm install && npm start`): the Hole UI plus an embedded
browser per service (LinkedIn, Indeed, Discord, Devpost, ...), each in its own persistent
profile. The app drives those pages itself (`src/driver.js`) -- no extension, no debugging
port, nothing to approve -- and posts what you tick in its inbox to `/api/ingest`. What it
may do per service is a level in `src/services.js` (view / capture / batch); LinkedIn,
Indeed and Discord stay at capture, matching the rule in `sourcing/` that nothing automates
a site that forbids it. `npm run selftest` drives a real embedded page and the extractors;
`HOLE_DESKTOP_E2E=1 python -m unittest tests.test_desktop_e2e` also posts to the real server.

## Codex config detected

`~/.codex/config.toml` exists. Reply `/import` to scan it for importable items (MCP
servers, slash commands, subagents, skills, instructions), or `/import --yes=<digest>`
using the digest the scan names to apply the user-level items.

## Session log — what's been asked for, what's been built, where it stands

This section is a point-in-time record, not architecture reference — it will go stale.
Kept here because it was asked for directly; treat dates/branch names as of 2026-08-26.

### What was asked for (source material)

- **Voice note** (transcribed): full autonomy, a profile page structured like LinkedIn,
  the app finding competitions itself instead of the user typing them in, and a tailored
  resume + cover letter generated per posting from the profile graph.
- **LinkedIn screenshots** (linkedin.com/in/ishaanjain842) and **GitHub**
  (github.com/Specter842) — the real profile data now loaded, replacing fixture data.
- **A folder of ~50 job-board links** ("100 Remote job websites") — the source list
  behind the sourcing expansion below.
- **github.com/MLS-Tech-Inc/shortlistjobs-mcp** — a hosted third-party job-application
  MCP server, reviewed and explicitly *not* adopted (uploads resume/profile data to a
  stranger's backend); its one good idea (expose the pipeline as MCP tools) was built
  locally instead — see `jobsearch/mcp/`.
- **Two rounds of UI reference screenshots** — a lime/purple SaaS dashboard, then an
  orange/maroon flight-booking app (Evoque) — each time with the explicit instruction to
  replace the frontend wholesale, not blend old structure with new colors.

### What's been built

- **Competitions discovery** — `jobsearch/sourcing/competitions.py` (Devpost's public
  API; Unstop/Devfolio/MLH recorded as bookmark rows since they can't be read
  automatically), wired into every `jobsearch run` automatically, plus `add_job`-style
  manual entry from the web UI and MCP. Currently tracking real discovered opportunities
  in addition to hand-entered ones.
- **Job sourcing expanded** from 3 placeholder boards to 32 real ones drawn from the
  provided link list — Greenhouse, Ashby, RemoteOK, Jobicy, WorkingNomads, Himalayas,
  Remotive. A live run has sourced 7,000+ real postings.
- **Real profile loaded** — the fixture profile (fake positions/email) was removed via
  `sources undo`; the graph now holds real LinkedIn-derived positions/education/certs/
  awards/volunteering plus real GitHub projects via the new `ingest/github.py` importer
  (language-as-skill-evidence, forks skipped).
- **MCP server** (`jobsearch/mcp/`) — 11 tools covering search, on-demand tailoring, the
  review queue, profile, and competitions, all calling the same functions the CLI and
  web UI already call (`pipeline.tailor_one()` is shared three ways, not duplicated).
  Runs locally over stdio; no hosted backend.
- **UI rebuilt twice**, each time completely (new palette, new component shapes, not a
  recolor of the old structure): first to a lime/purple language, then to the current
  warm orange/maroon one. Both passes touched the shared token file and component
  primitives so the whole app changed at once, then rebuilt the Dashboard's actual
  layout (not just its colors) to match the reference screenshot's structure. Nav went
  through icon-rail -> bottom bar -> the current top taskbar.
- **`CLAUDE.md`** (this file).

### Where things stand

Four PRs are open — #4 (competitions discovery), #5 (job sources), #6 (MCP server), #7
(current UI, orange/maroon). They're **stacked**, not independent: #6 and #7 already
contain #4 and #5's commits in their history (built sequentially on the same branch
lineage rather than each cut fresh from `main`). Merging #7 pulls in all four; #4/#5/#6
can then be closed rather than merged separately, or merge them in order (#4, #5, #6,
#7) if a cleaner individual history in `main` matters more than doing it in one shot.
Three earlier PRs (#1-#3, UI polish) are already merged.

Not yet done, and worth knowing before assuming otherwise:

- **`autonomous` is still `false`.** Nothing has ever been sent under the user's name.
- **No accomplishment bullets** are recorded under the real positions yet — only real
  metrics the user supplies belong there, so this was left empty rather than invented.
- **Cold email at volume and LinkedIn DM automation were explicitly declined as asked**
  — 150 LinkedIn DMs/day would get the account banned (`dispatch/linkedin.py` is
  draft-only, permanently, by design); high-volume cold email needs a real deliverability
  plan (sending domain, warmup) before raising `daily_cap`, not just raising the number.
- **No resume PDF template/format has been supplied** — tailoring uses `render.py`'s
  default one-page layout.
- `demo.db` / `jobsearch.db` are gitignored — the real profile and sourced-jobs data
  described above exist locally, not in any of these commits.
