"""Tests for the MCP server -- the third interface onto the same backend.

Every tool here wraps a function CLI and the web UI already exercise, so
these tests are less about re-proving that logic and more about proving the
wiring: does the tool call the right function with the right arguments, and
does the result actually reach the caller through the protocol layer (not
just "the underlying function works," which test_pipeline.py / test_web.py
already cover).

Calls go through `mcp.call_tool()`, the real async protocol path a client
would use, not the bare decorated function -- that is what tests the wiring
this file exists to test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db, generate  # noqa: E402
from jobsearch.mcp import server as mcp_server  # noqa: E402
from jobsearch.sourcing import competitions as competitions_sourcing  # noqa: E402


def stub_generate(text: str):
    def _generate(job_description, plan, **kwargs):
        resume, letter, notes = generate.parse_response(text)
        return generate.TailoredOutput(
            resume=resume, cover_letter=letter, fit_notes=notes, raw=text,
            model="stub", usage={"stop_reason": "end_turn"},
        )
    return _generate


CANNED = "# Resume\nSam Rivera\n\n# Cover Letter\nDear team,\n\n# Fit Notes\nGood match."


class McpTestCase(unittest.IsolatedAsyncioTestCase):
    """A populated database, reachable at $JOBSEARCH_DB the way the real
    `jobsearch mcp` command sets it up from --db."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"

        # The MCP tools open the database through $JOBSEARCH_DB, so unlike the
        # tests that pass a path, nothing but the environment keeps them off Turso.
        patched = mock.patch.dict(os.environ)
        patched.start()
        self.addCleanup(patched.stop)
        os.environ.pop("TURSO_DATABASE_URL", None)
        os.environ.pop("TURSO_AUTH_TOKEN", None)

        self._prior_env = os.environ.get("JOBSEARCH_DB")
        os.environ["JOBSEARCH_DB"] = str(self.db_path)
        self.addCleanup(self._restore_env)

        conn = db.connect(self.db_path)
        db.set_profile_field(conn, "full_name", "Sam Rivera")
        org = db.upsert_organization(conn, "Northwind Retail", kind="company")
        experience_id = db.insert_row(
            conn, "experiences",
            {"organization_id": org, "title": "Senior Software Engineer",
             "start_date": "2023-02", "end_date": "2025-01", "verified": 1},
        )
        bullet = db.insert_row(
            conn, "achievements",
            {"experience_id": experience_id, "title": "Rebuilt checkout",
             "description": "Event-driven Python service on PostgreSQL.",
             "quantified_impact": "p95 1.9s -> 380ms", "verified": 1},
        )
        db.link_skills_to(conn, ["Python", "PostgreSQL"], "achievement", bullet, verified=1)

        self.job_id = db.insert_row(
            conn, "jobs",
            {"source": "greenhouse", "company": "Acme", "title": "Backend Engineer",
             "location": "Remote", "remote": 1, "url": "https://example.com/job",
             "description": "We need Python and PostgreSQL.", "discovered_at": db.now(),
             "fingerprint": "fp-1", "fit_score": 42.5, "status": "scored"},
        )
        self.app_id = db.insert_application(
            conn,
            {"job_id": self.job_id, "company": "Acme", "role": "Backend Engineer",
             "source": "greenhouse", "status": "drafted", "fit_score": 42.5,
             "grounding_status": "clean", "resume_version": str(self.tmp.name)},
        )
        conn.commit()
        conn.close()

    def _restore_env(self) -> None:
        if self._prior_env is None:
            os.environ.pop("JOBSEARCH_DB", None)
        else:
            os.environ["JOBSEARCH_DB"] = self._prior_env

    async def call(self, name: str, **kwargs):
        """Invoke a tool through the real protocol path and return the
        parsed structured result -- what a client actually receives.

        The SDK wraps a tool's return value in {"result": ...} when the
        return type can't stand alone as an object schema (a bare list, or
        a dict[...] | None union) but returns a plain dict[str, Any] as-is
        when it can. Both shapes are correct per the SDK; a caller has to
        handle both, so this does.
        """
        result = await mcp_server.mcp.call_tool(name, kwargs)
        self.assertFalse(result.is_error, result.content)
        sc = result.structured_content
        return sc["result"] if sc is not None and "result" in sc and len(sc) == 1 else sc


class SearchAndGetTests(McpTestCase):
    async def test_search_finds_by_title_substring(self) -> None:
        rows = await self.call("search_jobs", query="backend")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Acme")

    async def test_search_query_is_case_insensitive(self) -> None:
        rows = await self.call("search_jobs", query="ACME")
        self.assertEqual(len(rows), 1)

    async def test_search_filters_by_status(self) -> None:
        rows = await self.call("search_jobs", status="new")
        self.assertEqual(rows, [])

    async def test_get_job_returns_full_row(self) -> None:
        job = await self.call("get_job", job_id=self.job_id)
        self.assertEqual(job["title"], "Backend Engineer")

    async def test_get_job_missing_id_returns_none(self) -> None:
        job = await self.call("get_job", job_id=999999)
        self.assertIsNone(job)


class AddJobTests(McpTestCase):
    async def test_add_job_creates_and_scores_against_profile(self) -> None:
        result = await self.call(
            "add_job", title="Platform Engineer", company="Beta Corp",
            url="https://example.com/beta", description="Python and PostgreSQL role.",
        )
        self.assertTrue(result["created"])
        self.assertEqual(result["job"]["source"], "manual")
        self.assertEqual(result["job"]["status"], "scored")
        self.assertIsNotNone(result["job"]["fit_score"])

    async def test_add_job_same_company_title_location_does_not_duplicate(self) -> None:
        # The fixture job's fingerprint is a hand-set literal ("fp-1"), not
        # one Posting.fingerprint() would produce, so dedup against it can't
        # be tested by matching its fields -- call add_job twice instead and
        # check the second call finds the first, which is what dedup means.
        first = await self.call(
            "add_job", title="Platform Engineer", company="Delta Inc", location="Austin, TX",
        )
        second = await self.call(
            "add_job", title="Platform Engineer", company="Delta Inc", location="Austin, TX",
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["job"]["id"], second["job"]["id"])


class TailorTests(McpTestCase):
    async def test_tailor_job_creates_a_drafted_application(self) -> None:
        with mock.patch.object(
            __import__("jobsearch.pipeline", fromlist=["generate"]).generate,
            "generate", stub_generate(CANNED),
        ):
            other_job = db.connect(self.db_path)
            job_id = db.insert_row(
                other_job, "jobs",
                {"source": "greenhouse", "company": "Gamma", "title": "SRE",
                 "discovered_at": db.now(), "fingerprint": "fp-2", "status": "scored"},
            )
            other_job.commit()
            other_job.close()

            result = await self.call("tailor_job", job_id=job_id)
        self.assertIn("application", result)
        self.assertEqual(result["application"]["status"], "drafted")

    async def test_tailor_job_twice_returns_same_application(self) -> None:
        result = await self.call("tailor_job", job_id=self.job_id)
        self.assertEqual(result["application"]["id"], self.app_id)

    async def test_tailor_missing_job_reports_error_not_crash(self) -> None:
        result = await self.call("tailor_job", job_id=999999)
        self.assertIn("error", result)


class QueueTests(McpTestCase):
    async def test_list_queue_default_is_drafted(self) -> None:
        rows = await self.call("list_queue")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.app_id)

    async def test_approve_sets_status_and_timestamp(self) -> None:
        result = await self.call("approve_application", application_id=self.app_id)
        self.assertEqual(result["application"]["status"], "approved")
        self.assertIsNotNone(result["application"]["approved_at"])

    async def test_reject_sets_status(self) -> None:
        result = await self.call("reject_application", application_id=self.app_id)
        self.assertEqual(result["application"]["status"], "rejected")

    async def test_approve_missing_application_reports_error(self) -> None:
        result = await self.call("approve_application", application_id=999999)
        self.assertIn("error", result)


class ProfileAndCompetitionsTests(McpTestCase):
    async def test_profile_summary_reflects_seeded_data(self) -> None:
        result = await self.call("profile_summary")
        self.assertEqual(result["profile"]["full_name"], "Sam Rivera")
        self.assertEqual(result["counts"]["experiences"], 1)
        self.assertEqual(result["skills_evidenced"], 2)

    async def test_list_competitions_empty_by_default(self) -> None:
        rows = await self.call("list_competitions")
        self.assertEqual(rows, [])

    async def test_discover_competitions_saves_what_the_connector_finds(self) -> None:
        from jobsearch.sourcing.competitions import Opportunity

        fake = [Opportunity(name="Test Hack 2026", category="hackathon", source="devpost")]
        with mock.patch.object(
            competitions_sourcing, "discover", return_value=(fake, [])
        ):
            result = await self.call("discover_competitions")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["added"], 1)

        rows = await self.call("list_competitions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Test Hack 2026")


if __name__ == "__main__":
    unittest.main()
