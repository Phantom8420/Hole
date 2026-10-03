"""Job sourcing: config in, `Posting` objects out.

Only ToS-safe endpoints live here. There is no Indeed connector (no public read
API, scraping blocked) and no LinkedIn connector (automated access banned). Both
omissions are deliberate and neither should be added.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..config import Config
from . import aggregators, ats_boards, internship_lists, remote_boards
from .base import STUB_MARKER, Posting, SourceError, SourceResult, dedupe, html_to_text  # noqa: F401

__all__ = [
    "Posting",
    "SourceError",
    "SourceResult",
    "SourcingReport",
    "collect",
    "store",
    "sync_open",
    "dedupe",
    "html_to_text",
]


@dataclass
class SourcingReport:
    postings: list[Posting] = field(default_factory=list)
    per_source: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # For `sync_open`: the sources that listed everything they have open, the ids
    # they listed, and the ids a source says have closed.
    complete: set[str] = field(default_factory=set)
    seen: dict[str, set[str]] = field(default_factory=dict)
    closed: dict[str, set[str]] = field(default_factory=dict)

    def absorb(self, result: SourceResult) -> None:
        self.postings.extend(result.postings)
        self.per_source[result.source] = self.per_source.get(result.source, 0) + len(result.postings)
        self.errors.extend(result.errors)
        if result.complete and not result.errors:
            self.complete.add(result.source)
            self.seen.setdefault(result.source, set()).update(str(p.external_id) for p in result.postings)
        if result.closed_ids:
            self.closed.setdefault(result.source, set()).update(str(i) for i in result.closed_ids)


def collect(config: Config, *, known: dict[str, set[str]] | None = None) -> SourcingReport:
    """Run every enabled connector. A failing source never stops the others.

    `known` maps a source name to the ids already stored for it, for the sources
    (the internship lists) that would otherwise fetch the same posting's text on
    every run.
    """
    report = SourcingReport()
    search = config.search
    keyword_query = " ".join(search.titles[:3] or search.keywords[:3])
    location_query = search.locations[0] if search.locations else ""

    for source in config.enabled_sources():
        try:
            if source.name == "greenhouse":
                report.absorb(ats_boards.fetch_greenhouse(source.list_of("boards")))
            elif source.name == "lever":
                report.absorb(ats_boards.fetch_lever(source.list_of("companies")))
            elif source.name == "ashby":
                report.absorb(ats_boards.fetch_ashby(source.list_of("boards")))
            elif source.name == "adzuna":
                report.absorb(
                    aggregators.fetch_adzuna(
                        app_id=str(source.get("app_id", "")),
                        app_key=str(source.get("app_key", "")),
                        country=str(source.get("country", "us")),
                        what=keyword_query,
                        where=location_query,
                        results_per_page=int(source.get("results_per_page", 50)),
                        max_pages=int(source.get("max_pages", 2)),
                        max_age_days=search.max_age_days,
                    )
                )
            elif source.name == "simplify":
                report.absorb(
                    internship_lists.fetch_simplify(
                        search,
                        terms=source.list_of("terms"),
                        categories=source.list_of("categories"),
                        degrees=source.list_of("degrees"),
                        exclude_sponsorship=source.list_of("exclude_sponsorship"),
                        limit=int(source.get("limit", 300)),
                        known=(known or {}).get("simplify", ()),
                    )
                )
            elif source.name == "usajobs":
                report.absorb(
                    aggregators.fetch_usajobs(
                        email=str(source.get("email", "")),
                        api_key=str(source.get("api_key", "")),
                        keyword=keyword_query,
                        location=location_query,
                        results_per_page=int(source.get("results_per_page", 50)),
                    )
                )
            elif source.name in remote_boards.REMOTE_BOARDS:
                # The aggregator boards take no credentials and no per-company
                # slug -- they are one endpoint each, so enabling one is just
                # naming it.
                options: dict[str, Any] = {"limit": int(source.get("limit", 100))}
                if source.name == "arbeitnow":
                    # Most of this board is on-site and in German-speaking Europe.
                    options["remote_only"] = bool(source.get("remote_only", True))
                report.absorb(remote_boards.REMOTE_BOARDS[source.name](**options))
            else:
                report.errors.append(f"Unknown source '{source.name}' in config -- ignored.")
        except Exception as exc:  # a broken connector must not end the run
            report.errors.append(f"{source.name}: {type(exc).__name__}: {exc}")

    report.postings = dedupe(report.postings)
    return report


def store(conn: sqlite3.Connection, postings: list[Posting]) -> tuple[int, int]:
    """Insert new postings, ignoring ones already seen. Returns (new, duplicates)."""
    discovered_at = db.now()
    new = 0
    duplicates = 0
    # Not "insert and catch sqlite3.IntegrityError": Turso answers a duplicate with
    # an ordinary reply that its client library trips over (a KeyError), so the
    # first posting already stored used to end the whole run. OR IGNORE cannot
    # raise, and one lookup saves a round trip for every known posting.
    known = {row["fingerprint"] for row in conn.execute("SELECT fingerprint FROM jobs")}
    for posting in postings:
        row = posting.to_row(discovered_at)
        if row["fingerprint"] in known or not db.insert_row(conn, "jobs", row, or_ignore=True):
            duplicates += 1
        else:
            known.add(row["fingerprint"])
            new += 1
    return new, duplicates


# If a source now lists fewer than this share of what is stored as open, it is far more
# likely to be an outage or an empty answer than every posting closing at once.
MIN_STILL_LISTED = 0.5


def sync_open(conn: sqlite3.Connection, report: SourcingReport) -> tuple[int, int]:
    """Close what the sources no longer list, and reopen what came back.

    Only for a source that listed everything it has open without a failure, or that
    named the closed ones itself: a posting missing from a partial answer is not
    closed. Only 'new' and 'scored' postings close (nothing to tell the user about a
    skipped one, and a posting with a draft is theirs to look at). Returns
    (closed, reopened).
    """
    gone: list[int] = []
    back: list[int] = []
    for source in sorted(report.complete):
        seen = report.seen.get(source, set())
        rows = conn.execute(
            "SELECT id, external_id, status FROM jobs WHERE source = ?", (source,)
        ).fetchall()
        live = [r for r in rows if r["status"] != "closed"]
        if live and len(seen) < len(live) * MIN_STILL_LISTED:
            continue
        gone += [
            int(r["id"]) for r in live
            if r["status"] in ("new", "scored") and str(r["external_id"]) not in seen
        ]
        back += [
            int(r["id"]) for r in rows
            if r["status"] == "closed" and str(r["external_id"]) in seen
        ]
    for source, ids in report.closed.items():
        rows = conn.execute(
            "SELECT id, external_id FROM jobs WHERE source = ? AND status IN ('new', 'scored')",
            (source,),
        ).fetchall()
        gone += [int(r["id"]) for r in rows if str(r["external_id"]) in ids]
    if gone:
        conn.executemany(
            "UPDATE jobs SET status = 'closed', skip_reason = 'no longer listed by the source' "
            "WHERE id = :id",
            [{"id": i} for i in dict.fromkeys(gone)],
        )
    if back:
        conn.executemany(
            "UPDATE jobs SET status = 'new', skip_reason = NULL, fit_score = NULL WHERE id = :id",
            [{"id": i} for i in dict.fromkeys(back)],
        )
    return len(set(gone)), len(set(back))


def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int | None = None,
    order: str = "IFNULL(fit_score, -1) DESC, id DESC",
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jobs"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += f" ORDER BY {order}"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return db.rows_to_dicts(conn.execute(sql, params))
