"""Starting a pipeline run when asked, and saying where the last one stands.

A run is `python -m jobsearch run`, the same thing the timer on the server starts at
12:00 GMT each day. This module only starts that process detached from whoever asked (so
restarting the website does not kill a run, and a run that crashes cannot take the website
with it) and reads `pipeline_runs` back for the dashboard and for the apps.

Two runs must not overlap: both would tailor, and with autonomous on both would apply, to
the same postings. A run records itself first and then stops if an earlier one has not
finished (see `other_in_progress`), so the guard holds whoever starts them: the timer, a
button, or a person at a terminal on any machine that shares the database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import worklists
from .config import Config

# A run that has not finished after this long is taken to have died. The longest one yet
# was minutes, but tailoring and applying to a few dozen postings can take an hour or two.
STALE_AFTER = timedelta(hours=4)


def _utc(value: str | None) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def last_run(conn: Any) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def other_in_progress(conn: Any, own_id: int, now: datetime | None = None) -> dict[str, Any] | None:
    """An earlier run that has not finished and is not yet taken for dead.

    Earlier means a lower id, so when two start in the same moment the first to record
    itself wins and the other stops, rather than both stopping.
    """
    cutoff = ((now or datetime.now(timezone.utc)) - STALE_AFTER).replace(microsecond=0).isoformat()
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE id < ? AND finished_at IS NULL AND started_at > ? "
        "ORDER BY id LIMIT 1",
        (own_id, cutoff),
    ).fetchone()
    return dict(row) if row else None


def running(conn: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """The run in progress, if there is one."""
    cutoff = ((now or datetime.now(timezone.utc)) - STALE_AFTER).replace(microsecond=0).isoformat()
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE finished_at IS NULL AND started_at > ? "
        "ORDER BY id DESC LIMIT 1",
        (cutoff,),
    ).fetchone()
    return dict(row) if row else None


def next_run_at(run_at: str, now: datetime | None = None) -> datetime:
    """The next time the clock reads `run_at` (HH:MM, GMT) after `now`."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    hour, minute = (int(part) for part in run_at.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if due > now else due + timedelta(days=1)


def status(conn: Any, config: Config, now: datetime | None = None) -> dict[str, Any]:
    """Everything an app needs to show: is it running, how the last run went, when the next
    one is, what is left to do and what is done, and whether anything is sent unattended."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last = last_run(conn)
    live = running(conn, now)
    keep = ("id", "started_at", "finished_at", "mode", "sourced", "scored", "tailored", "queued", "sent", "skipped", "errors")
    return {
        "running": live is not None,
        "run": {k: (live or last or {}).get(k) for k in keep} if (live or last) else None,
        "last_finished": (last or {}).get("finished_at") if last else None,
        "next_run_at": next_run_at(config.schedule.run_at, now).replace(microsecond=0).isoformat(),
        "run_at": config.schedule.run_at,
        "timezone": "GMT",
        "auto_apply": bool(config.autonomous),
        "counts": worklists.counts(conn),
        "caps": {
            "tailor_per_run": config.limits.max_tailor_per_run,
            "apply_per_run": config.limits.max_applications_per_run,
            "apply_per_day": config.limits.max_applications_per_day,
            "per_company_per_week": config.limits.max_per_company_per_week,
        },
    }


def start(project_root: Path, *, db_path: str | os.PathLike[str] | None = None) -> subprocess.Popen:
    """Begin a run in its own process, appending its output to output/run-latest.log."""
    log_dir = Path(project_root) / "output"
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "jobsearch", "run"]
    if db_path:
        command += ["--db", str(db_path)]
    kwargs: dict[str, Any] = {
        "cwd": str(project_root),
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with open(log_dir / "run-latest.log", "ab") as log:
        process = subprocess.Popen(command, stdout=log, **kwargs)  # noqa: S603 -- our own interpreter and module
    # Reap it when it ends; nobody else will.
    threading.Thread(target=process.wait, daemon=True).start()
    return process
