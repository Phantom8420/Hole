"""Tests for the local review UI.

No sockets. `App` holds the routing and the actions, so every route can be
driven directly; the HTTP layer around it is thin enough to read.

The escaping tests matter most. Job descriptions arrive from public boards and
model output is not trusted either, so both reach these pages as hostile text.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.web import WebError, serve  # noqa: E402
from jobsearch.web.server import App  # noqa: E402

TOKEN = "test-token-value"

XSS = '<script>alert("pwned")</script>'


class WebTestCase(unittest.TestCase):
    """A populated database and an App wired to it."""

    def setUp(self) -> None:
        # Windows keeps a handle on the sqlite file a moment after close().
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"

        conn = db.connect(self.db_path)  # applies the DDL on first connect
        db.set_profile_field(conn, "full_name", "Dana Reyes")
        org = db.upsert_organization(conn, "Northwind Retail", kind="company")
        self.experience_id = db.insert_row(
            conn,
            "experiences",
            {
                "organization_id": org,
                "title": "Senior Software Engineer",
                "start_date": "2021-03",
                "end_date": None,
                "is_current": 1,
                "verified": 1,
            },
        )
        self.achievement_id = db.insert_row(
            conn,
            "achievements",
            {
                "experience_id": self.experience_id,
                "title": "Rebuilt checkout",
                "description": "Rebuilt the checkout API on PostgreSQL.",
                "quantified_impact": "p95 1.9s -> 380ms",
                "verified": 0,  # awaiting review
            },
        )
        db.link_skills_to(conn, ["Python", "PostgreSQL"], "achievement", self.achievement_id, verified=1)
        db.upsert_skill(conn, "Rust", verified=1)  # no evidence -> locked out

        self.job_id = db.insert_row(
            conn,
            "jobs",
            {
                "source": "greenhouse",
                "company": "Acme",
                "title": "Backend Engineer",
                "location": "Remote",
                "remote": 1,
                "url": "https://example.com/job",
                "description": f"We need Python. {XSS}",
                "discovered_at": db.now(),
                "fingerprint": "fp-1",
                "fit_score": 42.5,
                "status": "scored",
            },
        )
        self.app_id = db.insert_application(
            conn,
            {
                "job_id": self.job_id,
                "company": "Acme",
                "role": "Backend Engineer",
                "source": "greenhouse",
                "status": "drafted",
                "fit_score": 42.5,
                "grounding_status": "clean",
                "resume_version": str(Path(self.tmp.name) / "bundle"),
                "decision_reasons": '["autonomous is off -- prepared but not sent"]',
            },
        )
        db.insert_row(conn, "pipeline_runs", {"started_at": db.now(), "mode": "review-only"})
        conn.commit()
        conn.close()

        self.config = Config.from_dict({"search": {"titles": ["Backend Engineer"]}})
        self.app = App(self.db_path, self.config, TOKEN)


class RouteTests(WebTestCase):
    def test_every_page_renders(self) -> None:
        for path in ("/", "/jobs", "/queue", "/profile", "/review", "/runs"):
            with self.subTest(path=path):
                status, body = self.app.get(path, {})
                self.assertEqual(status, 200)
                self.assertIn("<!doctype html>", body)

    def test_detail_pages_render(self) -> None:
        status, body = self.app.get(f"/jobs/{self.job_id}", {})
        self.assertEqual(status, 200)
        self.assertIn("Backend Engineer", body)

        status, body = self.app.get(f"/applications/{self.app_id}", {})
        self.assertEqual(status, 200)
        self.assertIn("Acme", body)

    def test_unknown_paths_are_404(self) -> None:
        for path in ("/nope", "/jobs/9999", "/applications/9999"):
            with self.subTest(path=path):
                status, _body = self.app.get(path, {})
                self.assertEqual(status, 404)

    def test_job_filter_by_status(self) -> None:
        status, body = self.app.get("/jobs", {"status": ["scored"]})
        self.assertEqual(status, 200)
        self.assertIn("Backend Engineer", body)
        status, body = self.app.get("/jobs", {"status": ["applied"]})
        self.assertEqual(status, 200)
        self.assertNotIn("Backend Engineer", body)


class EscapingTests(WebTestCase):
    """Board text and model output are untrusted. Nothing may render as markup."""

    def test_job_description_is_escaped_on_the_detail_page(self) -> None:
        _status, body = self.app.get(f"/jobs/{self.job_id}", {})
        # The sharper question: does the board's payload survive as markup?
        self.assertNotIn(XSS, body)
        self.assertIn("&lt;script&gt;", body)
        # One "<script>" substring: the single static inline script the Evoque
        # shell emits, which CSP allows by hash. A second would mean the
        # board's payload reached the page as markup.
        self.assertEqual(body.count("<script>"), 1)

    def test_company_name_is_escaped_in_listings(self) -> None:
        conn = db.connect(self.db_path)
        db.insert_row(
            conn,
            "jobs",
            {
                "source": "lever",
                "company": XSS,
                "title": "Evil Role",
                "description": "x",
                "discovered_at": db.now(),
                "fingerprint": "fp-2",
                "status": "new",
            },
        )
        conn.commit()
        conn.close()
        _status, body = self.app.get("/jobs", {"scope": ["all"]})
        # One "<script>" substring: the single static inline script the
        # Evoque shell emits, which CSP allows by hash. Any second one would
        # mean hostile markup reached the page unescaped.
        self.assertNotIn(XSS, body)
        self.assertEqual(body.count("<script>"), 1)
        self.assertIn("&lt;script&gt;", body)

    def test_generated_documents_are_escaped(self) -> None:
        bundle = Path(self.tmp.name) / "bundle"
        bundle.mkdir(exist_ok=True)
        (bundle / "resume.md").write_text(f"# Resume\n{XSS}", encoding="utf-8")
        _status, body = self.app.get(f"/applications/{self.app_id}", {})
        # One "<script>" substring: the single static inline script the
        # Evoque shell emits, which CSP allows by hash. Any second one would
        # mean hostile markup reached the page unescaped.
        self.assertNotIn(XSS, body)
        self.assertEqual(body.count("<script>"), 1)
        self.assertIn("&lt;script&gt;", body)


class CsrfTests(WebTestCase):
    def test_post_without_a_token_is_refused(self) -> None:
        status, body = self.app.post(f"/applications/{self.app_id}/approve", {})
        self.assertEqual(status, 403)
        self.assertIn("another site", body)

    def test_post_with_a_wrong_token_is_refused(self) -> None:
        status, _body = self.app.post(
            f"/applications/{self.app_id}/approve", {"token": "not-the-token"}
        )
        self.assertEqual(status, 403)

    def test_refused_post_does_not_change_anything(self) -> None:
        self.app.post(f"/applications/{self.app_id}/approve", {})
        conn = db.connect(self.db_path)
        app = db.get_application(conn, self.app_id)
        conn.close()
        self.assertEqual(app["status"], "drafted")

    def test_the_applied_actions_need_the_token_too(self) -> None:
        for path in (f"/applications/{self.app_id}/sent", f"/jobs/{self.job_id}/applied"):
            with self.subTest(path=path):
                status, _body = self.app.post(path, {"token": "not-the-token"})
                self.assertEqual(status, 403)
        conn = db.connect(self.db_path)
        self.assertEqual(db.get_application(conn, self.app_id)["status"], "drafted")
        conn.close()

    def test_forms_carry_the_token(self) -> None:
        # The page is server-rendered, so the approve/reject forms carry the
        # token the way a form actually should: a hidden input on the form
        # itself, which is what App.post() compares against.
        _status, body = self.app.get(f"/applications/{self.app_id}", {})
        self.assertIn(TOKEN, body)
        self.assertIn(f'<input type="hidden" name="token" value="{TOKEN}">', body)


class ActionTests(WebTestCase):
    def test_approve_sets_status_and_timestamp(self) -> None:
        status, location = self.app.post(
            f"/applications/{self.app_id}/approve", {"token": TOKEN}
        )
        self.assertEqual(status, 303)
        self.assertEqual(location, f"/applications/{self.app_id}")
        conn = db.connect(self.db_path)
        app = db.get_application(conn, self.app_id)
        conn.close()
        self.assertEqual(app["status"], "approved")
        self.assertTrue(app["approved_at"])

    def test_reject_sets_status(self) -> None:
        self.app.post(f"/applications/{self.app_id}/reject", {"token": TOKEN})
        conn = db.connect(self.db_path)
        app = db.get_application(conn, self.app_id)
        conn.close()
        self.assertEqual(app["status"], "rejected")

    def test_verifying_a_row_marks_it_confirmed(self) -> None:
        status, location = self.app.post(
            f"/review/achievements/{self.achievement_id}/verify", {"token": TOKEN}
        )
        self.assertEqual(status, 303)
        self.assertEqual(location, "/review")
        conn = db.connect(self.db_path)
        row = db.get_row(conn, "achievements", self.achievement_id)
        conn.close()
        self.assertEqual(row["verified"], 1)

    def test_only_allowlisted_tables_can_be_verified(self) -> None:
        status, _body = self.app.post("/review/sqlite_master/1/verify", {"token": TOKEN})
        self.assertEqual(status, 400)

    def test_tailoring_an_already_tailored_job_redirects_instead_of_regenerating(self) -> None:
        # An application already exists for this job, so no model call may happen.
        with mock.patch("jobsearch.generate.generate", side_effect=AssertionError("called")):
            status, location = self.app.post(f"/jobs/{self.job_id}/tailor", {"token": TOKEN})
        self.assertEqual(status, 303)
        self.assertEqual(location, f"/applications/{self.app_id}")

    def test_tailoring_failure_is_reported_not_raised(self) -> None:
        conn = db.connect(self.db_path)
        job_id = db.insert_row(
            conn,
            "jobs",
            {
                "source": "greenhouse",
                "company": "Beta",
                "title": "Backend Engineer",
                "description": "Python and PostgreSQL",
                "discovered_at": db.now(),
                "fingerprint": "fp-3",
                "status": "scored",
            },
        )
        conn.commit()
        conn.close()
        with mock.patch(
            "jobsearch.generate.generate", side_effect=RuntimeError("no API key")
        ):
            status, body = self.app.post(f"/jobs/{job_id}/tailor", {"token": TOKEN})
        self.assertEqual(status, 500)
        self.assertIn("no API key", body)


class ContentTests(WebTestCase):
    def test_unevidenced_skills_are_called_out_on_the_profile(self) -> None:
        _status, body = self.app.get("/profile", {})
        self.assertIn("Rust", body)
        self.assertIn("locked out", body)

    def test_review_lists_unverified_rows(self) -> None:
        _status, body = self.app.get("/review", {})
        self.assertIn("Rebuilt checkout", body)

    def test_review_is_empty_once_confirmed(self) -> None:
        self.app.post(f"/review/achievements/{self.achievement_id}/verify", {"token": TOKEN})
        _status, body = self.app.get("/review", {})
        self.assertIn("Nothing awaiting review", body)

    def test_dashboard_warns_when_autonomous_has_no_channel(self) -> None:
        app = App(self.db_path, Config.from_dict({"autonomous": True}), TOKEN)
        _status, body = app.get("/", {})
        self.assertIn("no dispatch channel is enabled", body)

    def test_dashboard_says_review_only_when_autonomous_is_off(self) -> None:
        conn = db.connect(self.db_path)
        conn.close()
        with mock.patch.object(Config, "exists", lambda self: True):
            _status, body = self.app.get("/", {})
        self.assertIn("Review-only mode", body)


class DashboardListTests(WebTestCase):
    """The dashboard's three working lists: what to apply to, what you have
    applied to, and the competitions still open."""

    def add_job(self, title: str, **fields: object) -> int:
        conn = db.connect(self.db_path)
        job_id = db.insert_row(conn, "jobs", {
            "source": "greenhouse", "company": "Globex", "title": title, "location": "Remote",
            "remote": 1, "url": f"https://example.com/{len(title)}", "description": "x",
            "discovered_at": db.now(), "fingerprint": f"fp-{title}", "fit_score": 30.0,
            "status": "scored", **fields,
        })
        conn.commit()
        conn.close()
        return job_id

    def add_competition(self, name: str, deadline: str | None, status: str = "discovered") -> None:
        conn = db.connect(self.db_path)
        db.insert_row(conn, "competitions", {
            "name": name, "category": "hackathon", "deadline": deadline, "status": status,
            "url": f"https://example.com/{len(name)}",
        })
        conn.commit()
        conn.close()

    def dashboard(self) -> str:
        status, body = self.app.get("/", {})
        self.assertEqual(status, 200)
        return body

    def row(self, table: str, row_id: int) -> dict:
        conn = db.connect(self.db_path)
        try:
            return db.get_row(conn, table, row_id)
        finally:
            conn.close()

    def test_to_apply_holds_postings_past_the_filters_and_nothing_else(self) -> None:
        self.add_job("Detection Engineer", remote=0, fit_score=33.0)
        self.add_job("Skipped Wizard", status="skipped", fit_score=90.0)
        self.add_job("Unscored Wizard", status="new", fit_score=None)
        body = self.dashboard()
        self.assertIn("Detection Engineer", body)
        self.assertIn("Backend Engineer", body)  # the fixture's scored posting
        self.assertIn("2 past your filters", body)
        self.assertNotIn("Skipped Wizard", body)
        self.assertNotIn("Unscored Wizard", body)

    def test_applied_lists_what_you_said_yes_to_and_takes_it_off_to_apply(self) -> None:
        body = self.dashboard()
        self.assertIn("Nothing applied to yet", body)
        self.assertIn("1 past your filters", body)
        self.app.post(f"/applications/{self.app_id}/approve", {"token": TOKEN})
        body = self.dashboard()
        self.assertIn("1 approved or sent", body)
        self.assertIn("0 past your filters", body)

    def test_upcoming_competitions_are_the_open_ones_soonest_first(self) -> None:
        today = date.today()
        self.add_competition("Later Hack", (today + timedelta(days=40)).isoformat())
        self.add_competition("Soon Hack", (today + timedelta(days=3)).isoformat())
        self.add_competition("Undated Hack", None)
        self.add_competition("Closed Hack", (today - timedelta(days=5)).isoformat())
        self.add_competition("Dismissed Hack", (today + timedelta(days=2)).isoformat(), "dismissed")
        body = self.dashboard()
        self.assertIn("3 still open", body)
        self.assertLess(body.index("Soon Hack"), body.index("Later Hack"))
        self.assertLess(body.index("Later Hack"), body.index("Undated Hack"))
        self.assertNotIn("Closed Hack", body)
        self.assertNotIn("Dismissed Hack", body)

    def test_hostile_titles_and_names_are_escaped(self) -> None:
        self.add_job(XSS)
        self.add_competition(XSS + " cup", (date.today() + timedelta(days=5)).isoformat())
        body = self.dashboard()
        self.assertNotIn(XSS, body)
        self.assertIn("&lt;script&gt;alert(", body)

    def test_i_applied_marks_the_draft_sent_and_the_job_applied(self) -> None:
        status, location = self.app.post(f"/jobs/{self.job_id}/applied", {"token": TOKEN})
        self.assertEqual((status, location), (303, f"/jobs/{self.job_id}"))
        app = self.row("applications", self.app_id)
        self.assertEqual(
            (app["status"], app["sent_date"], app["channel"]),
            ("sent", date.today().isoformat(), "manual"),
        )
        self.assertEqual(self.row("jobs", self.job_id)["status"], "applied")
        conn = db.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)  # the draft was the application, so none was added

    def test_i_applied_without_a_draft_records_one_manual_application(self) -> None:
        job_id = self.add_job("Detection Engineer")
        for _ in range(2):
            self.app.post(f"/jobs/{job_id}/applied", {"token": TOKEN})
        conn = db.connect(self.db_path)
        rows = db.rows_to_dicts(
            conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchall()
        )
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["status"], rows[0]["channel"], rows[0]["role"], rows[0]["company"]),
            ("sent", "manual", "Detection Engineer", "Globex"),
        )
        self.assertEqual(self.row("jobs", job_id)["status"], "applied")
        self.assertIn("1 approved or sent", self.dashboard())

    def test_mark_as_sent_keeps_the_first_date_and_never_undoes_a_response(self) -> None:
        self.app.post(f"/applications/{self.app_id}/sent", {"token": TOKEN})
        conn = db.connect(self.db_path)
        db.update_application(conn, self.app_id, {"sent_date": "2026-01-02"})
        conn.commit()
        conn.close()
        self.app.post(f"/applications/{self.app_id}/sent", {"token": TOKEN})
        app = self.row("applications", self.app_id)
        self.assertEqual((app["status"], app["sent_date"]), ("sent", "2026-01-02"))

        conn = db.connect(self.db_path)
        db.update_application(conn, self.app_id, {"status": "responded"})
        conn.commit()
        conn.close()
        self.app.post(f"/applications/{self.app_id}/sent", {"token": TOKEN})
        self.assertEqual(self.row("applications", self.app_id)["status"], "responded")

    def test_the_buttons_are_only_offered_while_they_make_sense(self) -> None:
        _s, job_page = self.app.get(f"/jobs/{self.job_id}", {})
        _s, app_page = self.app.get(f"/applications/{self.app_id}", {})
        self.assertIn("I applied to this role", job_page)
        self.assertIn("Mark as sent", app_page)
        self.app.post(f"/jobs/{self.job_id}/applied", {"token": TOKEN})
        _s, job_page = self.app.get(f"/jobs/{self.job_id}", {})
        _s, app_page = self.app.get(f"/applications/{self.app_id}", {})
        self.assertNotIn("I applied to this role", job_page)
        self.assertNotIn("Mark as sent", app_page)


class BindingTests(unittest.TestCase):
    def test_serve_refuses_a_public_interface(self) -> None:
        # With a password in the environment these binds are allowed, and serve()
        # would then run until killed -- so the test sets "no password" itself
        # rather than trusting whatever the process inherited.
        with mock.patch.dict(os.environ):
            os.environ.pop("JOBSEARCH_PASSWORD", None)
            for host in ("0.0.0.0", "192.168.1.10", ""):
                with self.subTest(host=host):
                    with self.assertRaises(WebError):
                        serve(host=host, open_browser=False)


if __name__ == "__main__":
    unittest.main()


class PublicBindTests(unittest.TestCase):
    """A public bind is possible for Render, but never silently unprotected."""

    def setUp(self) -> None:
        self._saved = os.environ.pop("JOBSEARCH_PASSWORD", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._saved is None:
            os.environ.pop("JOBSEARCH_PASSWORD", None)
        else:
            os.environ["JOBSEARCH_PASSWORD"] = self._saved

    def test_binding_publicly_without_a_password_is_refused(self) -> None:
        from jobsearch.web.server import WebError, serve

        with self.assertRaises(WebError) as caught:
            serve(host="0.0.0.0", port=0, open_browser=False)
        self.assertIn("password", str(caught.exception).lower())

    def test_loopback_still_needs_no_password(self) -> None:
        # Local use must stay frictionless; the socket is the protection there.
        app = App(None, Config(), "tok")
        self.assertEqual(app.password, "")
