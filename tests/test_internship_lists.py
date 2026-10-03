"""Tests for the internship-list connector and the places it leaves out.

No network: fetch_json is replaced by a function that answers each URL with the shape
that endpoint really returns.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db, pipeline, policy, sourcing  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.sourcing import ats_boards, base, internship_lists  # noqa: E402

LISTINGS_URL = internship_lists.SIMPLIFY_LISTINGS
GH = "https://boards.greenhouse.io/acme/jobs/123?utm_source=Simplify"


def listing(id: str = "L1", **changes: object) -> dict:
    row = {
        "id": id,
        "active": True,
        "is_visible": True,
        "company_name": "Acme",
        "title": "Software Engineer Intern",
        "category": "Software",
        "locations": ["New York, NY"],
        "terms": ["Summer 2027"],
        "degrees": ["Bachelor's"],
        "sponsorship": "Other",
        "date_posted": 1788000000,  # 2026-08-29
        "url": GH,
    }
    row.update(changes)
    return row


def search(**overrides: object):
    raw = {
        "titles": ["Software Engineer", "Data Scientist"],
        "locations": ["United States", "Dubai", "Remote"],
        "exclude_locations": ["India"],
        "exclude_title_keywords": ["senior", "phd"],
    }
    raw.update(overrides)
    return Config.from_dict({"search": raw}).search


class Network:
    """Answers the list itself and each ATS posting; records what was asked."""

    def __init__(self, rows: list[dict], **answers: object) -> None:
        self.rows = rows
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, url: str, **kwargs: object) -> object:
        self.asked.append(url)
        if url == LISTINGS_URL:
            return self.rows
        for fragment, answer in self.answers.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise base.SourceError(f"{url}: 404 -- not stubbed")


def fetch(rows: list[dict], search_config=None, **kwargs: object):
    network = Network(rows, **kwargs.pop("answers", {"boards/acme/jobs/123": {"content": "<p>Build things.</p>"}}))
    with mock.patch.object(internship_lists, "fetch_json", network):
        result = internship_lists.fetch_simplify(search_config or search(), **kwargs)
    return result, network


class FetchSimplifyTests(unittest.TestCase):
    def test_a_listing_becomes_a_posting_with_the_ats_text(self) -> None:
        result, _ = fetch([listing()])
        self.assertEqual(result.errors, [])
        (posting,) = result.postings
        self.assertEqual(posting.source, "simplify")
        self.assertEqual(posting.external_id, "L1")
        self.assertEqual((posting.company, posting.title), ("Acme", "Software Engineer Intern"))
        self.assertEqual(posting.location, "New York, NY")
        self.assertEqual(posting.apply_url, GH)
        self.assertEqual(posting.employment_type, "internship")
        self.assertEqual(posting.posted_at, "2026-08-29")
        self.assertIn("Build things.", posting.description)

    def test_the_listings_own_fields_decide_what_is_kept(self) -> None:
        rows = [
            listing("ok"),
            listing("closed", active=False),
            listing("hidden", is_visible=False),
            listing("hardware", category="Hardware"),
            listing("wrong-season", terms=["Summer 2026"]),
            listing("phd-only", degrees=["PhD"]),
            listing("citizens", sponsorship="U.S. Citizenship is Required"),
            listing("no-visas", sponsorship="Does Not Offer Sponsorship"),
            listing("any-degree", degrees=[]),
        ]
        result, _ = fetch(
            rows,
            terms=["Summer 2027"],
            categories=["Software"],
            degrees=["Bachelor's"],
            exclude_sponsorship=["U.S. Citizenship is Required", "Does Not Offer Sponsorship"],
        )
        self.assertEqual({p.external_id for p in result.postings}, {"ok", "any-degree"})

    def test_nothing_configured_keeps_every_open_listing(self) -> None:
        result, _ = fetch([listing("a", category="Hardware", terms=["N/A"]), listing("b", active=False)])
        self.assertEqual([p.external_id for p in result.postings], ["a"])

    def test_places_and_titles_are_judged_the_way_screen_judges_them(self) -> None:
        rows = [
            listing("mixed", locations=["Mumbai, India", "Dubai - United Arab Emirates", "Austin, TX"]),
            listing("india-only", locations=["Bengaluru, India"]),
            listing("remote", locations=["Remote in USA"]),
            listing("senior", title="Senior Software Engineer Intern"),
            listing("not-a-role-we-want", title="Marketing Intern"),
            listing("phd", title="PhD Software Engineer Intern"),
        ]
        result, _ = fetch(rows)
        by_id = {p.external_id: p for p in result.postings}
        self.assertEqual(set(by_id), {"mixed", "remote"})
        # only the places that would stand are kept, so the stored location is one screen() accepts
        self.assertEqual(by_id["mixed"].location, "Dubai - United Arab Emirates; Austin, TX")
        self.assertTrue(by_id["remote"].remote)
        screen_config = Config.from_dict({"search": {
            "titles": ["Software Engineer"], "locations": ["United States", "Dubai", "Remote"],
            "exclude_locations": ["India"], "min_fit": 0, "max_age_days": 0}})
        for posting in result.postings:
            job = {**posting.to_row("2026-10-03"), "fit_score": None}
            self.assertEqual(policy.screen(job, screen_config, policy.PolicyContext()).action, policy.QUEUE)

    def test_a_closed_posting_is_dropped_and_an_unreachable_one_is_left_for_next_time(self) -> None:
        rows = [
            listing("gone", url="https://boards.greenhouse.io/acme/jobs/1"),
            listing("flaky", url="https://boards.greenhouse.io/acme/jobs/2"),
            listing("fine", url="https://boards.greenhouse.io/acme/jobs/3"),
        ]
        result, _ = fetch(
            rows,
            answers={
                "jobs/1": base.SourceError("x: 404 -- check the board token"),
                "jobs/2": base.SourceError("x: HTTP 500"),
                "jobs/3": {"content": "<p>Real text.</p>"},
            },
        )
        self.assertEqual([p.external_id for p in result.postings], ["fine"])
        self.assertEqual(len(result.errors), 1)
        self.assertIn("next run", result.errors[0])

    def test_an_ats_with_no_open_api_gets_a_summary_not_a_fetch(self) -> None:
        workday = listing("wd", url="https://acme.wd1.myworkdayjobs.com/en-US/careers/job/NYC/x_R1")
        result, network = fetch([workday])
        (posting,) = result.postings
        self.assertEqual(network.asked, [LISTINGS_URL])
        self.assertIn("Software Engineer Intern at Acme", posting.description)
        self.assertIn("Summer 2027", posting.description)
        self.assertIn(base.STUB_MARKER, posting.description)

    def test_lever_and_ashby_text(self) -> None:
        ashby_id = "11111111-2222-3333-4444-555555555555"
        lever_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        rows = [
            listing("a1", url=f"https://jobs.ashbyhq.com/Acme/{ashby_id}/application"),
            listing("a2", title="Data Scientist Intern", url="https://jobs.ashbyhq.com/Acme/99999999-2222-3333-4444-555555555555"),
            listing("lv", url=f"https://jobs.lever.co/acme/{lever_id}"),
        ]
        board = {"jobs": [{"id": ashby_id, "descriptionHtml": "<p>Ashby text.</p>",
                           "compensation": {"compensationTierSummary": "$40/hr"}}]}
        lever = {"text": "x", "descriptionPlain": "Lever text.", "lists": []}
        result, network = fetch(rows, answers={"job-board/Acme": board, f"postings/acme/{lever_id}": lever})
        by_id = {p.external_id: p for p in result.postings}
        # the second Ashby listing is not on the board any more: closed
        self.assertEqual(set(by_id), {"a1", "lv"})
        self.assertIn("Ashby text.", by_id["a1"].description)
        self.assertEqual(by_id["a1"].compensation, "$40/hr")
        self.assertIn("Lever text.", by_id["lv"].description)
        self.assertEqual(sum("job-board/Acme" in url for url in network.asked), 1)  # one board, asked once

    def test_known_listings_are_neither_returned_nor_fetched(self) -> None:
        result, network = fetch([listing("seen"), listing("new", url="https://boards.greenhouse.io/acme/jobs/7")],
                                known={"seen"}, answers={"jobs/7": {"content": "<p>x</p>"}})
        self.assertEqual([p.external_id for p in result.postings], ["new"])
        self.assertFalse(any("jobs/123" in url for url in network.asked))

    def test_the_newest_are_taken_up_to_the_limit(self) -> None:
        rows = [listing(f"r{i}", date_posted=1780000000 + i, url=f"https://boards.greenhouse.io/acme/jobs/{i}") for i in range(5)]
        answers = {f"jobs/{i}": {"content": "<p>x</p>"} for i in range(5)}
        result, _ = fetch(rows, answers=answers, limit=2)
        self.assertEqual([p.external_id for p in result.postings], ["r4", "r3"])

    def test_an_unreachable_list_is_an_error_not_a_crash(self) -> None:
        with mock.patch.object(internship_lists, "fetch_json", side_effect=base.SourceError("boom")):
            result = internship_lists.fetch_simplify(search())
        self.assertEqual(result.postings, [])
        self.assertEqual(result.errors, ["boom"])

    def test_collect_passes_the_config_and_the_known_ids(self) -> None:
        config = Config.from_dict({
            "search": {"titles": ["Software Engineer"]},
            "sources": {"simplify": {"enabled": True, "terms": ["Summer 2027"], "categories": ["Software"],
                                     "degrees": ["Bachelor's"], "exclude_sponsorship": ["X"], "limit": 7}},
        })
        with mock.patch.object(sourcing.internship_lists, "fetch_simplify",
                               return_value=base.SourceResult(source="simplify")) as fetch_simplify:
            sourcing.collect(config, known={"simplify": {"a", "b"}})
        _, kwargs = fetch_simplify.call_args
        self.assertEqual(kwargs["terms"], ["Summer 2027"])
        self.assertEqual(kwargs["limit"], 7)
        self.assertEqual(kwargs["known"], {"a", "b"})
        self.assertEqual(kwargs["exclude_sponsorship"], ["X"])


class CollectOptionsTests(unittest.TestCase):
    def test_arbeitnow_is_told_whether_to_keep_only_remote(self) -> None:
        def run(settings: dict):
            config = Config.from_dict({"sources": {"arbeitnow": {"enabled": True, **settings}}})
            fake = mock.Mock(return_value=base.SourceResult(source="arbeitnow"))
            with mock.patch.dict(sourcing.remote_boards.REMOTE_BOARDS, {"arbeitnow": fake}):
                sourcing.collect(config)
            return fake.call_args.kwargs

        self.assertEqual(run({"limit": 400, "remote_only": False}), {"limit": 400, "remote_only": False})
        self.assertEqual(run({}), {"limit": 100, "remote_only": True})


class ScreeningAnInternshipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Config.from_dict({"search": {
            "titles": ["Software Engineer"], "require_title_keywords": ["intern"], "min_fit": 20, "max_age_days": 0}})
        self.job = {"company": "Acme", "title": "Software Engineer, Summer 2027", "location": "Remote",
                    "remote": 1, "description": "x", "fit_score": 30.0}

    def screen(self, **changes: object) -> str:
        return policy.screen({**self.job, **changes}, self.config, policy.PolicyContext()).action

    def test_a_posting_that_declares_itself_an_internship_meets_the_level_requirement(self) -> None:
        self.assertEqual(self.screen(), policy.SKIP)
        self.assertEqual(self.screen(employment_type="internship"), policy.QUEUE)
        self.assertEqual(self.screen(employment_type="full_time"), policy.SKIP)

    def test_unscored_is_not_a_low_score(self) -> None:
        self.assertEqual(self.screen(employment_type="internship", fit_score=None), policy.QUEUE)
        self.assertEqual(self.screen(employment_type="internship", fit_score=3.0), policy.SKIP)


class ScoringAndStorageTests(unittest.TestCase):
    def test_a_lead_with_no_posting_text_is_left_unscored_and_the_type_is_stored(self) -> None:
        class Graph:
            def match_docs(self):
                return []

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = db.connect(Path(tmp.name) / "t.db")
        self.addCleanup(conn.close)
        lead = base.Posting("simplify", "1", "Acme", "Software Engineer Intern",
                            description=f"Software Engineer Intern at Acme. {base.STUB_MARKER}.",
                            employment_type="internship")
        full = base.Posting("greenhouse", "2", "Beta", "Software Engineer",
                            description="Requirements: python " * 40)
        self.assertEqual(sourcing.store(conn, [lead, full]), (2, 0))
        pipeline.score_jobs(conn, Graph(), pipeline.RunReport())
        rows = {r["source"]: r for r in conn.execute("SELECT * FROM jobs")}
        self.assertIsNone(rows["simplify"]["fit_score"])
        self.assertEqual(rows["simplify"]["status"], "scored")
        self.assertEqual(rows["simplify"]["employment_type"], "internship")
        self.assertIsNotNone(rows["greenhouse"]["fit_score"])
        self.assertIsNone(rows["greenhouse"]["employment_type"])

    def test_an_existing_database_gains_the_column(self) -> None:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT NOT NULL, title TEXT)")
        self.assertIn("jobs.employment_type", db.add_missing_columns(conn))
        self.assertEqual(db.add_missing_columns(conn), [])


class SyncOpenTests(unittest.TestCase):
    """What a source stops listing closes; what comes back reopens."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self._tmp.name) / "t.db")
        postings = [base.Posting("greenhouse", str(i), "Acme", f"Role {i}") for i in range(1, 7)]
        postings.append(base.Posting("remotive", "r1", "Beta", "Remote role"))
        sourcing.store(self.conn, postings)
        for external_id, status in {"1": "scored", "2": "scored", "3": "scored", "4": "new",
                                    "5": "skipped", "6": "tailored", "r1": "scored"}.items():
            self.conn.execute("UPDATE jobs SET status = ? WHERE external_id = ?", (status, external_id))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def statuses(self) -> dict[str, str]:
        return {r["external_id"]: r["status"] for r in self.conn.execute("SELECT external_id, status FROM jobs")}

    def listing(self, source: str, ids: list[str], **flags: object) -> sourcing.SourcingReport:
        report = sourcing.SourcingReport()
        report.absorb(base.SourceResult(
            source=source, postings=[base.Posting(source, i, "Acme", f"Role {i}") for i in ids], **flags))
        return report

    def test_what_a_complete_source_no_longer_lists_closes(self) -> None:
        report = self.listing("greenhouse", ["1", "2", "5", "6"], complete=True)
        self.assertEqual(sourcing.sync_open(self.conn, report), (2, 0))
        # 3 (scored) and 4 (new) closed; skipped, tailored and other sources are left alone
        self.assertEqual(self.statuses(), {"1": "scored", "2": "scored", "3": "closed", "4": "closed",
                                           "5": "skipped", "6": "tailored", "r1": "scored"})

    def test_a_source_that_did_not_list_everything_closes_nothing(self) -> None:
        for flags in ({"complete": False}, {"complete": True, "errors": ["one board failed"]}):
            with self.subTest(flags):
                report = self.listing("greenhouse", ["1"], **flags)
                self.assertEqual(sourcing.sync_open(self.conn, report), (0, 0))
        self.assertNotIn("closed", self.statuses().values())

    def test_a_nearly_empty_answer_is_an_outage_not_a_mass_closing(self) -> None:
        report = self.listing("greenhouse", [], complete=True)
        self.assertEqual(sourcing.sync_open(self.conn, report), (0, 0))
        self.assertNotIn("closed", self.statuses().values())

    def test_a_posting_that_comes_back_reopens_unscored(self) -> None:
        self.conn.execute("UPDATE jobs SET status = 'closed', fit_score = 40 WHERE external_id = '3'")
        report = self.listing("greenhouse", ["1", "2", "3", "4", "5", "6"], complete=True)
        self.assertEqual(sourcing.sync_open(self.conn, report), (0, 1))
        row = self.conn.execute("SELECT status, fit_score FROM jobs WHERE external_id = '3'").fetchone()
        self.assertEqual((row["status"], row["fit_score"]), ("new", None))

    def test_ids_a_source_reports_closed_close_if_they_are_open_here(self) -> None:
        report = self.listing("greenhouse", [], closed_ids=["1", "5", "6", "nope"])
        self.assertEqual(sourcing.sync_open(self.conn, report), (1, 0))
        self.assertEqual(self.statuses()["1"], "closed")
        self.assertEqual(self.statuses()["5"], "skipped")

    def test_the_board_connectors_say_whether_they_listed_everything(self) -> None:
        payload = {"jobs": [{"id": 1, "title": "Engineer Intern", "location": {"name": "NYC"}}]}
        with mock.patch.object(ats_boards, "fetch_json", return_value=payload):
            self.assertTrue(ats_boards.fetch_greenhouse(["acme"]).complete)
        with mock.patch.object(ats_boards, "fetch_json", side_effect=base.SourceError("404")):
            self.assertFalse(ats_boards.fetch_greenhouse(["acme"]).complete)
        with mock.patch.object(ats_boards, "fetch_json", return_value=[]):
            self.assertTrue(ats_boards.fetch_lever(["acme"]).complete)

    def test_the_internship_list_reports_what_it_closed(self) -> None:
        rows = [listing(f"o{i}", url="https://acme.wd1.myworkdayjobs.com/x") for i in range(1000)]
        rows.append(listing("shut", active=False))
        result, _ = fetch(rows, known={"shut", "o1", "vanished"}, limit=0)
        self.assertEqual(result.closed_ids, ["shut", "vanished"])
        # a cut-off file closes nothing
        result, _ = fetch(rows[:10], known={"vanished"}, limit=0)
        self.assertEqual(result.closed_ids, [])


if __name__ == "__main__":
    unittest.main()
