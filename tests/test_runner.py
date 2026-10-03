"""The run controller: the next 12:00 GMT, which runs are live, what status says, how a run starts.

No network and no real subprocess: Popen is replaced where a run would start.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db, runner, worklists  # noqa: E402
from jobsearch.config import Config, _clock  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


class NextRunTests(unittest.TestCase):
    def test_today_if_the_time_has_not_come_tomorrow_if_it_has(self) -> None:
        self.assertEqual(runner.next_run_at("12:00", NOW), datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
        late = datetime(2026, 10, 3, 13, 30, tzinfo=UTC)
        self.assertEqual(runner.next_run_at("12:00", late), datetime(2026, 10, 4, 12, 0, tzinfo=UTC))
        on_the_dot = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)
        self.assertEqual(runner.next_run_at("12:00", on_the_dot), datetime(2026, 10, 4, 12, 0, tzinfo=UTC))

    def test_the_clock_is_gmt_whatever_zone_asks(self) -> None:
        ist = timezone(timedelta(hours=5, minutes=30))
        five_pm_in_india = datetime(2026, 10, 3, 17, 0, tzinfo=ist)  # 11:30 GMT
        self.assertEqual(runner.next_run_at("12:00", five_pm_in_india), datetime(2026, 10, 3, 12, 0, tzinfo=UTC))

    def test_the_configured_time_is_tidied_or_defaulted(self) -> None:
        for raw, clean in {"12:00": "12:00", "9:30": "09:30", "00:05": "00:05", " 18:45 ": "18:45"}.items():
            with self.subTest(raw):
                self.assertEqual(_clock(raw), clean)
        for bad in ("24:00", "12:60", "noon", "12", "12:5", "", None, "-1:00"):
            with self.subTest(bad):
                self.assertEqual(_clock(bad), "12:00")
        self.assertEqual(Config.from_dict({}).schedule.run_at, "12:00")
        self.assertEqual(Config.from_dict({"schedule": {"run_at": "6:15"}}).schedule.run_at, "06:15")


class RunCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "t.db"
        self.conn = db.connect(self.path)
        self.addCleanup(self.conn.close)

    def run_row(self, started: datetime, finished: bool = False, **fields: object) -> int:
        row = {"started_at": started.replace(microsecond=0).isoformat(), "mode": "review-only", **fields}
        if finished:
            row["finished_at"] = (started + timedelta(minutes=5)).replace(microsecond=0).isoformat()
        run_id = db.insert_row(self.conn, "pipeline_runs", row)
        self.conn.commit()
        return run_id


class RunBookkeepingTests(RunCase):
    def test_an_earlier_unfinished_run_is_in_progress_until_it_is_taken_for_dead(self) -> None:
        now = datetime.now(UTC)
        first = self.run_row(now - timedelta(minutes=30))
        second = self.run_row(now)
        self.assertEqual(runner.other_in_progress(self.conn, second, now)["id"], first)
        self.assertIsNone(runner.other_in_progress(self.conn, first, now))  # only an earlier one counts
        # five hours on, the first is taken to have died
        later = now + timedelta(hours=5)
        self.assertIsNone(runner.other_in_progress(self.conn, second, later))

    def test_a_finished_run_blocks_nothing(self) -> None:
        now = datetime.now(UTC)
        self.run_row(now - timedelta(minutes=30), finished=True)
        second = self.run_row(now)
        self.assertIsNone(runner.other_in_progress(self.conn, second, now))
        self.assertEqual(runner.running(self.conn, now)["id"], second)  # only the one just begun

    def test_running_is_the_latest_unfinished_recent_run(self) -> None:
        now = datetime.now(UTC)
        self.assertIsNone(runner.running(self.conn, now))
        self.run_row(now - timedelta(hours=6))  # long dead
        self.assertIsNone(runner.running(self.conn, now))
        live = self.run_row(now - timedelta(minutes=2))
        self.assertEqual(runner.running(self.conn, now)["id"], live)

    def test_timestamps_without_a_zone_are_read_as_gmt(self) -> None:
        self.assertEqual(runner._utc("2026-10-03T12:00:00"), datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
        self.assertEqual(runner._utc("2026-10-03T12:00:00+00:00"), datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
        self.assertIsNone(runner._utc("yesterday"))
        self.assertIsNone(runner._utc(None))


class StatusTests(RunCase):
    def add_job(self, title: str, **fields: object) -> int:
        job_id = db.insert_row(self.conn, "jobs", {
            "source": "greenhouse", "company": "Acme", "title": title, "location": "Remote", "remote": 1,
            "url": f"https://example.com/{title}", "description": "x", "discovered_at": db.now(),
            "fingerprint": f"fp-{title}", "status": "scored", "fit_score": 20.0, **fields,
        })
        self.conn.commit()
        return job_id

    def application(self, job_id: int, status: str) -> None:
        db.insert_application(self.conn, {"job_id": job_id, "company": "Acme", "role": "x", "status": status})
        self.conn.commit()

    def test_nothing_has_run_yet(self) -> None:
        info = runner.status(self.conn, Config(), NOW)
        self.assertFalse(info["running"])
        self.assertIsNone(info["run"])
        self.assertEqual(info["next_run_at"], "2026-10-03T12:00:00+00:00")
        self.assertEqual((info["run_at"], info["timezone"], info["auto_apply"]), ("12:00", "GMT", False))
        self.assertEqual(info["counts"], {"remaining": 0, "drafted": 0, "applied": 0, "sent": 0, "freelance": 0})

    def test_the_last_run_and_the_counts(self) -> None:
        self.run_row(NOW - timedelta(hours=20), finished=True, sourced=40, tailored=8, sent=0, errors=1)
        a, b = self.add_job("A"), self.add_job("B")
        self.add_job("C")
        self.add_job("Closed", status="closed")
        self.add_job("Gig", employment_type="freelance")
        self.application(a, "drafted")
        self.application(b, "sent")
        info = runner.status(self.conn, Config(), NOW)
        self.assertFalse(info["running"])
        self.assertEqual((info["run"]["sourced"], info["run"]["tailored"], info["run"]["errors"]), (40, 8, 1))
        self.assertEqual(info["counts"], {"remaining": 2, "drafted": 1, "applied": 1, "sent": 1, "freelance": 1})

    def test_a_run_in_progress_is_reported_as_the_run(self) -> None:
        live = self.run_row(datetime.now(UTC) - timedelta(minutes=3))
        info = runner.status(self.conn, Config(), datetime.now(UTC))
        self.assertTrue(info["running"])
        self.assertEqual(info["run"]["id"], live)

    def test_auto_apply_and_the_caps_are_reported(self) -> None:
        config = Config.from_dict({"autonomous": True, "limits": {"max_applications_per_day": 30}})
        info = runner.status(self.conn, config, NOW)
        self.assertTrue(info["auto_apply"])
        self.assertEqual(info["caps"]["apply_per_day"], 30)
        self.assertEqual(
            set(info["caps"]), {"tailor_per_run", "apply_per_run", "apply_per_day", "per_company_per_week"}
        )


class StartTests(unittest.TestCase):
    def test_a_run_starts_detached_in_the_project_with_its_output_in_a_log(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with mock.patch.object(runner.subprocess, "Popen") as popen, \
                    mock.patch.object(runner.threading, "Thread") as thread:
                process = runner.start(Path(tmp), db_path="x.db")
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [sys.executable, "-m", "jobsearch", "run", "--db", "x.db"])
            self.assertEqual(kwargs["cwd"], tmp)
            self.assertIs(kwargs["stdin"], runner.subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], runner.subprocess.STDOUT)
            self.assertTrue((Path(tmp) / "output" / "run-latest.log").exists())
            # detached one way or the other, and reaped
            self.assertEqual(("start_new_session" in kwargs) + ("creationflags" in kwargs), 1)
            thread.assert_called_once()
            self.assertIs(process, popen.return_value)

    def test_no_db_flag_when_the_default_database_is_meant(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with mock.patch.object(runner.subprocess, "Popen") as popen, mock.patch.object(runner.threading, "Thread"):
                runner.start(Path(tmp))
            self.assertEqual(popen.call_args[0][0], [sys.executable, "-m", "jobsearch", "run"])


class WorklistsTests(RunCase):
    def test_a_posting_is_in_one_list_at_a_time(self) -> None:
        def job(title: str, **fields: object) -> int:
            job_id = db.insert_row(self.conn, "jobs", {
                "source": "x", "company": "Acme", "title": title, "discovered_at": db.now(),
                "fingerprint": f"fp-{title}", "status": "scored", **fields,
            })
            self.conn.commit()
            return job_id

        plain, applied, gig, old_gig = job("plain"), job("applied"), job("gig", employment_type="freelance"), job(
            "oldgig", employment_type="contract", deadline="2020-01-01")
        db.insert_application(self.conn, {"job_id": applied, "company": "Acme", "role": "applied", "status": "approved"})
        self.conn.commit()
        ids = lambda where: {r["id"] for r in self.conn.execute(f"SELECT id FROM jobs WHERE {where}")}  # noqa: E731, S608
        self.assertEqual(ids(worklists.TO_APPLY), {plain})
        self.assertEqual(ids(worklists.FREELANCE), {gig})
        self.assertNotIn(old_gig, ids(worklists.FREELANCE))
        self.assertEqual(worklists.counts(self.conn)["applied"], 1)


if __name__ == "__main__":
    unittest.main()
