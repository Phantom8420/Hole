"""Tests for sourcing, policy, dispatch, and the pipeline orchestrator.

No network and no API key. Connectors are exercised against captured response
shapes; the model call is stubbed.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import cli, db, generate, graph, pipeline, policy, retrieval, schedule  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.dispatch import ats_form, email_gmail, find_apply_email  # noqa: E402
from jobsearch.dispatch import linkedin as linkedin_draft  # noqa: E402
from jobsearch.sourcing import aggregators, ats_boards, base, store  # noqa: E402

BACKEND_POSTING = """
Requirements
- 5+ years designing and building high-throughput Python services
- Production experience with PostgreSQL and Redis at scale
- Docker, Kubernetes, and modern CI/CD
"""


def make_config(**overrides: object) -> Config:
    raw: dict = {
        "autonomous": False,
        "search": {"titles": ["Backend Engineer"], "min_fit": 30, "max_age_days": 3650},
        "limits": {
            "max_applications_per_day": 5,
            "max_applications_per_run": 3,
            "max_per_company_per_week": 2,
            "max_tailor_per_run": 4,
        },
        "dispatch": {"channel_order": ["ats_form", "email"], "require_clean_grounding": True},
        "sources": {"greenhouse": {"enabled": True, "boards": ["acme"]}},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key].update(value)
        else:
            raw[key] = value
    return Config.from_dict(raw, path=Path("config.toml"))


JOB = {
    "id": 1,
    "company": "Acme",
    "title": "Backend Engineer",
    "location": "Remote",
    "remote": 1,
    "apply_url": "https://boards.greenhouse.io/acme/jobs/9",
    "description": BACKEND_POSTING,
    "fit_score": 55.0,
    "posted_at": None,
}


class TempDbCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_path = self.tmp / "test.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def seed_profile(self) -> None:
        with db.session(self.db_path) as conn:
            db.set_profile_field(conn, "name", "Sam Rivera")
            db.set_profile_field(conn, "email", "sam@example.com")
            org = db.upsert_organization(conn, "Northwind Retail")
            experience_id = db.insert_row(
                conn,
                "experiences",
                {"organization_id": org, "title": "Senior Software Engineer",
                 "start_date": "2023-02", "end_date": "2025-01", "verified": 1},
            )
            bullet = db.insert_row(
                conn,
                "achievements",
                {"experience_id": experience_id, "title": "Rebuilt checkout",
                 "description": "Event-driven Python service on PostgreSQL, Redis, Kubernetes.",
                 "quantified_impact": "cut p95 latency to 380ms", "verified": 1},
            )
            db.link_skills_to(
                conn, ["Python", "PostgreSQL", "Redis", "Kubernetes"], "achievement", bullet,
                verified=1,
            )


# --------------------------------------------------------------------------- config


class ConfigTests(unittest.TestCase):
    def test_autonomous_defaults_off(self) -> None:
        self.assertFalse(Config.from_dict({}).autonomous)

    def test_missing_file_is_reported_as_a_problem(self) -> None:
        config = Config.load(Path("does-not-exist.toml"))
        self.assertFalse(config.exists())
        self.assertTrue(any("Copy" in p or "No config" in p for p in config.problems()))

    def test_enabled_sources_need_targets(self) -> None:
        config = Config.from_dict(
            {"sources": {"greenhouse": {"enabled": True, "boards": []}}}, path=Path("c.toml")
        )
        with mock.patch.object(Path, "is_file", lambda self: True):
            problems = config.problems()
        self.assertTrue(any("no boards" in p for p in problems))

    def test_autonomous_without_a_channel_is_a_problem(self) -> None:
        config = make_config(autonomous=True)
        with mock.patch.object(Path, "is_file", lambda self: True):
            problems = config.problems()
        self.assertTrue(any("ats_form" in p or "channel" in p for p in problems))

    def test_review_only_warning(self) -> None:
        config = make_config()
        with mock.patch.object(Path, "is_file", lambda self: True):
            self.assertTrue(any("autonomous = false" in w for w in config.warnings()))


# --------------------------------------------------------------------------- sourcing


class SourcingBaseTests(unittest.TestCase):
    def test_html_to_text_keeps_structure(self) -> None:
        text = base.html_to_text("<p>Build things</p><ul><li>Python</li><li>Redis</li></ul>")
        self.assertIn("Build things", text)
        self.assertIn("- Python", text)
        self.assertNotIn("<", text)

    def test_html_to_text_survives_escaped_markup(self) -> None:
        self.assertIn("Python", base.html_to_text("&lt;p&gt;Python&lt;/p&gt;"))

    def test_html_to_text_drops_scripts(self) -> None:
        self.assertNotIn("alert", base.html_to_text("<p>Hi</p><script>alert(1)</script>"))

    def test_remote_is_inferred_from_location(self) -> None:
        self.assertTrue(base.Posting("x", "1", "Acme", "Engineer", location="Remote - US").remote)
        self.assertFalse(base.Posting("x", "2", "Acme", "Engineer", location="Austin TX").remote)

    def test_apply_url_falls_back_to_url(self) -> None:
        posting = base.Posting("x", "1", "Acme", "Engineer", url="https://a.example/1")
        self.assertEqual(posting.apply_url, "https://a.example/1")

    def test_fingerprint_collapses_the_same_role(self) -> None:
        first = base.Posting("greenhouse", "1", "Acme Inc", "Backend Engineer", location="Remote")
        second = base.Posting("lever", "99", "acme inc.", "backend  engineer", location="remote")
        self.assertEqual(first.fingerprint(), second.fingerprint())

    def test_fingerprint_separates_different_roles(self) -> None:
        first = base.Posting("greenhouse", "1", "Acme", "Backend Engineer")
        second = base.Posting("greenhouse", "2", "Acme", "Frontend Engineer")
        self.assertNotEqual(first.fingerprint(), second.fingerprint())

    def test_dedupe(self) -> None:
        postings = [
            base.Posting("a", "1", "Acme", "Engineer"),
            base.Posting("b", "2", "Acme", "Engineer"),
        ]
        self.assertEqual(len(base.dedupe(postings)), 1)

    def test_iso_date_shapes(self) -> None:
        self.assertEqual(base.iso_date("2026-03-04T10:00:00Z"), "2026-03-04")
        self.assertEqual(base.iso_date(1700000000000), "2023-11-14")
        self.assertIsNone(base.iso_date(None))


class ConnectorTests(unittest.TestCase):
    def test_greenhouse_uses_the_hosted_application_url(self) -> None:
        payloads = [
            {"name": "Acme"},
            {"jobs": [{
                "id": 9, "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://acme.com/careers?gh_jid=9",
                "content": "&lt;p&gt;Python and Redis&lt;/p&gt;",
                "updated_at": "2026-03-04T00:00:00Z",
            }]},
        ]
        with mock.patch.object(ats_boards, "fetch_json", side_effect=payloads):
            result = ats_boards.fetch_greenhouse(["acme"])
        posting = result.postings[0]
        self.assertEqual(posting.company, "Acme")
        self.assertEqual(posting.apply_url, "https://boards.greenhouse.io/acme/jobs/9")
        self.assertEqual(posting.url, "https://acme.com/careers?gh_jid=9")
        self.assertIn("Python and Redis", posting.description)
        self.assertEqual(posting.posted_at, "2026-03-04")

    def test_greenhouse_records_errors_without_raising(self) -> None:
        with mock.patch.object(ats_boards, "fetch_json", side_effect=base.SourceError("404")):
            result = ats_boards.fetch_greenhouse(["nope"])
        self.assertEqual(result.postings, [])
        self.assertTrue(result.errors)

    def test_lever_assembles_description_blocks(self) -> None:
        payload = [{
            "id": "abc", "text": "Backend Engineer",
            "categories": {"location": "Remote"},
            "hostedUrl": "https://jobs.lever.co/acme/abc",
            "applyUrl": "https://jobs.lever.co/acme/abc/apply",
            "descriptionPlain": "Own the backend.",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
            "createdAt": 1700000000000,
        }]
        with mock.patch.object(ats_boards, "fetch_json", return_value=payload):
            result = ats_boards.fetch_lever(["acme"])
        posting = result.postings[0]
        self.assertIn("Own the backend", posting.description)
        self.assertIn("Requirements", posting.description)
        self.assertIn("Python", posting.description)
        self.assertEqual(posting.apply_url, "https://jobs.lever.co/acme/abc/apply")

    def test_ashby_reads_remote_flag(self) -> None:
        payload = {"name": "Acme", "jobs": [{
            "id": "1", "title": "Backend Engineer", "location": "Anywhere",
            "isRemote": True, "jobUrl": "https://jobs.ashbyhq.com/acme/1",
            "descriptionHtml": "<p>Go and Postgres</p>", "publishedAt": "2026-02-01",
        }]}
        with mock.patch.object(ats_boards, "fetch_json", return_value=payload):
            result = ats_boards.fetch_ashby(["acme"])
        self.assertTrue(result.postings[0].remote)
        self.assertIn("Go and Postgres", result.postings[0].description)

    def test_adzuna_requires_credentials(self) -> None:
        result = aggregators.fetch_adzuna(app_id="", app_key="")
        self.assertEqual(result.postings, [])
        self.assertTrue(result.errors)

    def test_adzuna_parses_results(self) -> None:
        payload = {"results": [{
            "id": "7", "title": "Backend Engineer", "description": "Python work.",
            "company": {"display_name": "Acme"}, "location": {"display_name": "Remote"},
            "redirect_url": "https://adzuna.example/7", "created": "2026-02-02T00:00:00Z",
            "salary_min": 100000, "salary_max": 150000,
        }]}
        with mock.patch.object(aggregators, "fetch_json", return_value=payload):
            result = aggregators.fetch_adzuna(app_id="a", app_key="b", max_pages=1)
        self.assertEqual(result.postings[0].company, "Acme")
        self.assertEqual(result.postings[0].compensation, "100000-150000")

    def test_usajobs_parses_results(self) -> None:
        payload = {"SearchResult": {"SearchResultItems": [{"MatchedObjectDescriptor": {
            "PositionID": "X1", "PositionTitle": "IT Specialist",
            "OrganizationName": "Dept of Example", "PositionLocationDisplay": "Remote",
            "PositionURI": "https://usajobs.example/X1", "ApplyURI": ["https://apply.example/X1"],
            "UserArea": {"Details": {"JobSummary": "Run systems.", "Requirements": "Python."}},
            "PublicationStartDate": "2026-01-15",
        }}]}}
        with mock.patch.object(aggregators, "fetch_json", return_value=payload):
            result = aggregators.fetch_usajobs(email="a@b.c", api_key="k")
        posting = result.postings[0]
        self.assertEqual(posting.apply_url, "https://apply.example/X1")
        self.assertIn("Run systems", posting.description)
        self.assertIn("Python", posting.description)


class StoreTests(TempDbCase):
    def test_store_skips_duplicates(self) -> None:
        postings = [base.Posting("greenhouse", "1", "Acme", "Backend Engineer")]
        with db.session(self.db_path) as conn:
            new, dupes = store(conn, postings)
            self.assertEqual((new, dupes), (1, 0))
            new, dupes = store(conn, postings)
            self.assertEqual((new, dupes), (0, 1))

    def test_store_never_provokes_an_integrity_error(self) -> None:
        class Remote:
            """sqlite, but a constraint failure comes out the way Turso's does (a
            KeyError from its client library, never sqlite3.IntegrityError). With
            `blind`, the fingerprint lookup also finds nothing, as it would for a
            posting another writer stored a moment ago."""

            def __init__(self, conn: sqlite3.Connection, blind: bool = False) -> None:
                self.conn, self.blind = conn, blind

            def execute(self, sql, params=()):
                if self.blind and sql.startswith("SELECT fingerprint"):
                    return iter(())
                try:
                    return self.conn.execute(sql, params)
                except sqlite3.IntegrityError as exc:
                    raise KeyError("result") from exc

        postings = [
            base.Posting("greenhouse", "1", "Acme", "Backend Engineer"),
            base.Posting("lever", "2", "Beta", "Data Engineer"),
        ]
        fresh = base.Posting("ashby", "3", "Gamma", "ML Engineer")
        with db.session(self.db_path) as conn:
            self.assertEqual(store(Remote(conn), postings), (2, 0))
            self.assertEqual(store(Remote(conn), postings + postings), (0, 4))
            self.assertEqual(store(Remote(conn, blind=True), postings), (0, 2))
            self.assertEqual(store(Remote(conn), [fresh, fresh]), (1, 1))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 3)


# --------------------------------------------------------------------------- policy


class ScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = make_config()
        self.context = policy.PolicyContext()

    def screen(self, **overrides: object) -> policy.Decision:
        return policy.screen({**JOB, **overrides}, self.config, self.context)

    def test_matching_job_proceeds(self) -> None:
        self.assertEqual(self.screen().action, policy.QUEUE)

    def test_excluded_company(self) -> None:
        self.config.search.exclude_companies = ["acme"]
        decision = self.screen()
        self.assertEqual(decision.action, policy.SKIP)
        self.assertIn("exclude list", decision.reasons[0])

    def test_excluded_keyword(self) -> None:
        self.config.search.exclude_keywords = ["security clearance"]
        decision = self.screen(description="Requires security clearance.")
        self.assertEqual(decision.action, policy.SKIP)

    def test_excluded_title_word(self) -> None:
        self.config.search.exclude_title_keywords = ["senior", "lead"]
        for title in ("Senior Backend Engineer", "SENIOR backend engineer", "Backend Engineer (Lead)"):
            with self.subTest(title):
                decision = self.screen(title=title)
                self.assertEqual(decision.action, policy.SKIP)
                self.assertIn("excluded word", decision.reasons[0])

    def test_title_words_are_whole_words_and_only_read_in_the_title(self) -> None:
        self.config.search.exclude_title_keywords = ["senior", "lead", "vp"]
        for title in ("Backend Engineer, Leadership Programme", "VPN Backend Engineer"):
            with self.subTest(title):
                self.assertEqual(self.screen(title=title).action, policy.QUEUE)
        description = "You will work with senior engineers and a lead."
        self.assertEqual(self.screen(description=description).action, policy.QUEUE)

    def test_title_words_come_from_the_config_in_lower_case(self) -> None:
        config = Config.from_dict({"search": {"exclude_title_keywords": ["Senior", "VP"]}})
        self.assertEqual(config.search.exclude_title_keywords, ["senior", "vp"])
        self.assertEqual(Config.from_dict({}).search.exclude_title_keywords, [])

    def test_title_must_match(self) -> None:
        decision = self.screen(title="Account Executive")
        self.assertEqual(decision.action, policy.SKIP)
        self.assertIn("does not match", decision.reasons[0])

    def test_no_configured_titles_lets_everything_through(self) -> None:
        self.config.search.titles = []
        self.assertEqual(self.screen(title="Chef").action, policy.QUEUE)

    def test_fit_floor(self) -> None:
        decision = self.screen(fit_score=10.0)
        self.assertEqual(decision.action, policy.SKIP)
        self.assertIn("below min_fit", decision.reasons[0])

    def test_remote_only(self) -> None:
        self.config.search.remote_only = True
        self.assertEqual(self.screen(remote=0).action, policy.SKIP)

    def test_unknown_location_is_not_a_mismatch(self) -> None:
        self.config.search.locations = ["United States"]
        self.assertEqual(self.screen(remote=0, location="N/A").action, policy.QUEUE)

    def test_wrong_location_is_a_mismatch(self) -> None:
        self.config.search.locations = ["United States"]
        self.assertEqual(self.screen(remote=0, location="Dublin, Ireland").action, policy.SKIP)

    def test_stale_posting(self) -> None:
        self.config.search.max_age_days = 30
        self.assertEqual(self.screen(posted_at="2020-01-01").action, policy.SKIP)

    def test_already_applied(self) -> None:
        self.context.applied_fingerprints.add("acme|backend engineer")
        decision = self.screen()
        self.assertEqual(decision.action, policy.SKIP)
        self.assertIn("already applied", decision.reasons[0])

    def test_tailor_cap(self) -> None:
        self.context.tailored_this_run = 99
        self.assertEqual(self.screen().action, policy.SKIP)

    def test_title_matching_is_loose_but_not_reckless(self) -> None:
        self.assertTrue(policy.title_matches("Senior Backend Engineer", ["Backend Engineer"]))
        self.assertTrue(policy.title_matches("Backend Engineer, Payments", ["Backend Engineer"]))
        self.assertFalse(policy.title_matches("Warehouse Associate", ["Backend Engineer"]))


class DispatchDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = make_config(
            autonomous=True,
            dispatch={"channel_order": ["ats_form"], "ats": {"enabled": True}},
        )
        self.context = policy.PolicyContext()

    def decide(self, **kwargs: object) -> policy.Decision:
        return policy.decide_dispatch(JOB, self.config, self.context, **kwargs)

    def test_clean_run_sends(self) -> None:
        self.assertEqual(self.decide().action, policy.SEND)

    def test_review_only_never_sends(self) -> None:
        self.config.autonomous = False
        decision = self.decide()
        self.assertEqual(decision.action, policy.QUEUE)
        self.assertIn("autonomous is off", decision.reasons[0])

    def test_flagged_grounding_blocks_sending(self) -> None:
        finding = mock.Mock(kind="unsourced-number")
        decision = self.decide(grounding_findings=[finding])
        self.assertEqual(decision.action, policy.QUEUE)
        self.assertIn("grounding check flagged", decision.reasons[0])

    def test_grounding_can_be_waived_by_config(self) -> None:
        self.config.dispatch.require_clean_grounding = False
        self.assertEqual(self.decide(grounding_findings=[mock.Mock(kind="x")]).action, policy.SEND)

    def test_missing_profile_fields_block_sending(self) -> None:
        decision = self.decide(missing_profile_fields=["email"])
        self.assertEqual(decision.action, policy.QUEUE)

    def test_unverified_records_block_when_required(self) -> None:
        self.config.dispatch.require_verified_records = True
        self.assertEqual(self.decide(used_unverified=True).action, policy.QUEUE)

    def test_per_run_cap(self) -> None:
        self.context.sent_this_run = 3
        self.assertIn("per_run", self.decide().reasons[0])

    def test_per_day_cap(self) -> None:
        self.context.sent_today = 5
        self.assertIn("per_day", self.decide().reasons[0])

    def test_per_company_cap(self) -> None:
        self.context.per_company_week["acme"] = 2
        self.assertIn("this week", self.decide().reasons[0])

    def test_no_channel_blocks_sending(self) -> None:
        self.config.dispatch.ats.enabled = False
        self.assertIn("no dispatch channel", self.decide().reasons[0])

    def test_recording_a_send_advances_every_counter(self) -> None:
        self.context.record_sent("Acme")
        self.assertEqual(self.context.sent_today, 1)
        self.assertEqual(self.context.sent_this_run, 1)
        self.assertEqual(self.context.per_company_week["acme"], 1)


class PolicyContextTests(TempDbCase):
    def test_context_loads_counters_from_the_database(self) -> None:
        from datetime import date

        with db.session(self.db_path) as conn:
            db.insert_application(conn, {
                "company": "Acme", "role": "Backend Engineer", "status": "sent",
                "sent_date": date.today().isoformat(),
            })
            context = policy.PolicyContext.load(conn)
        self.assertEqual(context.sent_today, 1)
        self.assertEqual(context.per_company_week["acme"], 1)
        self.assertIn("acme|backend engineer", context.applied_fingerprints)


# --------------------------------------------------------------------------- dispatch


class DispatchHelperTests(unittest.TestCase):
    def test_find_apply_email_uses_an_invited_address(self) -> None:
        self.assertEqual(find_apply_email("Send your resume to jobs@acme.com"), "jobs@acme.com")

    def test_find_apply_email_ignores_noreply(self) -> None:
        self.assertIsNone(find_apply_email("Questions? noreply@acme.com"))

    def test_find_apply_email_returns_none_when_absent(self) -> None:
        self.assertIsNone(find_apply_email("Apply on our website."))

    def test_detect_ats(self) -> None:
        self.assertEqual(ats_form.detect_ats("https://boards.greenhouse.io/acme/jobs/9"), "greenhouse")
        self.assertEqual(ats_form.detect_ats("https://jobs.lever.co/acme/1/apply"), "lever")
        self.assertEqual(ats_form.detect_ats("https://jobs.ashbyhq.com/acme/1"), "ashby")
        self.assertEqual(ats_form.detect_ats("https://acme.com/careers"), "unknown")

    def test_unknown_host_is_refused(self) -> None:
        config = make_config().dispatch.ats
        config.enabled = True
        result = ats_form.submit(
            config, apply_url="https://acme.com/careers",
            fields=ats_form.ApplicantFields(), project_root=Path("."), slug="x",
        )
        self.assertFalse(result.ok)
        self.assertIn("unrecognized", result.detail)

    def test_applicant_fields_from_profile(self) -> None:
        fields = ats_form.ApplicantFields.from_profile(
            {"full_name": "Sam Rivera", "email": "sam@example.com", "phone": "555"}
        )
        self.assertEqual((fields.first_name, fields.last_name), ("Sam", "Rivera"))
        self.assertIn("resume file", fields.missing_required())

    def test_missing_essentials_abort_before_touching_a_browser(self) -> None:
        config = make_config().dispatch.ats
        config.enabled = True
        result = ats_form.submit(
            config, apply_url="https://boards.greenhouse.io/acme/jobs/9",
            fields=ats_form.ApplicantFields(), project_root=Path("."), slug="x",
        )
        self.assertFalse(result.ok)
        self.assertIn("missing", result.detail)

    def test_email_builds_a_mime_message_with_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            attachment = Path(tmp) / "resume.pdf"
            attachment.write_bytes(b"%PDF-1.4 fake")
            message = email_gmail.build_message(
                to="hiring@acme.com", subject="Application", body="Hello",
                attachments=[attachment],
            )
        self.assertEqual(message["To"], "hiring@acme.com")
        self.assertTrue(message.is_multipart())
        self.assertIn("resume.pdf", message.as_string())

    def test_email_disabled_is_reported_not_raised(self) -> None:
        result = email_gmail.send(
            make_config().dispatch.email, Path("."), to="a@b.c", subject="s", body="b"
        )
        self.assertFalse(result.ok)
        self.assertIn("enabled is false", result.detail)


class LinkedInDraftTests(unittest.TestCase):
    def test_module_exposes_no_automation(self) -> None:
        exported = {name for name in dir(linkedin_draft) if not name.startswith("_")}
        for forbidden in ("send", "submit", "connect", "click", "navigate", "login"):
            self.assertNotIn(forbidden, exported)

    def test_deep_links_are_plain_search_urls(self) -> None:
        link = linkedin_draft.company_people_link("Acme", "Backend Engineer")
        self.assertTrue(link.startswith("https://www.linkedin.com/search/results/people/"))
        self.assertIn("Acme", link.replace("+", " ").replace("%20", " "))

    def test_connection_note_is_truncated_to_the_platform_limit(self) -> None:
        draft = linkedin_draft.OutreachDraft("linkedin_note", "", "word " * 200)
        self.assertLessEqual(len(draft.truncated_for_note()), linkedin_draft.CONNECTION_NOTE_LIMIT)

    def test_short_note_is_left_alone(self) -> None:
        draft = linkedin_draft.OutreachDraft("linkedin_note", "", "Short note.")
        self.assertEqual(draft.truncated_for_note(), "Short note.")

    def test_response_parsing(self) -> None:
        subject, body = linkedin_draft._parse(
            "===SUBJECT===\nRe: Backend role\n===MESSAGE===\nHello there.\n"
        )
        self.assertEqual(subject, "Re: Backend role")
        self.assertEqual(body, "Hello there.")


# --------------------------------------------------------------------------- scheduling


class ScheduleTests(unittest.TestCase):
    def test_wrapper_runs_the_pipeline_from_the_project_directory(self) -> None:
        path = schedule.write_wrapper()
        try:
            script = path.read_text(encoding="utf-8")
            self.assertIn("-m jobsearch run", script)
            self.assertIn("cd /d", script)
        finally:
            path.unlink(missing_ok=True)

    def test_bad_time_is_rejected(self) -> None:
        with mock.patch.object(schedule, "_schtasks_available", return_value=True):
            self.assertFalse(schedule.install(at="not-a-time").ok)


# --------------------------------------------------------------------------- pipeline


CANNED = """===RESUME===
# Sam Rivera
sam@example.com

## Experience
### Senior Software Engineer - Northwind Retail (2023-02 - 2025-01)
- Event-driven Python service on PostgreSQL, cut p95 latency to 380ms.
===COVER_LETTER===
Dear team,

I cut checkout p95 latency to 380ms at Northwind Retail.

Sam Rivera
===FIT_NOTES===
No payments domain experience.
"""

DIRTY = CANNED.replace("cut p95 latency to 380ms.", "saved $4.2M annually.")


def stub_generate(text: str):
    def _generate(job_description, plan, **kwargs):
        resume, letter, notes = generate.parse_response(text)
        return generate.TailoredOutput(
            resume=resume, cover_letter=letter, fit_notes=notes, raw=text,
            model="stub", usage={"stop_reason": "end_turn"},
        )

    return _generate


class PipelineTests(TempDbCase):
    def setUp(self) -> None:
        super().setUp()
        self.seed_profile()
        with db.session(self.db_path) as conn:
            store(conn, [base.Posting(
                source="greenhouse", external_id="9", company="Acme",
                title="Backend Engineer", location="Remote",
                apply_url="https://boards.greenhouse.io/acme/jobs/9",
                url="https://acme.com/careers", description=BACKEND_POSTING,
            )])

    def run_pipeline(self, config: Config, canned: str = CANNED, **kwargs: object):
        with mock.patch.object(pipeline.generate, "generate", stub_generate(canned)):
            with db.session(self.db_path) as conn:
                return pipeline.run(conn, config, skip_sourcing=True, **kwargs)

    def test_review_only_run_tailors_but_never_sends(self) -> None:
        report = self.run_pipeline(make_config())
        self.assertEqual(report.tailored, 1)
        self.assertEqual(report.sent, 0)
        self.assertEqual(report.queued, 1)
        with db.session(self.db_path) as conn:
            application = db.list_applications(conn)[0]
        self.assertEqual(application["status"], "drafted")
        self.assertIn("autonomous is off", application["decision_reasons"])

    def test_flagged_grounding_stops_an_autonomous_send(self) -> None:
        config = make_config(
            autonomous=True, dispatch={"channel_order": ["ats_form"], "ats": {"enabled": True}}
        )
        report = self.run_pipeline(config, canned=DIRTY)
        self.assertEqual(report.sent, 0)
        self.assertEqual(report.queued, 1)
        with db.session(self.db_path) as conn:
            application = db.list_applications(conn)[0]
        self.assertEqual(application["grounding_status"], "flagged")

    def test_scoring_records_a_fit_for_every_posting(self) -> None:
        self.run_pipeline(make_config(), **{"limit": 0})
        with db.session(self.db_path) as conn:
            job = db.get_row(conn, "jobs", 1)
        self.assertIsNotNone(job["fit_score"])
        self.assertGreater(job["fit_score"], 0)

    def test_screened_out_jobs_record_why(self) -> None:
        config = make_config(search={"titles": ["Chef"]})
        report = self.run_pipeline(config)
        self.assertEqual(report.screened_out, 1)
        with db.session(self.db_path) as conn:
            job = db.get_row(conn, "jobs", 1)
        self.assertEqual(job["status"], "skipped")
        self.assertIn("does not match", job["skip_reason"])

    def test_run_is_recorded_for_audit(self) -> None:
        report = self.run_pipeline(make_config())
        with db.session(self.db_path) as conn:
            run_row = db.get_row(conn, "pipeline_runs", report.run_id)
        self.assertEqual(run_row["mode"], "review-only")
        self.assertIsNotNone(run_row["finished_at"])
        self.assertEqual(run_row["tailored"], 1)

    def test_empty_profile_aborts_with_an_explanation(self) -> None:
        empty = self.tmp / "empty.db"
        with db.session(empty) as conn:
            report = pipeline.run(conn, make_config(), skip_sourcing=True)
        self.assertTrue(any("empty" in e for e in report.errors))

    def test_generation_failure_marks_the_job_not_the_run(self) -> None:
        def explode(*args: object, **kwargs: object):
            raise generate.GenerationError("no API key")

        with mock.patch.object(pipeline.generate, "generate", explode):
            with db.session(self.db_path) as conn:
                report = pipeline.run(conn, make_config(), skip_sourcing=True)
        self.assertEqual(report.tailored, 0)
        self.assertTrue(report.errors)
        with db.session(self.db_path) as conn:
            self.assertEqual(db.get_row(conn, "jobs", 1)["status"], "failed")

    def test_write_bundle_records_provenance(self) -> None:
        with db.session(self.db_path) as conn:
            g = graph.ProfileGraph.load(conn)
        plan = retrieval.build_plan(g, BACKEND_POSTING, company="Acme", role="Backend Engineer")
        result = stub_generate(CANNED)(BACKEND_POSTING, plan)
        out_dir = self.tmp / "bundle"
        pipeline.write_bundle(
            out_dir, result=result, job_description=BACKEND_POSTING, plan=plan,
            meta={"company": "Acme"},
        )
        self.assertTrue((out_dir / "resume.md").is_file())
        sources = json.loads((out_dir / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(sources["company"], "Acme")
        self.assertIn("skills_claimed", sources)
        self.assertTrue(sources["experiences_used"])


class PipelineCliTests(TempDbCase):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main([*argv, "--db", str(self.db_path)])
        return code, buffer.getvalue()

    def test_jobs_list_is_empty_before_a_run(self) -> None:
        code, output = self.run_cli("jobs")
        self.assertEqual(code, 0)
        self.assertIn("No postings stored", output)

    def test_jobs_rescore_resets_stale_verdicts(self) -> None:
        with db.session(self.db_path) as conn:
            store(conn, [base.Posting("greenhouse", "1", "Acme", "Backend Engineer")])
            db.update_row(conn, "jobs", 1, {"status": "skipped", "skip_reason": "old rule"})
        code, output = self.run_cli("jobs", "rescore")
        self.assertEqual(code, 0)
        self.assertIn("Reset 1", output)
        with db.session(self.db_path) as conn:
            self.assertEqual(db.get_row(conn, "jobs", 1)["status"], "new")

    def test_runs_history_is_empty_initially(self) -> None:
        code, output = self.run_cli("runs")
        self.assertIn("No pipeline runs", output)

    def test_outreach_refuses_to_send_linkedin_messages(self) -> None:
        with db.session(self.db_path) as conn:
            db.insert_row(conn, "messages", {
                "channel": "linkedin_dm", "body": "Hi there", "status": "drafted",
                "created_at": db.now(),
            })
        code, _ = self.run_cli("outreach", "send", "1")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
