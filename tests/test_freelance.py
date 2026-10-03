"""Freelance and contract gigs: how sources type them, how they are screened, listed, and left alone.

No network: fetch_json is faked with the shape each endpoint really returns.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db, pipeline, policy, sourcing  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.sourcing import ats_boards, base  # noqa: E402
from jobsearch.sourcing import remote_boards as rb  # noqa: E402

TODAY = date.today()


class EmploymentTypeTests(unittest.TestCase):
    def test_a_sources_own_words_map_to_one_kind(self) -> None:
        cases = {
            "full_time": "full_time", "Full-Time": "full_time", "FullTime": "full_time",
            "part_time": "part_time", "PartTime": "part_time",
            "contract": "contract", "Contractor": "contract", "Temporary": "contract",
            "freelance": "freelance", "Intern": "internship", "Internship": "internship",
            "Student college": "internship", "Trainee": "internship",
        }
        for raw, kind in cases.items():
            with self.subTest(raw):
                self.assertEqual(base.employment_type(raw), kind)

    def test_lists_are_read_whole_and_the_most_specific_kind_wins(self) -> None:
        self.assertEqual(base.employment_type(["Student", "Intern", "Part time"]), "internship")
        self.assertEqual(base.employment_type(["Experienced", "Permanent", "Full time"]), "full_time")
        self.assertEqual(base.employment_type(["Contract", "Part-Time"]), "contract")
        self.assertEqual(base.employment_type("freelance", ["Full-Time"]), "freelance")

    def test_nothing_known_is_none(self) -> None:
        for raw in (None, "", [], ["Senior"], "Remote"):
            with self.subTest(raw):
                self.assertIsNone(base.employment_type(raw))


class SourcesTypePostingsTests(unittest.TestCase):
    def test_the_aggregators_pass_on_what_they_say(self) -> None:
        remotive = {"jobs": [
            {"id": 1, "title": "Python scripts", "company_name": "A", "job_type": "freelance", "url": "u1"},
            {"id": 2, "title": "Engineer", "company_name": "B", "job_type": "full_time", "url": "u2"},
        ]}
        with mock.patch.object(rb, "fetch_json", return_value=remotive):
            types = [p.employment_type for p in rb.fetch_remotive().postings]
        self.assertEqual(types, ["freelance", "full_time"])

        jobicy = {"jobs": [{"id": 1, "jobTitle": "Dev", "companyName": "A", "jobType": ["Contract"], "url": "u"}]}
        with mock.patch.object(rb, "fetch_json", return_value=jobicy):
            self.assertEqual(rb.fetch_jobicy().postings[0].employment_type, "contract")

        himalayas = {"jobs": [
            {"guid": "g1", "title": "T", "companyName": "A", "employmentType": "Contractor"},
            {"guid": "g2", "title": "T", "companyName": "A", "employmentType": "Intern"},
        ]}
        with mock.patch.object(rb, "fetch_json", return_value=himalayas):
            types = [p.employment_type for p in rb.fetch_himalayas().postings]
        self.assertEqual(types, ["contract", "internship"])

    def test_lever_and_ashby_pass_on_the_commitment(self) -> None:
        lever = [{"id": "1", "text": "Dev", "categories": {"location": "NYC", "commitment": "Contract"}}]
        with mock.patch.object(ats_boards, "fetch_json", return_value=lever):
            self.assertEqual(ats_boards.fetch_lever(["acme"]).postings[0].employment_type, "contract")
        ashby = {"name": "Acme", "jobs": [{"id": "1", "title": "Dev", "employmentType": "Intern"}]}
        with mock.patch.object(ats_boards, "fetch_json", return_value=ashby):
            self.assertEqual(ats_boards.fetch_ashby(["acme"]).postings[0].employment_type, "internship")


LISTING = {
    "id": "abc", "slug": "build-a-scanner", "title": "Build a port scanner", "type": "bounty",
    "token": "USDC", "rewardAmount": 500, "compensationType": "fixed", "deadline": "2026-10-20T18:00:00.000Z",
    "sponsor": {"name": "Acme DAO"},
}
DETAIL = {
    "description": "<p>Write a <b>Python</b> port scanner.</p>", "requirements": "<p>Tests included.</p>",
    "region": "Global", "skills": [{"skills": "Development", "subskills": ["Backend", "Security"]}],
    "publishedAt": "2026-10-01T10:00:00.000Z",
}


class SuperteamTests(unittest.TestCase):
    def fetch(self, rows: list, details: dict) -> base.SourceResult:
        def fake(url: str, **kwargs: object):
            if url == rb.SUPERTEAM_API:
                return rows
            slug = url.rsplit("/", 1)[-1]
            if isinstance(details.get(slug), Exception):
                raise details[slug]
            return details.get(slug, {})

        with mock.patch.object(rb, "fetch_json", fake):
            return rb.fetch_superteam()

    def test_a_global_listing_becomes_a_dollar_paid_gig(self) -> None:
        result = self.fetch([LISTING], {"build-a-scanner": DETAIL})
        (gig,) = result.postings
        self.assertEqual(result.errors, [])
        self.assertTrue(result.complete)
        self.assertEqual(gig.source, "superteam")
        self.assertEqual((gig.company, gig.title), ("Acme DAO", "Build a port scanner"))
        self.assertEqual(gig.employment_type, "freelance")
        self.assertEqual(gig.compensation, "USDC 500")
        self.assertEqual((gig.deadline, gig.posted_at), ("2026-10-20", "2026-10-01"))
        self.assertTrue(gig.remote)
        self.assertEqual(gig.url, "https://earn.superteam.fun/listing/build-a-scanner")
        for text in ("Write a Python port scanner.", "Tests included.", "Development (Backend, Security)", "Reward: USDC 500"):
            self.assertIn(text, gig.description)

    def test_a_range_is_shown_as_one(self) -> None:
        ranged = {**LISTING, "compensationType": "range", "minRewardAsk": 1000, "maxRewardAsk": 2500}
        (gig,) = self.fetch([ranged], {"build-a-scanner": DETAIL}).postings
        self.assertEqual(gig.compensation, "USDC 1,000-2,500")

    def test_regional_listings_and_hackathons_are_left_out(self) -> None:
        rows = [
            LISTING,
            {**LISTING, "id": "np", "slug": "nepal-only"},
            {**LISTING, "id": "hk", "slug": "a-hackathon", "type": "hackathon"},
        ]
        details = {"build-a-scanner": DETAIL, "nepal-only": {**DETAIL, "region": "Nepal"}}
        result = self.fetch(rows, details)
        self.assertEqual([p.external_id for p in result.postings], ["abc"])

    def test_a_listing_with_no_region_is_open_to_anyone(self) -> None:
        result = self.fetch([LISTING], {"build-a-scanner": {**DETAIL, "region": None}})
        self.assertEqual(len(result.postings), 1)

    def test_a_page_that_cannot_be_read_is_an_error_and_the_list_is_not_complete(self) -> None:
        rows = [LISTING, {**LISTING, "id": "x", "slug": "broken"}]
        result = self.fetch(rows, {"build-a-scanner": DETAIL, "broken": base.SourceError("boom")})
        self.assertEqual([p.external_id for p in result.postings], ["abc"])
        self.assertEqual(result.errors, ["boom"])
        self.assertFalse(result.complete)

    def test_a_dead_list_is_an_error_not_a_crash(self) -> None:
        with mock.patch.object(rb, "fetch_json", side_effect=base.SourceError("503")):
            result = rb.fetch_superteam()
        self.assertEqual((result.postings, result.errors), ([], ["503"]))
        with mock.patch.object(rb, "fetch_json", return_value={"error": "x"}):
            self.assertEqual(len(rb.fetch_superteam().errors), 1)


def gig(**changes: object) -> dict:
    job = {
        "company": "Acme", "title": "Python developer for a scraping project", "location": "Remote",
        "remote": 1, "description": "x", "fit_score": 12.0, "employment_type": "freelance",
        "posted_at": None, "deadline": None,
    }
    job.update(changes)
    return job


class ScreeningAGigTests(unittest.TestCase):
    def setUp(self) -> None:
        raw = {"titles": ["Software Engineer"], "require_title_keywords": ["intern"],
               "exclude_title_keywords": ["senior"], "locations": ["United States", "Remote"],
               "min_fit": 5, "max_age_days": 0}
        self.on = Config.from_dict({"search": {**raw, "include_freelance": True}})
        self.off = Config.from_dict({"search": raw})

    def screen(self, config: Config, **changes: object) -> policy.Decision:
        return policy.screen(gig(**changes), config, policy.PolicyContext())

    def test_a_gig_skips_the_title_and_level_rules_when_freelance_is_included(self) -> None:
        self.assertEqual(self.screen(self.on).action, policy.QUEUE)
        for kind in ("freelance", "contract"):
            self.assertEqual(self.screen(self.on, employment_type=kind).action, policy.QUEUE)
        self.assertEqual(self.screen(self.off).action, policy.SKIP)  # as before: not a role we asked for

    def test_a_job_is_still_held_to_them(self) -> None:
        for kind in (None, "full_time", "part_time"):
            self.assertEqual(self.screen(self.on, employment_type=kind).action, policy.SKIP)

    def test_a_gig_is_still_held_to_the_rest(self) -> None:
        self.assertEqual(self.screen(self.on, title="Senior Python developer").action, policy.SKIP)
        self.assertEqual(self.screen(self.on, fit_score=1.0).action, policy.SKIP)
        self.assertEqual(self.screen(self.on, description="5+ years of experience", fit_score=12.0).action, policy.QUEUE)
        self.on.search.max_experience_years = 1
        self.assertEqual(self.screen(self.on, description="5+ years of experience").action, policy.SKIP)

    def test_a_deadline_that_has_passed_ends_it(self) -> None:
        late = (TODAY - timedelta(days=1)).isoformat()
        soon = (TODAY + timedelta(days=3)).isoformat()
        decision = self.screen(self.on, deadline=late)
        self.assertEqual(decision.action, policy.SKIP)
        self.assertIn("deadline", decision.reasons[0])
        self.assertEqual(self.screen(self.on, deadline=soon).action, policy.QUEUE)
        self.assertEqual(self.screen(self.on, deadline=TODAY.isoformat()).action, policy.QUEUE)

    def test_the_setting_comes_from_the_config(self) -> None:
        self.assertTrue(Config.from_dict({"search": {"include_freelance": True}}).search.include_freelance)
        self.assertFalse(Config.from_dict({}).search.include_freelance)


class TheRunLeavesGigsAloneTests(unittest.TestCase):
    def test_a_gig_is_listed_but_never_tailored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "t.db")
            try:
                db.set_profile_field(conn, "name", "Sam Rivera")
                db.set_profile_field(conn, "email", "sam@example.com")
                org = db.upsert_organization(conn, "Northwind")
                exp = db.insert_row(conn, "experiences", {"organization_id": org, "title": "Engineer",
                                                          "start_date": "2023-02", "verified": 1})
                bullet = db.insert_row(conn, "achievements", {"experience_id": exp, "title": "Built",
                                                              "description": "Python service on PostgreSQL.", "verified": 1})
                db.link_skills_to(conn, ["Python", "PostgreSQL"], "achievement", bullet, verified=1)
                text = "Requirements\n- Python and PostgreSQL scripting\n- Web scraping with Python"
                sourcing.store(conn, [base.Posting("remotive", "g1", "Acme", "Python scraping project",
                                                   location="Remote", description=text, employment_type="freelance")])
                config = Config.from_dict({
                    "search": {"titles": ["Chef"], "min_fit": 0, "max_age_days": 0, "include_freelance": True},
                    "limits": {"max_tailor_per_run": 5},
                })
                with mock.patch.object(pipeline.generate, "generate", side_effect=AssertionError("no model call")):
                    report = pipeline.run(conn, config, skip_sourcing=True)
                row = conn.execute("SELECT status FROM jobs").fetchone()
                self.assertEqual((report.tailored, report.screened_out, row["status"]), (0, 0, "scored"))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
