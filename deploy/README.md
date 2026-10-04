# Deploying

The app is one Python process (`python -m jobsearch web`) that renders every page
itself, so there is no separate frontend build to host. Production is three layers:

    browser -> Vercel (stable URL, proxy) -> Caddy on the Oracle VM (TLS) -> jobsearch on :8765 -> Turso

## Oracle VM (`deploy/oracle/`)

Oracle Linux 9, Always Free. `jobsearch.service` and `caddy.service` go in
`/etc/systemd/system/`, `Caddyfile` in `/etc/caddy/`. Change the hostname in the
Caddyfile and `JOBSEARCH_HOST` in the unit if the public IP changes (the name is
`<ip with dashes>.sslip.io`, which needs no DNS setup).

Things that bit during setup:

- **SELinux is enforcing.** Files under the home directory are not executable by
  systemd until they are relabelled:
  `sudo semanage fcontext -a -t usr_t "/home/opc/Hole(/.*)?" && sudo chcon -R -t usr_t /home/opc/Hole`
- **Two firewalls.** Open TCP 80 and 443 in the VCN security list *and* on the VM:
  `sudo firewall-cmd --permanent --add-service={http,https} && sudo firewall-cmd --reload`
- **Under 500 MB of RAM** on the AMD Micro shape. Add a swapfile or `dnf` and
  Chromium will get the process OOM-killed.
- `.env` (Turso credentials, `JOBSEARCH_PASSWORD`) and `config.toml` are gitignored;
  copy them up by hand and `chmod 600` them. `frontend/dist` is not needed.

Update: `cd ~/Hole && git pull && sudo systemctl restart jobsearch`.

**Logins survive that restart.** A login (the 30-day cookie you get for the web password) is
kept in `output/web-sessions.json`, so restarting the service does not sign anyone out. The file
holds only an HMAC of each login's token under the password, nothing that can be turned back into
a cookie: changing `JOBSEARCH_PASSWORD` ends every login, and so does deleting the file.

**The daily run.** `jobsearch-run.service` is one pipeline run (`python -m jobsearch run`)
and `jobsearch-run.timer` starts it every day at 12:00 GMT, so the lists refresh and drafts
get written without anyone opening an app. Install both once:

    sudo cp deploy/oracle/jobsearch-run.service deploy/oracle/jobsearch-run.timer /etc/systemd/system/
    sudo systemctl daemon-reload && sudo systemctl enable --now jobsearch-run.timer
    systemctl list-timers jobsearch-run.timer        # the next time it fires

A run can also be started on request: the dashboard's "Update listings now" button, the
desktop app's "Update listings" button, or `POST /api/run` with the ingest token. Two runs
never overlap, however they start.

The two endpoints are what an app (the desktop one, or a phone one) needs, both with
`Authorization: Bearer <JOBSEARCH_API_TOKEN>` and both answering 404 until that is set:

- `POST /api/run`: 202 `{"started": true, "running": true}` when a run begins, 409 with
  `"running": true` when one is already going.
- `GET /api/status`: `{"running", "run": {"id", "started_at", "finished_at", "sourced",
  "tailored", "queued", "sent", "errors", ...}, "next_run_at", "run_at", "timezone",
  "auto_apply", "counts": {"remaining", "drafted", "applied", "sent", "freelance"},
  "caps": {...}}`. `remaining` is what is open and not yet applied to, `applied` what you
  said yes to, whether or not it has gone out. The time is set in two places that must agree, the timer's `OnCalendar` and
`[schedule] run_at` in `config.toml`, which is what the dashboard and the apps announce;
`tests/test_deploy.py` checks the shipped files against each other.

**When the run cannot tailor.** If the model's key is missing or turned down (a `401
UNAUTHENTICATED` in `journalctl -u jobsearch-run.service`), the run stops asking after the first
posting and leaves everything waiting, and nothing is marked failed. Fix the key in `~/Hole/.env`
(`python -m jobsearch config --check-model` makes one tiny call and says whether it works) and
the next run takes the postings. A run that failed several postings one after another stops
the same way after five. Postings that were marked failed for a reason that was not theirs, by an
older version, go back with `python -m jobsearch jobs retry --reason UNAUTHENTICATED`.

## Vercel (`deploy/vercel/`)

A rewrite-only project: every request is proxied to the Oracle origin, so the public
URL survives a change of VM. Import the repo in Vercel with **Root Directory** set to
`deploy/vercel` and **Framework Preset** *Other*; it then redeploys on every push to
`main`. If the VM's address changes, edit both destinations in `vercel.json` (along with
the Caddyfile and `JOBSEARCH_HOST`; `tests/test_deploy.py` fails if they disagree).

Production is `hole-roan.vercel.app` (plain `hole` was taken). What bit during setup:

- **`/` needs its own rewrite.** `/:path*` does not match the bare root, so without it
  the front page, and the redirect after login, is Vercel's 404.
- **Vercel forwards the destination's `Host`,** not the visitor's, so the origin needs
  nothing added for the Vercel hostname and `JOBSEARCH_HOST` stays the origin's own
  name. The comma-separated list is for a proxy that does forward the original Host.
