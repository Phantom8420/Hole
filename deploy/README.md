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
