"""The lists the work is tracked in: to apply, applied, freelance.

One definition, read by the dashboard and by the status the apps ask for, so the numbers
they show cannot drift apart. Applied means you said yes to it, whether or not it has
gone out yet, so a posting never sits in To apply and Applied at once. (The run is
stricter: policy.PolicyContext also counts a draft as an application for the role, so it
never writes a second one.)
"""

from __future__ import annotations

import sqlite3

from . import policy

APPLIED_STATUSES = ("approved", "sent", "responded")
APPLIED_IN = "(" + ", ".join(f"'{s}'" for s in APPLIED_STATUSES) + ")"
FREELANCE_IN = "(" + ", ".join(f"'{t}'" for t in policy.FREELANCE_TYPES) + ")"
NOT_APPLIED = (
    f"id NOT IN (SELECT job_id FROM applications WHERE job_id IS NOT NULL AND status IN {APPLIED_IN})"
)

# Postings that got past the filters and that you have not applied to. A freelance gig is
# not a job to apply for, so it has its own list. 'closed' postings are in neither.
TO_APPLY = (
    f"status IN ('scored', 'tailored') AND IFNULL(employment_type, '') NOT IN {FREELANCE_IN} "
    f"AND {NOT_APPLIED}"
)

# Gigs still open: a deadline that has passed takes one off.
FREELANCE = (
    f"status IN ('scored', 'tailored') AND employment_type IN {FREELANCE_IN} "
    f"AND (deadline IS NULL OR deadline >= date('now')) AND {NOT_APPLIED}"
)


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """What is left to do and what is done."""
    return {
        # still open and not applied to: what the run has yet to get to, or you have to
        "remaining": _count(conn, f"SELECT COUNT(*) FROM jobs WHERE {TO_APPLY}"),  # noqa: S608
        # tailored and waiting for a yes
        "drafted": _count(conn, "SELECT COUNT(*) FROM applications WHERE status = 'drafted'"),
        # said yes to, sent or not
        "applied": _count(conn, f"SELECT COUNT(*) FROM applications WHERE status IN {APPLIED_IN}"),  # noqa: S608
        "sent": _count(conn, "SELECT COUNT(*) FROM applications WHERE status IN ('sent', 'responded')"),
        "freelance": _count(conn, f"SELECT COUNT(*) FROM jobs WHERE {FREELANCE}"),  # noqa: S608
    }
