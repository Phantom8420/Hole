"""The five Evoque-shell pages.

The dashboard is the reference layout with this app's data in it: the flight
list is sourced postings ranked by fit, the globe arc runs between the two
countries those postings actually cluster in, and the slot the reference gave
to an aircraft photo holds the discovery chart instead.

Jobs, Competitions, Resume and Profile are branches of it -- same sidebar,
same panel/row vocabulary from `evoque.py`, arranged for what each one shows.
No page defines its own styling.

Aggregation is reused from `pages.py` rather than re-queried here, so both
front ends always report the same numbers.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any, Sequence

from .. import answers as answer_bank
from .. import db, policy
from ..config import Config
from . import evoque as E
from . import geo, pages
from .html import esc

FALLBACK_ARC = ((39.8, -98.6), (54.0, -2.5))  # US -> UK, if data names neither


# --------------------------------------------------------------------------- shared


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Badge counts for the nav, keyed by the nav's own route keys."""
    def one(sql: str) -> int:
        try:
            return int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            return 0

    return {
        "jobs": one("SELECT COUNT(*) FROM jobs"),
        "competitions": one("SELECT COUNT(*) FROM competitions"),
        "resume": one("SELECT COUNT(*) FROM applications WHERE status='drafted'"),
    }


def _fit(job: dict[str, Any]) -> tuple[str, str]:
    """Fit score as (text, tone-class) -- blank when never scored, because an
    unscored posting genuinely has no score and 0 would read as a bad one."""
    score = job.get("fit_score")
    if score is None:
        return "", "mute"
    pct = int(round(float(score) * 100)) if float(score) <= 1 else int(round(float(score)))
    tone = "good" if pct >= 70 else "" if pct >= 45 else "mute"
    return f"{pct}", tone


def _where(job: dict[str, Any]) -> str:
    if job.get("remote"):
        return "Remote"
    return (job.get("location") or "—")[:28]


def _days_until(value: Any) -> int | None:
    """Whole days from today to an ISO-ish date, or None if it isn't one.

    Deadlines in this table are free text as often as they are dates ("rolling",
    "TBA"), so anything unparseable is shown as-is rather than guessed at.
    """
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            when = datetime.strptime(text[:len(datetime.now().strftime(fmt)) + 4].strip(), fmt)
        except ValueError:
            continue
        return (when.date() - date.today()).days
    return None


def _deadline_chip(value: Any) -> str:
    """Registration deadline -- the one that actually closes the door."""
    text = str(value or "").strip()
    if not text:
        return '<span class="date-chip none">no deadline</span>'
    left = _days_until(text)
    if left is None:
        tone = ""
        label = text[:18]
    elif left < 0:
        tone = " past"
        label = f"closed {text[:12]}"
    elif left <= 7:
        tone = " soon"
        label = f"{left}d left"
    else:
        tone = ""
        label = f"{left}d left"
    return (
        f'<span class="date-chip{tone}" title="Applications close {esc(text)}">'
        f'<b>APPLY</b>{esc(label)}</span>'
    )


def _period_chip(value: Any) -> str:
    """When the competition itself runs."""
    text = str(value or "").strip()
    if not text:
        return '<span class="date-chip none"><b>RUNS</b>TBA</span>'
    return (
        f'<span class="date-chip" title="Competition runs {esc(text)}">'
        f'<b>RUNS</b>{esc(text[:18])}</span>'
    )


def _competition_order(c: dict[str, Any]) -> tuple[int, int, str]:
    """`deadline` is free text as often as a date, so SQL cannot order it
    sensibly ("Apr" sorts before "Aug"). Sort here instead, and put the ones
    you can still enter first -- a list led by closed competitions is useless."""
    left = _days_until(c.get("deadline"))
    if left is None:
        return (1, 0, str(c.get("name") or ""))   # undated: after the open ones
    if left < 0:
        return (2, -left, str(c.get("name") or ""))  # closed: last, most recent first
    return (0, left, str(c.get("name") or ""))       # open: soonest first


# Ordered so a title matching more than one bucket lands in the more specific
# one -- "Senior Backend Engineer, Data Platform" hits "data" before the
# generic "swe" catch-all gets a chance. Checked against the lowercased title
# only; the description isn't specific enough to be worth the extra noise.
ROLE_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("Internships", ("intern", "co-op", "coop")),
    ("New Grad / Junior", ("new grad", "junior", "graduate", "entry level", "entry-level", "associate")),
    ("Data / ML", ("data engineer", "data scientist", "machine learning", "ml engineer", "ai engineer", "applied scientist")),
    ("DevOps / Platform / SRE", ("devops", "site reliability", " sre", "platform engineer", "infrastructure engineer")),
    ("Mobile", (" ios ", "android", "mobile engineer")),
    ("Frontend", ("frontend", "front-end", "front end", "ui engineer")),
    ("Full Stack", ("full stack", "full-stack", "fullstack")),
    ("Backend", ("backend", "back-end", "back end")),
    ("Security", ("security engineer", "appsec", "infosec")),
    ("Software Engineer", ("software engineer", "swe", "software developer")),
]


def _role_bucket(title: str) -> str:
    t = f" {(title or '').lower()} "
    for label, needles in ROLE_BUCKETS:
        if any(n in t for n in needles):
            return label
    return "Other"


def _grouped_job_panels(jobs: Sequence[dict[str, Any]]) -> str:
    """One panel per role bucket, in ROLE_BUCKETS order, then Other last.

    Grouping happens after the SQL query, over whatever page of rows it
    already returned -- same 200-row cap and fit/remote ordering as the flat
    list, just sectioned instead of run together.
    """
    order = [label for label, _ in ROLE_BUCKETS] + ["Other"]
    grouped: dict[str, list[dict[str, Any]]] = {label: [] for label in order}
    for job in jobs:
        grouped[_role_bucket(job.get("title") or "")].append(job)
    panels = []
    for label in order:
        rows = grouped[label]
        if not rows:
            continue
        panels.append(
            E.panel(f"{label} ({len(rows)})", _job_rows(rows), sub="ranked by fit, then newest")
        )
    return "".join(panels)


def _job_rows(jobs: Sequence[dict[str, Any]]) -> str:
    """The reference's flight rows: source, company, title, fit."""
    out = []
    for job in jobs:
        pct, tone = _fit(job)
        badge = f'<span class="tag {tone}">{pct}</span>' if pct else ""
        out.append(
            f'<a class="trow" href="/jobs/{int(job["id"])}">'
            f'<span class="co">{esc((job.get("company") or "—")[:22])}</span>'
            f'<span class="ti">{esc((job.get("title") or "Untitled")[:70])}</span>'
            f'<span class="lo">{esc(_where(job))}</span>{badge}'
            f'<span class="go">{E.icon("arrow", 13, 2.5)}</span></a>'
        )
    return "".join(out)


# The dashboard's three working lists. Applied means you said yes to it, whether
# or not it has gone out yet: the same set policy.PolicyContext treats as "already
# applied", so a posting never sits in To apply and Applied at once.
APPLIED_STATUSES = ("approved", "sent", "responded")
_APP_TONE = {"sent": "good", "approved": "good", "responded": "good", "rejected": "bad", "drafted": ""}
_APPLIED_IN = "(" + ", ".join(f"'{s}'" for s in APPLIED_STATUSES) + ")"

_FREELANCE_IN = "(" + ", ".join(f"'{t}'" for t in policy.FREELANCE_TYPES) + ")"
_NOT_APPLIED = f"id NOT IN (SELECT job_id FROM applications WHERE job_id IS NOT NULL AND status IN {_APPLIED_IN})"

# Postings that got past the filters and that you have not applied to. A freelance gig is
# not a job to apply for, so it has its own list.
_TO_APPLY = f"status IN ('scored', 'tailored') AND IFNULL(employment_type, '') NOT IN {_FREELANCE_IN} AND {_NOT_APPLIED}"

# Gigs still open: a deadline that has passed takes one off.
_FREELANCE = (
    f"status IN ('scored', 'tailored') AND employment_type IN {_FREELANCE_IN} "
    f"AND (deadline IS NULL OR deadline >= date('now')) AND {_NOT_APPLIED}"
)


def _to_apply(conn: sqlite3.Connection, limit: int) -> tuple[list[dict[str, Any]], int]:
    rows = db.rows_to_dicts(
        conn.execute(
            f"SELECT * FROM jobs WHERE {_TO_APPLY} "  # noqa: S608 -- constants only
            "ORDER BY remote DESC, IFNULL(fit_score, -1) DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )
    total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {_TO_APPLY}").fetchone()[0]  # noqa: S608
    return rows, int(total)


def _freelance(conn: sqlite3.Connection, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Freelance and contract gigs that got past the filters: soonest deadline first,
    then by fit."""
    rows = db.rows_to_dicts(
        conn.execute(
            f"SELECT * FROM jobs WHERE {_FREELANCE} "  # noqa: S608 -- constants only
            "ORDER BY deadline IS NULL, deadline, IFNULL(fit_score, -1) DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )
    total = conn.execute(f"SELECT COUNT(*) FROM jobs WHERE {_FREELANCE}").fetchone()[0]  # noqa: S608
    return rows, int(total)


def _applied(conn: sqlite3.Connection, limit: int) -> tuple[list[dict[str, Any]], int]:
    rows = db.rows_to_dicts(
        conn.execute(
            f"SELECT * FROM applications WHERE status IN {_APPLIED_IN} "  # noqa: S608
            "ORDER BY COALESCE(sent_date, approved_at, '') DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    )
    total = conn.execute(
        f"SELECT COUNT(*) FROM applications WHERE status IN {_APPLIED_IN}"  # noqa: S608
    ).fetchone()[0]
    return rows, int(total)


def _upcoming(conn: sqlite3.Connection, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Competitions you can still enter, soonest deadline first, undated ones
    after the dated ones. Dismissed and closed ones never show."""
    rows = db.rows_to_dicts(
        conn.execute(
            "SELECT * FROM competitions WHERE status <> 'dismissed' ORDER BY id DESC LIMIT 400"
        ).fetchall()
    )
    still_open = sorted(
        (c for c in rows if _competition_order(c)[0] < 2), key=_competition_order
    )
    return still_open[:limit], len(still_open)


def _stack_row(href: str, title: str, meta: str, *, tag: str = "", external: bool = False) -> str:
    """A sidebar row: the title gets the width and `meta` (already escaped HTML)
    sits under it, because the sidebar is too narrow for the table-style row the
    Jobs page uses. An empty `href` makes it a plain, unlinked row."""
    body = f'<span class="ti"><b>{esc(title)}</b><small>{meta}</small></span>{tag}'
    if not href:
        return f'<div class="trow two">{body}</div>'
    icon = E.icon("external", 13, 2.2) if external else E.icon("arrow", 13, 2.5)
    blank = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return f'<a class="trow two" href="{esc(href)}"{blank}>{body}<span class="go">{icon}</span></a>'


def _to_apply_rows(jobs: Sequence[dict[str, Any]]) -> str:
    out = []
    for job in jobs:
        pct, tone = _fit(job)
        out.append(_stack_row(
            f"/jobs/{int(job['id'])}",
            (job.get("title") or "Untitled")[:70],
            esc(f"{(job.get('company') or '—')[:24]} · {_where(job)}"),
            tag=f'<span class="tag {tone}">{pct}</span>' if pct else "",
        ))
    return "".join(out)


def _freelance_rows(gigs: Sequence[dict[str, Any]]) -> str:
    out = []
    for gig in gigs:
        pay = f" · {gig['compensation']}" if gig.get("compensation") else ""
        due = _deadline_chip(gig["deadline"]) if gig.get("deadline") else ""
        out.append(_stack_row(
            f"/jobs/{int(gig['id'])}",
            (gig.get("title") or "Untitled")[:70],
            f"{esc((gig.get('company') or '—')[:22])}{esc(pay[:28])} {due}",
        ))
    return "".join(out)


def _applied_rows(apps: Sequence[dict[str, Any]]) -> str:
    return "".join(
        _stack_row(
            f"/applications/{int(a['id'])}",
            (a.get("role") or "Untitled")[:70],
            esc((a.get("company") or "—")[:30]),
            tag=f'<span class="tag {_APP_TONE.get(str(a.get("status")), "mute")}">'
                f'{esc(str(a.get("status") or ""))}</span>',
        )
        for a in apps
    )


def _upcoming_rows(competitions: Sequence[dict[str, Any]]) -> str:
    return "".join(
        _stack_row(
            c.get("apply_url") or c.get("url") or "",
            (c.get("name") or "Untitled")[:70],
            f"{_deadline_chip(c.get('deadline'))}{esc((c.get('category') or '')[:20])}",
            external=True,
        )
        for c in competitions
    )


def _arc_endpoints(country_rows: Sequence[tuple[str, float]]) -> tuple[tuple[float, float], tuple[float, float]]:
    """The two best-represented countries that have a centroid, so the arc on
    the globe connects places this data actually names."""
    found = [
        geo.COUNTRY_CENTROIDS[name]
        for name, _v in country_rows
        if name in geo.COUNTRY_CENTROIDS
    ]
    if len(found) >= 2:
        return found[0], found[1]
    if len(found) == 1:
        return found[0], FALLBACK_ARC[1]
    return FALLBACK_ARC


# --------------------------------------------------------------------------- dashboard


def dashboard(conn: sqlite3.Connection, config: Config) -> str:
    counts = _counts(conn)
    profile = db.profile_counts(conn)
    evidenced = len(db.skill_evidence_counts(conn))
    unevidenced = len(db.unevidenced_skills(conn))

    # Remote is the target, so in To apply a remote posting outranks an on-site
    # one at any fit rather than being interleaved with it.
    to_apply, to_apply_total = _to_apply(conn, 6)
    applied, applied_total = _applied(conn, 5)
    freelance, freelance_total = _freelance(conn, 6)
    upcoming, upcoming_total = _upcoming(conn, 6)

    by_country = pages._postings_by_country(conn)
    series = pages._sent_vs_discovered(conn, days=14)
    remote, onsite = pages._remote_split(conn)
    drafted = int(
        conn.execute(
            "SELECT COUNT(*) FROM applications WHERE status='drafted'"
        ).fetchone()[0]
    )
    sent = int(
        conn.execute("SELECT COUNT(*) FROM applications WHERE status='sent'").fetchone()[0]
    )
    delta = pages._week_over_week_delta(conn)

    # Config problems lead, above everything else. When there is nothing wrong
    # this renders nothing and the dashboard is exactly the reference layout --
    # but a pipeline that cannot send, or one that sends unattended, says so
    # before anything else on the page.
    def more(href: str, title: str) -> str:
        return f'<a class="sort-btn" href="{href}" title="{title}">{E.icon("arrow", 19)}</a>'

    # The three lists are what the dashboard is for, so they come before the search box.
    sidebar = (
        E.notices(pages._config_notices(config))
        + E.list_panel(
            title="To apply",
            sub=f"{to_apply_total:,} past your filters",
            rows=_to_apply_rows(to_apply) or '<div class="empty">Nothing is past your filters yet.</div>',
            tools=more("/jobs?status=scored&amp;scope=all", "Every posting past your filters"),
            stacked=True,
        )
        + E.list_panel(
            title="Applied",
            sub=f"{applied_total:,} approved or sent",
            rows=_applied_rows(applied) or '<div class="empty">Nothing applied to yet.</div>',
            tools=more("/resume", "All applications"),
            stacked=True,
        )
        + E.list_panel(
            title="Freelance & contract",
            sub=f"{freelance_total:,} open gigs",
            rows=_freelance_rows(freelance) or '<div class="empty">No open gigs yet.</div>',
            tools=more("/jobs?status=scored&amp;scope=all", "Every posting past your filters"),
            stacked=True,
        )
        + E.list_panel(
            title="Upcoming competitions",
            sub=f"{upcoming_total:,} still open",
            rows=_upcoming_rows(upcoming) or '<div class="empty">No open competitions tracked.</div>',
            tools=more("/competitions", "All competitions"),
            stacked=True,
        )
        + E.search_card(action="/jobs")
    )

    # --- the callout the reference used for a fare, showing the strongest match
    lead = to_apply[0] if to_apply else None
    if lead:
        pct, _tone = _fit(lead)
        fit_line = f"fit {pct}" if pct else "unscored"
        arc_card = (
            '<div class="arc-card"><div class="arc-top">'
            f'<span class="route">{esc((lead.get("company") or "—")[:16])}</span>'
            f'<span>{esc(fit_line)}</span></div><hr class="arc-hr">'
            '<div class="arc-bot">'
            f'<span class="seats">{esc((lead.get("title") or "Untitled")[:22])}</span>'
            f'<a class="book" href="/jobs/{int(lead["id"])}">Tailor</a>'
            "</div></div>"
        )
    else:
        arc_card = ""

    badge = f'<div class="plane-badge">{E.icon("pin", 22, 2)}</div>'

    # --- the aircraft slot: discovery vs sent, then the numbers under it
    chart = E.area_chart(
        [("Discovered", series["discovered"]), ("Sent", series["sent"])],
        series["days"],
    )
    delta_txt = f"{delta:+.0f}% wk/wk" if delta is not None else "—"
    panel = E.spec_panel(
        "Last 14 days",
        [
            ("Postings sourced", f"{counts['jobs']:,}"),
            ("Awaiting review", f"{drafted:,}"),
            ("Sent", f"{sent:,}"),
            ("Remote share", f"{(remote/((remote+onsite) or 1))*100:.0f}%"),
            ("Discovery", delta_txt),
        ],
        head=f'<div class="ac-photo" style="background:none;height:auto;padding:4px 2px 0">{chart}</div>',
    )

    zoom = (
        '<div class="zoom">'
        '<button id="zin"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>'
        '<button id="zout"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg></button>'
        '<button id="zfit"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/></svg></button>'
        '<div class="help">?</div></div>'
    )
    credit = (
        f'<div class="credit">{esc(str(profile.get("experiences", 0)))} POSITIONS · '
        f"{evidenced}/{evidenced + unevidenced} SKILLS EVIDENCED</div>"
    )

    a, b = _arc_endpoints(by_country)
    canvas = f'<canvas id="globe" {E.globe_attr(a, b)}></canvas>'
    return E.page(
        title="Dashboard",
        heading="Global Overview",
        sub=f"{counts['jobs']:,} postings across {len(by_country)} countries",
        active="",
        sidebar=sidebar,
        main=canvas + badge + arc_card + panel + zoom + credit,
        counts=counts,
    )


# --------------------------------------------------------------------------- jobs


def jobs(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    where: str = "",
    status: str | None = None,
    scope: str = "remote",
    group: str = "role",
) -> str:
    """Postings. Remote-first: `scope` defaults to remote-only, because that is
    what this search is actually for. `scope=all` opens it back up rather than
    hiding on-site work permanently."""
    counts = _counts(conn)
    remote_only = scope != "all"
    sql = "SELECT * FROM jobs"
    clauses, params = [], []
    if remote_only:
        clauses.append("remote = 1")
    if q:
        clauses.append("(title LIKE ? OR company LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if where:
        clauses.append("location LIKE ?")
        params.append(f"%{where}%")
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Remote-first ranking is what the remote-focused default is for. Applying
    # it to "All" as well would push every on-site posting past the 200-row cap,
    # so the toggle would look broken -- there it ranks on fit alone.
    order = "remote DESC, fit_score DESC" if remote_only else "fit_score DESC"
    sql += f" ORDER BY fit_score IS NULL, {order}, id DESC LIMIT 200"
    rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())
    total_remote = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE remote=1").fetchone()[0])

    status_rows = conn.execute(
        "SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC"
    ).fetchall()
    by_source = pages._by_source(conn)[:7]
    by_loc = pages._by_location(conn, limit=7)
    remote, onsite = pages._remote_split(conn)

    def keep(**over: str) -> str:
        parts = {"q": q, "where": where, "status": status or "", "scope": scope, "group": group}
        parts.update(over)
        return "/jobs?" + "&".join(f"{k}={esc(v)}" for k, v in parts.items() if v)

    grouped = group != "off"
    scope_toggle = (
        '<div class="seg">'
        f'<a class="{"on" if remote_only else ""}" href="{keep(scope="remote")}">Remote only</a>'
        f'<a class="{"" if remote_only else "on"}" href="{keep(scope="all")}">All</a>'
        "</div>"
        '<div class="seg">'
        f'<a class="{"on" if grouped else ""}" href="{keep(group="role")}">By role</a>'
        f'<a class="{"" if grouped else "on"}" href="{keep(group="off")}">Flat list</a>'
        "</div>"
    )
    sidebar = E.search_card(action="/jobs", q=q, where=where, extra=scope_toggle) + E.list_panel(
        title="Filter",
        sub="by pipeline status",
        rows="".join(
            f'<a class="trow" href="{keep(status=str(r["status"]))}">'
            f'<span class="ti">{esc(r["status"])}</span>'
            f'<span class="tag mute">{int(r["n"]):,}</span></a>'
            for r in status_rows
        ),
    )

    head = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(total_remote, "remote postings")
        + E.stat(len(rows), "matching this view")
        + E.stat(f"{(remote/((remote+onsite) or 1))*100:.0f}%", "of all postings are remote")
        + "</div>"
    )
    if not rows:
        listing = E.panel(
            "0 postings", '<div class="empty">Nothing matches that search.</div>', sub=""
        )
    elif grouped:
        listing = _grouped_job_panels(rows)
    else:
        listing = E.panel(
            f"{len(rows)} postings", _job_rows(rows), sub="ranked by fit, then newest"
        )

    body = (
        head
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("By source", E.bars(by_source), sub="where postings came from")
        + E.panel("By location", E.bars(by_loc), sub="top locations named")
        + "</div>"
        + listing
    )
    return E.page(
        title="Jobs",
        heading="Jobs",
        sub=(
            f"{total_remote:,} remote of {counts['jobs']:,} sourced · showing {len(rows)}"
            if remote_only
            else f"{counts['jobs']:,} sourced · showing {len(rows)}"
        ),
        active="jobs",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- competitions


def competitions(conn: sqlite3.Connection, *, q: str = "") -> str:
    counts = _counts(conn)
    sql = "SELECT * FROM competitions"
    params: list[Any] = []
    if q:
        sql += " WHERE name LIKE ? OR tracks LIKE ?"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id DESC LIMIT 400"
    rows = db.rows_to_dicts(conn.execute(sql, params).fetchall())

    rows.sort(key=_competition_order)
    rows = rows[:200]

    by_status = conn.execute(
        "SELECT status, COUNT(*) n FROM competitions GROUP BY status ORDER BY n DESC"
    ).fetchall()
    by_cat = [
        (str(r["category"]), float(r["n"]))
        for r in conn.execute(
            "SELECT category, COUNT(*) n FROM competitions GROUP BY category ORDER BY n DESC"
        ).fetchall()
    ]

    def row(c: dict[str, Any]) -> str:
        tone = "good" if (c.get("status") == "entered") else "mute"
        url = c.get("apply_url") or c.get("url") or ""
        tag = f'<span class="tag {tone}">{esc(c.get("status") or "")}</span>'
        open_link = (
            f'<span class="go">{E.icon("external", 13, 2.2)}</span>' if url else ""
        )
        # Both dates on the row itself. They answer different questions -- "by
        # when must I register" and "when does the thing actually run" -- and
        # having to open a row to find the first one is how a deadline is missed.
        inner = (
            f'<span class="co">{esc((c.get("category") or "")[:14])}</span>'
            f'<span class="ti">{esc((c.get("name") or "Untitled")[:52])}</span>'
            f"{_deadline_chip(c.get('deadline'))}"
            f"{_period_chip(c.get('period'))}"
            f"{tag}{open_link}"
        )
        if url:
            return f'<a class="trow" href="{esc(url)}" target="_blank" rel="noopener noreferrer">{inner}</a>'
        return f'<div class="trow">{inner}</div>'

    sidebar = E.search_card(action="/competitions", q=q) + E.list_panel(
        title="Status",
        sub="where each one stands",
        rows="".join(
            f'<div class="trow"><span class="ti">{esc(r["status"])}</span>'
            f'<span class="tag mute">{int(r["n"]):,}</span></div>'
            for r in by_status
        ),
    )
    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(counts["competitions"], "tracked")
        + E.stat(sum(1 for c in rows if c.get("deadline")), "with a deadline")
        + E.stat(sum(1 for c in rows if c.get("status") == "entered"), "entered")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("By category", E.bars(by_cat), sub="what kind of thing they are")
        + E.panel(
            "Discovered vs entered",
            E.donut(
                float(sum(1 for c in rows if c.get("status") == "entered")),
                float(sum(1 for c in rows if c.get("status") != "entered")),
                good_label="entered",
                rest_label="open",
            ),
            sub="of the ones on this page",
        )
        + "</div>"
        + E.panel(
            f"{len(rows)} competitions",
            "".join(row(c) for c in rows) or '<div class="empty">None tracked yet.</div>',
            sub="soonest deadline first",
        )
    )
    return E.page(
        title="Competitions",
        heading="Competitions",
        sub=f"{counts['competitions']:,} tracked",
        active="competitions",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- resume


def resume(conn: sqlite3.Connection) -> str:
    counts = _counts(conn)
    apps = db.list_applications(conn)
    drafted = [a for a in apps if a.get("status") == "drafted"]
    sent = [a for a in apps if a.get("status") == "sent"]
    funnel = pages._funnel(conn)

    def app_row(a: dict[str, Any]) -> str:
        score = a.get("fit_score")
        pct = f"{int(round(float(score)*100))}" if score not in (None, "") and float(score) <= 1 else ""
        tag = f'<span class="tag">{pct}</span>' if pct else ""
        return (
            f'<a class="trow" href="/applications/{int(a["id"])}">'
            f'<span class="co">{esc((a.get("company") or "—")[:20])}</span>'
            f'<span class="ti">{esc((a.get("role") or a.get("title") or "—")[:60])}</span>'
            f'<span class="lo">{esc(str(a.get("status") or ""))}</span>{tag}'
            f'<span class="go">{E.icon("arrow", 13, 2.5)}</span></a>'
        )

    sidebar = E.search_card(action="/jobs") + E.list_panel(
        title="Awaiting review",
        sub=f"{len(drafted)} drafted",
        rows="".join(app_row(a) for a in drafted[:8]),
    )
    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(len(apps), "applications")
        + E.stat(len(drafted), "awaiting review")
        + E.stat(len(sent), "sent")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Pipeline", E.bars(funnel), sub="how far postings get")
        + E.panel(
            "Review queue",
            "".join(app_row(a) for a in drafted[:10])
            or '<div class="empty">Nothing drafted right now.</div>',
            sub="tailored, not yet sent",
        )
        + "</div>"
        + E.panel(
            "Sent",
            "".join(app_row(a) for a in sent[:20])
            or '<div class="empty">Nothing has been sent.</div>',
            sub="applications that went out",
        )
    )
    return E.page(
        title="Resume",
        heading="Resume",
        sub=f"{len(drafted)} awaiting review · {len(sent)} sent",
        active="resume",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- profile


def profile(conn: sqlite3.Connection) -> str:
    counts = _counts(conn)
    prof = db.get_profile(conn)
    experiences = db.list_experiences(conn)
    education = db.list_education(conn)
    projects = db.list_projects(conn)
    skills = db.list_skills(conn)
    evidence = db.skill_evidence_counts(conn)
    profile_counts = db.profile_counts(conn)

    def when(row: dict[str, Any]) -> str:
        start = row.get("start_date") or ""
        end = "present" if row.get("is_current") else (row.get("end_date") or "")
        return " – ".join(x for x in (start, end) if x) or ""

    def headline(r: dict[str, Any]) -> str:
        """Each of these tables names its subject differently: a position has a
        `title`, a degree has a `degree` (plus field of study), a project has a
        `name`."""
        if r.get("title"):
            return str(r["title"])
        if r.get("degree"):
            field = r.get("field_of_study")
            return f"{r['degree']}, {field}" if field else str(r["degree"])
        return str(r.get("name") or "—")

    def tl(rows: Sequence[dict[str, Any]]) -> str:
        # `list_experiences`/`list_education`/`list_projects` all resolve the
        # organization name onto the row already, so there is nothing to look up.
        out = []
        for r in rows:
            org = r.get("organization")
            desc = (r.get("description") or "").strip()
            out.append(
                f'<div class="tl-item"><h4>{esc(headline(r))}</h4>'
                + (f'<div class="org">{esc(org)}</div>' if org else "")
                + f'<div class="when">{esc(when(r))}</div>'
                + (f"<p>{esc(desc[:260])}</p>" if desc else "")
                + "</div>"
            )
        return f'<div class="tl">{"".join(out)}</div>' if out else '<div class="empty">Nothing recorded.</div>'

    chips = "".join(
        f'<span class="chip{" ev" if evidence.get(s["id"]) else ""}">{esc(s.get("name") or "")}'
        + (f' · {evidence[s["id"]]}' if evidence.get(s["id"]) else "")
        + "</span>"
        for s in skills
    )

    # A skill with no evidence row cannot reach a resume -- `retrieval.py`
    # excludes it in code. Naming those here is the only way to know why a
    # skill you listed never shows up in a generated document.
    unevidenced = db.unevidenced_skills(conn)
    gap_notice = (
        E.notices(
            [
                {
                    "tone": "warn",
                    "text": f"{len(unevidenced)} skill(s) have no supporting record, "
                    "so they are locked out of every resume:",
                    "items": [str(s.get("name") or "") for s in unevidenced],
                }
            ]
        )
        if unevidenced
        else ""
    )

    sidebar = E.list_panel(
        title=esc(prof.get("full_name") or "Profile"),
        sub=esc(prof.get("headline") or prof.get("email") or ""),
        rows="".join(
            f'<div class="trow"><span class="ti">{esc(k)}</span>'
            f'<span class="lo">{esc(str(v)[:26])}</span></div>'
            for k, v in list(prof.items())[:10]
            if v
        ),
    )
    actions = (
        '<div class="frow" style="margin-bottom:16px">'
        '<a class="btn-sm pri" href="/profile/build">' + E.icon("plus", 14, 2.5)
        + " Add to profile</a>"
        '<a class="btn-sm" href="/profile/edit">Edit contact details</a>'
        '<a class="btn-sm" href="/review">Review imports</a>'
        "</div>"
    )
    body = (
        actions
        + '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(profile_counts.get("experiences", 0), "positions")
        + E.stat(profile_counts.get("achievements", 0), "accomplishments")
        + E.stat(f"{len(evidence)}/{len(skills)}", "skills evidenced")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Experience", tl(experiences), sub="positions held")
        + E.panel("Education", tl(education), sub="degrees")
        + "</div>"
        + '<div class="grid g2">'
        + E.panel("Projects", tl(projects), sub="things built")
        + E.panel(
            "Skills",
            gap_notice
            + (f'<div class="chips">{chips}</div>' if chips else '<div class="empty">No skills recorded.</div>'),
            sub="highlighted where evidence backs them",
        )
        + "</div>"
    )
    return E.page(
        title="Profile",
        heading=esc(prof.get("full_name") or "Profile"),
        sub=f"{profile_counts.get('experiences', 0)} positions · {len(evidence)} evidenced skills",
        active="profile",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- queue


def _app_row(a: dict[str, Any], *, href: str | None = None) -> str:
    score = a.get("fit_score")
    pct = ""
    if score not in (None, ""):
        f = float(score)
        pct = f"{int(round(f * 100))}" if f <= 1 else f"{int(round(f))}"
    tone = _APP_TONE.get(str(a.get("status")), "mute")
    link = href or f"/applications/{int(a['id'])}"
    return (
        f'<a class="trow" href="{esc(link)}">'
        f'<span class="co">{esc((a.get("company") or "—")[:20])}</span>'
        f'<span class="ti">{esc((a.get("role") or a.get("title") or "—")[:56])}</span>'
        f'<span class="tag {tone}">{esc(str(a.get("status") or ""))}</span>'
        + (f'<span class="tag">{pct}</span>' if pct else "")
        + f'<span class="go">{E.icon("arrow", 13, 2.5)}</span></a>'
    )


def queue(conn: sqlite3.Connection) -> str:
    """Applications waiting on a decision, strongest fit first."""
    counts = _counts(conn)
    apps = db.list_applications(conn)
    drafted = sorted(
        (a for a in apps if a.get("status") == "drafted"),
        key=lambda a: a.get("fit_score") or 0,
        reverse=True,
    )
    by_status: dict[str, int] = {}
    for a in apps:
        key = str(a.get("status") or "unknown")
        by_status[key] = by_status.get(key, 0) + 1

    sidebar = E.list_panel(
        title="Queue",
        sub=f"{len(drafted)} awaiting a decision",
        rows="".join(
            f'<div class="trow"><span class="ti">{esc(k)}</span>'
            f'<span class="tag mute">{v:,}</span></div>'
            for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])
        ),
    )
    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(len(apps), "applications")
        + E.stat(len(drafted), "awaiting review")
        + E.stat(by_status.get("sent", 0), "sent")
        + "</div>"
        + E.panel(
            "Awaiting review",
            "".join(_app_row(a) for a in drafted)
            or '<div class="empty">Nothing is waiting. Run the pipeline to tailor more.</div>',
            sub="approve or reject each one on its own page",
        )
    )
    return E.page(
        title="Queue",
        heading="Queue",
        sub=f"{len(drafted)} awaiting review",
        active="queue",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts={**counts, "queue": len(drafted)},
    )


# --------------------------------------------------------------------------- analytics


def analytics(conn: sqlite3.Connection) -> str:
    """Reach, activity and funnel on one page.

    `/reach`, `/funnel` and `/terminal` were three views of the same figures and
    all now serve this, so the section names those pages promised are the
    section names here -- nothing that was reachable before stopped being so.
    """
    counts = _counts(conn)
    by_source = pages._by_source(conn)[:8]
    by_loc = pages._by_location(conn, limit=8)
    by_country = pages._postings_by_country(conn)[:12]
    apps_country = pages._applications_by_country(conn)[:8]
    funnel_rows = pages._funnel(conn)
    fit_hist = pages._fit_histogram(conn)
    top_skills = pages._top_skills(conn)[:8]
    remote, onsite = pages._remote_split(conn)
    series = pages._sent_vs_discovered(conn, days=14)
    src_rows, band_cols, fit_cells = pages._fit_by_source(conn)

    empty = '<div class="empty">Nothing to plot yet.</div>'

    def or_empty(markup: str, rows: Sequence[Any]) -> str:
        return markup if rows else empty

    # Fit score by source, as a small grid: rows are sources, columns fit bands.
    if src_rows and band_cols:
        head = "".join('<span class="hm-h">' + esc(b) + "</span>" for b in band_cols)
        body_rows = []
        peak = max(
            (fit_cells.get((s, b), 0.0) for s in src_rows for b in band_cols),
            default=0.0,
        ) or 1.0
        for src in src_rows:
            cells = []
            for band in band_cols:
                v = fit_cells.get((src, band), 0.0)
                alpha = 0.06 + 0.85 * (v / peak)
                cells.append(
                    '<span class="hm-c" style="background:rgba(232,118,58,'
                    + "{:.2f}".format(alpha)
                    + ')" title="'
                    + esc(src)
                    + " / "
                    + esc(band)
                    + '">'
                    + ("{:,}".format(int(v)) if v else "")
                    + "</span>"
                )
            body_rows.append(
                '<div class="hm-r"><span class="hm-l">'
                + esc(str(src)[:16])
                + "</span>"
                + "".join(cells)
                + "</div>"
            )
        heatmap = (
            '<div class="hm"><div class="hm-r"><span class="hm-l"></span>'
            + head
            + "</div>"
            + "".join(body_rows)
            + "</div>"
        )
    else:
        heatmap = empty

    country_rows = "".join(
        '<div class="trow"><span class="ti">'
        + esc(name)
        + '</span><span class="tag mute">'
        + "{:,}".format(int(v))
        + "</span></div>"
        for name, v in by_country
    )

    sidebar = E.list_panel(
        title="Reach",
        sub="countries these postings name",
        rows=country_rows,
    )

    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(counts["jobs"], "postings")
        + E.stat(len(by_country), "countries named")
        + E.stat("{:.0f}%".format((remote / ((remote + onsite) or 1)) * 100), "remote")
        + "</div>"
        + E.panel(
            "Activity",
            or_empty(
                E.area_chart(
                    [("Discovered", series["discovered"]), ("Sent", series["sent"])],
                    series["days"],
                ),
                series["days"],
            ),
            sub="discovered vs sent, last 14 days",
        )
        + '<div class="grid g2" style="margin:16px 0">'
        + E.panel("Source reach", or_empty(E.bars(by_source), by_source), sub="which board found it")
        + E.panel("Top locations", or_empty(E.bars(by_loc), by_loc), sub="as the posting names it")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel(
            "Remote vs onsite",
            E.donut(remote, onsite, good_label="remote", rest_label="onsite")
            if (remote + onsite)
            else empty,
            sub="of every posting sourced",
        )
        + E.panel("Fit score by source", heatmap, sub="how well each board matches you")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Pipeline", or_empty(E.bars(funnel_rows), funnel_rows), sub="where postings stop")
        + E.panel("Fit distribution", or_empty(E.bars(fit_hist), fit_hist), sub="how well they match")
        + "</div>"
        + '<div class="grid g2">'
        + E.panel(
            "Applications by country",
            or_empty(E.bars(apps_country), apps_country),
            sub="where applications actually went",
        )
        + E.panel(
            "Most-wanted skills",
            or_empty(E.bars(top_skills), top_skills),
            sub="across sourced postings",
        )
        + "</div>"
    )
    return E.page(
        title="Analytics",
        heading="Analytics",
        sub="{:,} postings across {} countries".format(counts["jobs"], len(by_country)),
        active="analytics",
        sidebar=sidebar,
        main='<div class="main-scroll">' + body + "</div>",
        counts=counts,
    )


# --------------------------------------------------------------------------- runs


def runs(conn: sqlite3.Connection) -> str:
    counts = _counts(conn)
    rows = db.rows_to_dicts(
        conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 100").fetchall()
    )

    def run_row(r: dict[str, Any]) -> str:
        mode = str(r.get("mode") or "—")
        tone = "bad" if mode == "autonomous" else "mute"
        return (
            f'<div class="trow"><span class="co">#{int(r["id"])}</span>'
            f'<span class="ti">{esc(str(r.get("started_at") or "")[:19])}</span>'
            f'<span class="tag {tone}">{esc(mode)}</span>'
            f'<span class="lo">sourced {int(r.get("sourced") or 0):,} · '
            f'tailored {int(r.get("tailored") or 0):,} · sent {int(r.get("sent") or 0):,}</span>'
            "</div>"
        )

    totals = {k: sum(int(r.get(k) or 0) for r in rows) for k in ("sourced", "tailored", "sent")}
    sidebar = E.list_panel(
        title="Totals",
        sub=f"across {len(rows)} runs",
        rows="".join(
            f'<div class="trow"><span class="ti">{esc(k)}</span>'
            f'<span class="tag mute">{v:,}</span></div>'
            for k, v in totals.items()
        ),
    )
    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(len(rows), "runs recorded")
        + E.stat(totals["sourced"], "postings sourced")
        + E.stat(totals["sent"], "applications sent")
        + "</div>"
        + E.panel(
            "History",
            "".join(run_row(r) for r in rows)
            or '<div class="empty">No pipeline run has been recorded yet.</div>',
            sub="newest first",
        )
    )
    return E.page(
        title="Runs",
        heading="Runs",
        sub=f"{len(rows)} recorded",
        active="runs",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- answers


# --------------------------------------------------------------------------- ideas and to-dos


def publications(conn: sqlite3.Connection, token: str) -> str:
    counts = _counts(conn)
    rows = db.rows_to_dicts(conn.execute("SELECT * FROM publication_items ORDER BY id DESC").fetchall())
    tabs = [("publication", "Publications"), ("resource", "Resources"), ("idea", "Ideas")]
    def cards(items: list[dict[str, Any]]) -> str:
        if not items:
            return '<div class="empty">Nothing here yet.</div>'
        out = []
        for item in items:
            status = item.get("status") or "not_started"
            options = "".join(f'<option value="{s}"{" selected" if s == status else ""}>{label}</option>' for s, label in (("not_started", "Not started"), ("in_progress", "In progress"), ("completed", "Completed")))
            out.append(f'<div class="pub-card"><div><b>{esc(item["title"])}</b>' + (f'<a href="{esc(item["url"])}" target="_blank">Open link</a>' if item.get("url") else "") + f'</div><form method="post" action="/publications/{int(item["id"])}/status"><input type="hidden" name="token" value="{esc(token)}"><select name="status" onchange="this.form.submit()">{options}</select></form><form method="post" action="/publications/{int(item["id"])}/delete"><input type="hidden" name="token" value="{esc(token)}"><button class="todo-delete" type="submit" aria-label="Delete">{E.icon("trash", 15)}</button></form></div>')
        return ''.join(out)
    def panel(kind: str, title: str) -> str:
        body = cards([r for r in rows if r["kind"] == kind])
        form = E.form("/publications/add", token, f'<input type="hidden" name="kind" value="{kind}">' + E.field("Title", "title") + E.field("Link (optional)", "url", ph="https://"), f"Add {title[:-1]}", cls="capture-form")
        return E.panel(title, body + form)
    status_counts = {s: sum(1 for r in rows if r.get("status") == s) for s in ("not_started", "in_progress", "completed")}
    status_bar = '<div class="pub-status-bar">' + ''.join(f'<span><i class="status-dot {s}"></i>{label}<b>{status_counts[s]}</b></span>' for s, label in (("not_started", "Not started"), ("in_progress", "In progress"), ("completed", "Completed"))) + '</div>'
    body = status_bar + '<div class="pub-tabs">' + ''.join(f'<span class="sticky-tab {kind}">{title}</span>' for kind, title in tabs) + '</div><div class="grid g2">' + panel("publication", "Publications") + panel("resource", "Resources") + '</div>' + E.panel("Ideas", cards([r for r in rows if r["kind"] == "idea"]) + E.form("/publications/add", token, '<input type="hidden" name="kind" value="idea">' + E.field("Title", "title"), "Add Idea", cls="capture-form"), sub="Notes for future writing")
    sidebar = E.list_panel(title="Publications", sub=f"{len(rows)} tracked", rows=''.join(f'<div class="trow"><span class="ti">{t}</span><span class="tag">{sum(1 for r in rows if r["kind"] == k)}</span></div>' for k, t in tabs))
    return E.page(title="Publications", heading="Publications", sub="Track your work, resources, and ideas", active="publications", sidebar=sidebar, main=f'<div class="main-scroll">{body}</div>', counts=counts)


def todos(conn: sqlite3.Connection, token: str) -> str:
    """A small, persistent scratchpad: ideas on the left, actions on the right."""
    counts = _counts(conn)
    rows = db.rows_to_dicts(
        conn.execute(
            "SELECT id, kind, text, completed FROM todo_items "
            "ORDER BY completed ASC, id DESC"
        ).fetchall()
    )
    ideas = [row for row in rows if row["kind"] == "idea"]
    todos_ = [row for row in rows if row["kind"] == "todo"]

    def cards(items: list[dict[str, Any]], *, actionable: bool) -> str:
        if not items:
            return '<div class="empty">Nothing here yet.</div>'
        out = []
        for item in items:
            item_id = int(item["id"])
            done = bool(item["completed"])
            controls = ""
            if actionable:
                action_label = "Mark incomplete" if done else "Mark complete"
                controls += (
                    f'<form class="inline" method="post" action="/todos/{item_id}/toggle">'
                    f'<input type="hidden" name="token" value="{esc(token)}">'
                    f'<button class="todo-toggle" type="submit" aria-label="{action_label}">'
                    + E.icon("check" if done else "clock", 16) + "</button></form>"
                )
            controls += (
                f'<form class="inline" method="post" action="/todos/{item_id}/delete">'
                f'<input type="hidden" name="token" value="{esc(token)}">'
                '<button class="todo-delete" type="submit" aria-label="Delete">'
                + E.icon("trash", 15) + "</button></form>"
            )
            card_class = "idea-card done" if done else "idea-card"
            out.append(
                f'<div class="{card_class}"><p>{esc(item["text"])}</p>{controls}</div>'
            )
        return '<div class="idea-todo-list">' + "".join(out) + "</div>"

    def column(title: str, hint: str, kind: str, items: list[dict[str, Any]], *, actionable: bool) -> str:
        form = E.form(
            "/todos/add", token,
            f'<input type="hidden" name="kind" value="{kind}">'
            + E.textarea("", "text", rows=3),
            f"Add {title[:-1]}", cls="capture-form",
        )
        return E.panel(title, cards(items, actionable=actionable) + form, sub=hint)

    body = (
        '<div class="idea-todo-grid">'
        + '<div class="idea-todo-col">'
        + column("Ideas", "Capture thoughts worth returning to", "idea", ideas, actionable=False)
        + "</div><div class=\"idea-todo-col\">"
        + column("To Dos", "Keep the next actions in view", "todo", todos_, actionable=True)
        + "</div></div>"
    )
    sidebar = E.list_panel(
        title="Your workspace",
        sub="A place for thoughts and next actions",
        rows=(
            f'<div class="trow"><span class="ti">Ideas</span><span class="tag">{len(ideas)}</span></div>'
            f'<div class="trow"><span class="ti">To dos</span><span class="tag">{len(todos_)}</span></div>'
        ),
    )
    return E.page(
        title="Ideas & To Dos", heading="Ideas & To Dos",
        sub=f"{len(ideas)} ideas · {sum(not bool(i['completed']) for i in todos_)} open to dos",
        active="todos", sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>', counts=counts,
    )


# --------------------------------------------------------------------------- answers


def answers(conn: sqlite3.Connection, token: str) -> str:
    """The answer bank. Gaps first -- those are the questions that actually
    stopped an application, so the most expensive blank sits at the top."""
    counts = _counts(conn)
    stored = answer_bank.list_all(conn)
    gaps = answer_bank.gaps(conn)
    known = {a.pattern for a in stored}
    suggestions = [q for q in pages.COMMON_QUESTIONS if q[0] not in known]

    gap_rows = "".join(
        f'<div class="trow"><span class="ti">{esc(str(g.get("question") or ""))[:70]}</span>'
        + (f'<span class="lo">{esc(str(g.get("company")))}</span>' if g.get("company") else "")
        + f'<span class="tag bad">seen {int(g.get("seen_count") or 1)}</span></div>'
        for g in gaps
    )
    stored_rows = "".join(
        '<div class="trow">'
        f'<span class="ti">{esc(a.pattern[:44])}</span>'
        f'<span class="lo">{esc(a.answer[:40])}</span>'
        + (f'<span class="tag mute">{esc(a.company)}</span>' if a.company else "")
        + E.form(
            f"/answers/{int(a.id)}/delete",
            token,
            "",
            "Delete",
            cls="inline",
        )
        + "</div>"
        for a in stored
    )

    add_form = E.form(
        "/answers/add",
        token,
        E.field("Question or pattern", "pattern", ph="e.g. years of experience")
        + E.textarea("Answer", "answer", rows=3)
        + E.field("Only for company (optional)", "company"),
        "Store answer",
    )
    suggest_rows = "".join(
        E.form(
            "/answers/add",
            token,
            f'<input type="hidden" name="pattern" value="{esc(pattern)}">'
            f'<div class="field"><label>{esc(label)}</label>'
            f'<input name="answer" placeholder="{esc(hint)}"></div>',
            "Save",
            cls="suggest",
        )
        for pattern, label, hint in suggestions[:8]
    )

    sidebar = E.list_panel(
        title="Unanswered",
        sub=f"{len(gaps)} question(s) blocked an application",
        rows=gap_rows,
    )
    body = (
        '<div class="grid g3" style="margin-bottom:16px">'
        + E.stat(len(stored), "stored answers")
        + E.stat(len(gaps), "gaps")
        + E.stat(len(suggestions), "suggested")
        + "</div>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Add an answer", add_form, sub="used to fill application forms")
        + E.panel(
            "Stored",
            stored_rows or '<div class="empty">Nothing stored yet.</div>',
            sub="what forms fill from",
        )
        + "</div>"
        + E.panel(
            "Common questions",
            suggest_rows or '<div class="empty">All covered.</div>',
            sub="fill any of these in now and they stop being blockers",
        )
    )
    return E.page(
        title="Answers",
        heading="Answers",
        sub=f"{len(stored)} stored · {len(gaps)} still missing",
        active="answers",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- review


def review(conn: sqlite3.Connection, token: str) -> str:
    """Rows an import produced that a human has not confirmed. Only verified
    rows can be sent autonomously, so this is the gate for that."""
    counts = _counts(conn)
    sections: list[tuple[str, list[dict[str, Any]]]] = []
    for name in pages.REVIEWABLE:
        try:
            rows = db.rows_to_dicts(
                conn.execute(
                    f"SELECT * FROM {name} WHERE verified = 0 ORDER BY id"  # noqa: S608 - fixed allowlist
                ).fetchall()
            )
        except sqlite3.OperationalError:
            continue
        if rows:
            sections.append((name, rows))

    def label(row: dict[str, Any]) -> str:
        return str(
            row.get("title")
            or row.get("name")
            or row.get("description")
            or row.get("degree")
            or "(no label)"
        )

    panels = "".join(
        E.panel(
            name,
            "".join(
                '<div class="trow">'
                f'<span class="ti">{esc(label(r)[:70])}</span>'
                + E.form(
                    f"/review/{esc(name)}/{int(r['id'])}/verify",
                    token,
                    "",
                    "Confirm",
                    cls="inline",
                )
                + "</div>"
                for r in rows
            ),
            sub=f"{len(rows)} unconfirmed",
        )
        for name, rows in sections
    )
    total = sum(len(r) for _n, r in sections)
    sidebar = E.list_panel(
        title="Unconfirmed",
        sub=f"{total} row(s) across {len(sections)} tables",
        rows="".join(
            f'<div class="trow"><span class="ti">{esc(n)}</span>'
            f'<span class="tag mute">{len(r)}</span></div>'
            for n, r in sections
        ),
    )
    body = panels or E.panel(
        "Review",
        '<div class="empty">Nothing awaiting review. Every row has been confirmed.</div>',
        sub="imports land here until a human confirms them",
    )
    return E.page(
        title="Review",
        heading="Review",
        sub=f"{total} unconfirmed row(s)",
        active="profile",
        sidebar=sidebar,
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- detail pages


def job_detail(
    conn: sqlite3.Connection, job_id: int, config: Config, token: str
) -> str | None:
    job = db.get_row(conn, "jobs", job_id)
    if not job:
        return None
    counts = _counts(conn)
    existing = db.rows_to_dicts(
        conn.execute(
            "SELECT * FROM applications WHERE job_id = ? ORDER BY id DESC", (job_id,)
        ).fetchall()
    )
    pct, tone = _fit(job)

    def link(url: Any, text: str) -> str:
        if not url:
            return "—"
        return (
            f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer" '
            f'class="lnk">{esc(text)}</a>'
        )

    spec = E.kv(
        [
            ("Status", f'<span class="tag mute">{esc(job.get("status") or "new")}</span>'),
            ("Fit", f'<span class="tag {tone}">{pct}</span>' if pct else "not scored"),
            ("Remote", "Yes" if job.get("remote") else "No"),
            ("Location", esc(job.get("location") or "—")),
            ("Compensation", esc(job.get("compensation") or "—")),
            ("Source", esc(job.get("source") or "—")),
            ("Posted", esc(str(job.get("posted_at") or "—")[:19])),
            ("Discovered", esc(str(job.get("discovered_at") or "—")[:19])),
            ("Posting", link(job.get("url"), "open")),
            ("Apply", link(job.get("apply_url"), "open")),
        ]
    )
    if job.get("skip_reason"):
        spec += E.kv([("Skipped because", esc(job["skip_reason"]))])

    tailor = E.form(
        f"/jobs/{job_id}/tailor", token, "", "Tailor an application for this role"
    )
    # Applying happens on the employer's site, so this is how it gets recorded.
    if job.get("status") != "applied" and not any(
        a.get("status") in APPLIED_STATUSES for a in existing
    ):
        tailor += E.form(f"/jobs/{job_id}/applied", token, "", "I applied to this role")
    apps_panel = E.panel(
        "Applications",
        "".join(_app_row(a) for a in existing)
        or '<div class="empty">Nothing tailored for this posting yet.</div>',
        sub="drafts and sends against this posting",
    )
    body = (
        '<a class="crumb" href="/jobs">' + E.icon("arrow", 13, 2.5) + " Back to jobs</a>"
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Posting", f'<div class="spec-flat">{spec}</div>' + tailor)
        + apps_panel
        + "</div>"
        + E.panel(
            "Description",
            E.doc("job description", job.get("description") or "(none recorded)"),
            sub="as the board published it",
        )
    )
    return E.page(
        title=f"{job.get('title') or 'Job'}",
        heading=str(job.get("title") or "Untitled")[:60],
        sub=f"{job.get('company') or 'Unknown'} · {_where(job)}",
        active="jobs",
        sidebar=E.list_panel(
            title="This posting",
            sub=str(job.get("company") or ""),
            rows="".join(_app_row(a) for a in existing),
        ),
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


def application_detail(
    conn: sqlite3.Connection, app_id: int, token: str
) -> str | None:
    app = db.get_application(conn, app_id)
    if not app:
        return None
    counts = _counts(conn)

    reasons: list[str] = []
    raw = app.get("decision_reasons")
    if raw:
        try:
            parsed = json.loads(raw)
            reasons = [str(r) for r in parsed] if isinstance(parsed, list) else [str(parsed)]
        except (ValueError, TypeError):
            reasons = [str(raw)]

    documents = pages._read_bundle(app.get("resume_version"))
    grounding = str(app.get("grounding_status") or "?")
    status = str(app.get("status") or "")
    score = app.get("fit_score")
    fit_txt = f"{float(score):.1f}" if score not in (None, "") else "—"

    spec = E.kv(
        [
            ("Status", f'<span class="tag {"good" if status=="sent" else "mute"}">{esc(status)}</span>'),
            ("Fit score", esc(fit_txt)),
            (
                "Grounding",
                f'<span class="tag {"good" if grounding=="clean" else "bad"}">{esc(grounding)}</span>',
            ),
            ("Company", esc(app.get("company") or "—")),
            ("Role", esc(app.get("role") or "—")),
            ("Bundle", esc(app.get("resume_version") or "—")),
        ]
    )

    buttons = []
    if status == "drafted":
        buttons.append(E.form(f"/applications/{app_id}/approve", token, "", "Approve", cls="inline"))
    if status in ("drafted", "approved"):
        buttons.append(E.form(f"/applications/{app_id}/sent", token, "", "Mark as sent", cls="inline"))
    if status == "drafted":
        buttons.append(E.form(f"/applications/{app_id}/reject", token, "", "Reject", cls="inline dan"))
    decide = (
        '<div class="frow" style="margin-top:12px">' + "".join(buttons) + "</div>" if buttons else ""
    )

    why = (
        E.notices([{ "tone": "warn", "text": "Why the policy engine decided this:", "items": reasons}])
        if reasons
        else ""
    )
    docs = "".join(E.doc(name, text) for name, text in documents.items()) or (
        '<div class="empty">No generated documents found for this bundle.</div>'
    )

    body = (
        '<a class="crumb" href="/queue">' + E.icon("arrow", 13, 2.5) + " Back to queue</a>"
        + why
        + '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel("Application", f'<div class="spec-flat">{spec}</div>' + decide)
        + E.panel(
            "Decision",
            '<div class="empty">No reasons recorded.</div>' if not reasons else
            "".join(f'<div class="trow"><span class="ti">{esc(r)}</span></div>' for r in reasons),
            sub="why it was queued rather than sent",
        )
        + "</div>"
        + E.panel("Generated documents", docs, sub="exactly what would be sent")
    )
    return E.page(
        title=f"{app.get('role') or 'Application'}",
        heading=str(app.get("role") or "Application")[:60],
        sub=f"{app.get('company') or ''} · {status}",
        active="queue",
        sidebar=E.list_panel(
            title="Application",
            sub=str(app.get("company") or ""),
            rows=_app_row(app),
        ),
        main=f'<div class="main-scroll">{body}</div>',
        counts=counts,
    )


# --------------------------------------------------------------------------- profile builder

# Each add-form's fields match the names `App._add_entity` reads. Adding a field
# here without adding it there stores nothing, so the two move together.
ADD_FORMS: tuple[tuple[str, str, str], ...] = (
    ("experience", "Add a position", "Where you worked, and when."),
    ("achievement", "Add an accomplishment", "Something you did, under the position you did it in."),
    ("education", "Add a degree", "School, degree, field of study."),
    ("project", "Add a project", "Something you built."),
    ("certification", "Add a certification", "Issuer and date."),
    ("skill", "Add a skill", "Evidence comes from linking it to the work above."),
)


def _add_form(entity: str, token: str, experiences: Sequence[dict[str, Any]]) -> str:
    """One add-form per section, LinkedIn-style: fill it in, it appears."""
    if entity == "experience":
        fields = (
            E.field("Title", "title", ph="Software Engineer")
            + E.field("Organization", "org", ph="Acme Inc")
            + '<div class="fgrid">'
            + E.field("Employment type", "type", ph="full-time")
            + E.field("Location", "location", ph="Remote")
            + "</div>"
            + '<div class="fgrid">'
            + E.field("Start", "start", ph="2024-01")
            + E.field("End (blank = current)", "end", ph="2025-06")
            + "</div>"
            + E.field("Skills (comma separated)", "skills", ph="Python, SQL")
        )
    elif entity == "achievement":
        options = "".join(
            '<option value="{}">{}{}</option>'.format(
                int(x["id"]),
                esc(str(x.get("title") or "")[:40]),
                (" - " + esc(str(x.get("organization"))[:28])) if x.get("organization") else "",
            )
            for x in experiences
        )
        if not options:
            return (
                '<div class="empty">Add a position first &mdash; an accomplishment '
                "has to belong to one.</div>"
            )
        fields = (
            '<div class="field"><label for="f-experience_id">Under which position</label>'
            '<select id="f-experience_id" name="experience_id">' + options + "</select></div>"
            + E.field("What you did", "title", ph="Cut checkout latency in half")
            + E.textarea("Detail", "description", rows=3)
            + E.field("Measured impact", "impact", ph="p95 1.8s to 0.9s")
            + E.field("Skills (comma separated)", "skills")
        )
    elif entity == "education":
        fields = (
            E.field("Degree", "title", ph="B.Tech")
            + E.field("Institution", "org")
            + E.field("Field of study", "field", ph="Computer Science")
            + '<div class="fgrid">'
            + E.field("Start", "start")
            + E.field("End", "end")
            + "</div>"
        )
    elif entity == "project":
        fields = (
            E.field("Name", "title")
            + E.textarea("Description", "description", rows=3)
            + '<div class="fgrid">'
            + E.field("Your role", "role")
            + E.field("URL", "url")
            + "</div>"
            + E.field("Skills (comma separated)", "skills")
        )
    elif entity == "certification":
        fields = (
            E.field("Name", "title")
            + E.field("Issuer", "org")
            + '<div class="fgrid">'
            + E.field("Issued", "start")
            + E.field("URL", "url")
            + "</div>"
        )
    else:  # skill
        fields = E.field("Skill", "name") + E.field("Proficiency", "proficiency", ph="advanced")
    return E.form("/profile/" + entity + "/add", token, fields, "Add")


def profile_build(conn: sqlite3.Connection, token: str) -> str:
    """Add anything to the profile the way LinkedIn does: a form per section,
    with what is already recorded listed beside it and a way to remove it."""
    counts = _counts(conn)
    experiences = db.list_experiences(conn)
    education = db.list_education(conn)
    projects = db.list_projects(conn)
    skills = db.list_skills(conn)
    achievements = db.list_achievements(conn)
    try:
        certifications = db.rows_to_dicts(
            conn.execute("SELECT * FROM certifications ORDER BY id DESC").fetchall()
        )
    except sqlite3.OperationalError:
        certifications = []
    existing: dict[str, list[dict[str, Any]]] = {
        "experience": experiences,
        "achievement": achievements,
        "education": education,
        "project": projects,
        "certification": certifications,
        "skill": skills,
    }

    def label(entity: str, r: dict[str, Any]) -> str:
        if entity == "education":
            return str(r.get("degree") or "-")
        return str(r.get("title") or r.get("name") or "-")

    # Accomplishments hang off a position, so they are listed under the one
    # they belong to rather than in a flat list of their own -- a bullet with
    # no employer or dates around it is exactly what this schema forbids.
    by_parent: dict[int, list[dict[str, Any]]] = {}
    for a in achievements:
        parent = a.get("experience_id")
        if parent is not None:
            by_parent.setdefault(int(parent), []).append(a)

    EMPTY = {
        "experience": "No positions yet. Add the first one and accomplishments can hang off it.",
        "achievement": "No accomplishments yet. Add one under a position.",
        "education": "No degrees yet.",
        "project": "No projects yet.",
        "certification": "No certifications yet.",
        "skill": "No skills yet.",
    }

    def sub_rows(exp_id: int) -> str:
        kids = by_parent.get(exp_id) or []
        if not kids:
            return ""
        out = []
        for k in kids:
            impact = str(k.get("quantified_impact") or "").strip()
            out.append(
                '<div class="subrow"><span class="ti">'
                + esc(str(k.get("title") or "")[:60])
                + "</span>"
                + ('<span class="lo">' + esc(impact[:40]) + "</span>" if impact else "")
                + E.form(
                    "/profile/achievement/{}/delete".format(int(k["id"])),
                    token,
                    "",
                    "Remove",
                    cls="inline dan",
                )
                + "</div>"
            )
        return '<div class="subrows">' + "".join(out) + "</div>"

    def listing(entity: str) -> str:
        rows = existing.get(entity) or []
        if not rows:
            return '<div class="empty">' + EMPTY[entity] + "</div>"
        out = []
        for r in rows:
            org = r.get("organization")
            row = (
                '<div class="trow"><span class="ti">'
                + esc(label(entity, r)[:52])
                + "</span>"
                + ('<span class="lo">' + esc(str(org)[:22]) + "</span>" if org else "")
                + E.form(
                    "/profile/{}/{}/delete".format(entity, int(r["id"])),
                    token,
                    "",
                    "Remove",
                    cls="inline dan",
                )
                + "</div>"
            )
            if entity == "experience":
                row = '<div class="grp">' + row + sub_rows(int(r["id"])) + "</div>"
            elif entity == "achievement":
                impact = str(r.get("quantified_impact") or "").strip()
                if impact:
                    row = row[: -len("</div>")] + (
                        '<div class="subrows"><div class="subrow"><span class="lo">'
                        + esc(impact[:60])
                        + "</span></div></div></div>"
                    )
            out.append(row)
        return "".join(out)

    sections = "".join(
        '<div class="grid g2" style="margin-bottom:16px">'
        + E.panel(title, _add_form(entity, token, experiences), sub=hint)
        + E.panel(
            "Recorded",
            listing(entity),
            sub="{} on file".format(len(existing.get(entity) or [])),
        )
        + "</div>"
        for entity, title, hint in ADD_FORMS
    )

    sidebar = E.list_panel(
        title="Profile",
        sub="what the graph holds",
        rows="".join(
            '<div class="trow"><span class="ti">'
            + esc(entity)
            + '</span><span class="tag mute">'
            + "{:,}".format(len(rows))
            + "</span></div>"
            for entity, rows in existing.items()
        ),
    )
    back = '<a class="crumb" href="/profile">' + E.icon("arrow", 13, 2.5) + " Back to profile</a>"
    return E.page(
        title="Add to profile",
        heading="Build your profile",
        sub="everything a generated resume is drawn from",
        active="profile",
        sidebar=sidebar,
        main='<div class="main-scroll">' + back + sections + "</div>",
        counts=counts,
    )


def profile_edit(conn: sqlite3.Connection, token: str) -> str:
    """The contact details an application form asks for."""
    counts = _counts(conn)
    prof = db.get_profile(conn)
    fields = "".join(
        E.field(label, name, prof.get(name, ""), kind=kind, ph=hint)
        for name, label, kind, hint in pages.PROFILE_FIELDS
    )
    main_form = E.form("/profile/save", token, fields, "Save")
    extra = E.form(
        "/profile/save",
        token,
        E.field("Field name", "key", ph="notice_period") + E.field("Value", "value"),
        "Add field",
    )
    known = {name for name, _l, _k, _h in pages.PROFILE_FIELDS}
    others = "".join(
        '<div class="trow"><span class="ti">'
        + esc(k)
        + '</span><span class="lo">'
        + esc(str(v)[:34])
        + "</span>"
        + E.form("/profile/attr/" + esc(k) + "/delete", token, "", "Remove", cls="inline dan")
        + "</div>"
        for k, v in prof.items()
        if k not in known and k != "updated_at" and v
    )
    sidebar = E.list_panel(
        title=str(prof.get("full_name") or "Profile"),
        sub=str(prof.get("headline") or ""),
        rows="".join(
            '<div class="trow"><span class="ti">'
            + esc(k)
            + '</span><span class="lo">'
            + esc(str(v)[:24])
            + "</span></div>"
            for k, v in prof.items()
            if v and k != "updated_at"
        ),
    )
    back = '<a class="crumb" href="/profile">' + E.icon("arrow", 13, 2.5) + " Back to profile</a>"
    body = (
        back
        + '<div class="grid g2">'
        + E.panel("Contact details", main_form, sub="typed straight into application forms")
        + E.panel(
            "Anything else",
            extra + (others or '<div class="empty">No extra fields.</div>'),
            sub="custom fields a form might ask for",
        )
        + "</div>"
    )
    return E.page(
        title="Edit profile",
        heading="Edit profile",
        sub="contact details used on applications",
        active="profile",
        sidebar=sidebar,
        main='<div class="main-scroll">' + body + "</div>",
        counts=counts,
    )
