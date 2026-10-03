"""Tests for the remote-job aggregator connectors.

No network. Each connector is fed the response shape its API actually returns,
captured from a live call, so a field rename upstream shows up here rather than
as an empty run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch.policy import available_channel  # noqa: E402
from jobsearch.sourcing import remote_boards as rb  # noqa: E402
from jobsearch.sourcing.base import employment_type  # noqa: E402

REMOTIVE = {
    "jobs": [
        {
            "id": 2090989,
            "url": "https://remotive.com/remote-jobs/medical/assistant-2090989",
            "title": "Assistant Account Payable",
            "company_name": "The Obesity Society",
            "candidate_required_location": "USA",
            "salary": "",
            "description": "<p>Handle <strong>invoices</strong></p>",
            "publication_date": "2026-08-12T06:36:49",
        }
    ]
}

REMOTEOK = [
    {"legal": "attribution notice, no id"},
    {
        "id": "1136579",
        "company": "Resourceful Talent Group",
        "position": "Product Manager",
        "location": "",
        "url": "https://remoteOK.com/remote-jobs/x",
        "apply_url": "https://remoteOK.com/remote-jobs/x",
        "description": "<p>We are looking for</p>",
        "salary_min": 0,
        "salary_max": 0,
        "date": "2026-08-13T00:00:03+00:00",
    },
]

HIMALAYAS = {
    "jobs": [
        {
            "guid": "abc",
            "title": "Creative Producer",
            "companyName": "Priorities USA",
            "applicationLink": "https://himalayas.app/companies/x/jobs/y",
            "locationRestrictions": ["United States"],
            "excerpt": "About the job",
            "minSalary": 80,
            "maxSalary": 150,
            "currency": "USD",
            "salaryPeriod": "hourly",
            "pubDate": "2026-08-14T00:00:00+00:00",
        }
    ]
}

ARBEITNOW = {
    "data": (
        [{"slug": f"local{i}", "company_name": "X", "title": "Onsite", "remote": False,
          "url": "https://www.arbeitnow.com/jobs/local", "location": "Berlin",
          "description": "<p>x</p>", "created_at": "1786698032"} for i in range(30)]
        + [{"slug": "student1", "company_name": "Ommax", "title": "Intern AI & Data Engineering (m/f/d)",
            "remote": False, "url": "https://www.arbeitnow.com/jobs/student1", "location": "Munich",
            "job_types": ["Student", "Intern", "Full time"], "description": "<p>z</p>",
            "created_at": "1786698032"}]
        + [{"slug": "remote1", "company_name": "Lassie", "title": "Growth Manager",
            "remote": True, "url": "https://www.arbeitnow.com/jobs/remote1",
            "location": "Remote", "description": "<p>y</p>", "created_at": "1786698032"}]
    )
}


def _patch(payload):
    return mock.patch.object(rb, "fetch_json", return_value=payload)


class RemotiveTests(unittest.TestCase):
    def test_maps_fields_and_strips_html(self) -> None:
        with _patch(REMOTIVE):
            result = rb.fetch_remotive(limit=5)
        self.assertEqual(result.errors, [])
        job = result.postings[0]
        self.assertEqual(job.company, "The Obesity Society")
        self.assertEqual(job.title, "Assistant Account Payable")
        self.assertEqual(job.posted_at, "2026-08-12")
        self.assertTrue(job.remote)
        self.assertNotIn("<p>", job.description)
        self.assertIn("invoices", job.description)

    def test_an_empty_salary_becomes_none_not_an_empty_string(self) -> None:
        with _patch(REMOTIVE):
            self.assertIsNone(rb.fetch_remotive().postings[0].compensation)


class RemoteOkTests(unittest.TestCase):
    def test_the_attribution_row_is_skipped(self) -> None:
        with _patch(REMOTEOK):
            result = rb.fetch_remoteok()
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.postings[0].title, "Product Manager")

    def test_zero_salaries_mean_unstated_not_unpaid(self) -> None:
        with _patch(REMOTEOK):
            self.assertIsNone(rb.fetch_remoteok().postings[0].compensation)


class HimalayasTests(unittest.TestCase):
    def test_salary_range_is_rendered_with_its_period(self) -> None:
        with _patch(HIMALAYAS):
            job = rb.fetch_himalayas().postings[0]
        self.assertEqual(job.compensation, "USD 80-150 / hourly")
        self.assertEqual(job.location, "United States")


class ArbeitnowTests(unittest.TestCase):
    def test_the_whole_payload_is_scanned_for_remote_roles(self) -> None:
        # The live bug: only a small fraction of this board is remote, so
        # slicing before filtering returned nothing at all.
        with _patch(ARBEITNOW):
            result = rb.fetch_arbeitnow(limit=5, remote_only=True)
        self.assertEqual(len(result.postings), 1)
        self.assertEqual(result.postings[0].company, "Lassie")

    def test_onsite_roles_are_kept_when_not_filtering(self) -> None:
        with _patch(ARBEITNOW):
            result = rb.fetch_arbeitnow(limit=5, remote_only=False)
        self.assertEqual(len(result.postings), 5)

    def test_student_postings_are_typed_from_the_boards_own_tags(self) -> None:
        with _patch(ARBEITNOW):
            result = rb.fetch_arbeitnow(limit=100, remote_only=False)
        types = {p.external_id: p.employment_type for p in result.postings}
        self.assertEqual(types["student1"], "internship")
        self.assertIsNone(types["local0"])
        self.assertIsNone(types["remote1"])
        self.assertEqual(employment_type(["Entry", "Working student", "Part time"]), "internship")
        self.assertEqual(employment_type(["Experienced", "Permanent", "Full time"]), "full_time")
        self.assertIsNone(employment_type(None))


class FailureTests(unittest.TestCase):
    def test_a_board_being_down_is_reported_not_raised(self) -> None:
        with mock.patch.object(rb, "fetch_json", side_effect=rb.SourceError("503")):
            result = rb.fetch_remotive()
        self.assertEqual(result.postings, [])
        self.assertTrue(result.errors)

    def test_fetch_boards_names_an_unknown_board(self) -> None:
        results = rb.fetch_boards(["nope"])
        self.assertTrue(results[0].errors)
        self.assertIn("unknown board", results[0].errors[0])

    def test_one_broken_board_does_not_stop_the_others(self) -> None:
        with mock.patch.dict(
            rb.REMOTE_BOARDS,
            {"boom": mock.Mock(side_effect=RuntimeError("kaboom"))},
            clear=False,
        ):
            results = rb.fetch_boards(["boom"])
        self.assertTrue(results[0].errors)
        self.assertIn("kaboom", results[0].errors[0])


class ChannelSelectionTests(unittest.TestCase):
    """A board listing is not an ATS form, whatever its apply_url looks like."""

    class _Ats:
        enabled = True

    class _Dispatch:
        channel_order = ["ats_form", "email"]
        ats = None

        class email:
            enabled = False

    class _Config:
        dispatch = None

    def _config(self):
        config = self._Config()
        dispatch = self._Dispatch()
        dispatch.ats = self._Ats()
        config.dispatch = dispatch
        return config

    def test_a_real_ats_url_selects_the_form_channel(self) -> None:
        job = {"apply_url": "https://job-boards.greenhouse.io/figma/jobs/1"}
        self.assertEqual(available_channel(job, self._config()), "ats_form")

    def test_an_aggregator_listing_does_not(self) -> None:
        # Launching a browser to discover the host is unrecognized, once per
        # posting, is the failure this prevents.
        for url in (
            "https://remoteOK.com/remote-jobs/remote-product-manager",
            "https://remotive.com/remote-jobs/sales/inside-sales-2086540",
            "https://himalayas.app/companies/x/jobs/y",
        ):
            with self.subTest(url=url):
                self.assertIsNone(available_channel({"apply_url": url}, self._config()))


if __name__ == "__main__":
    unittest.main()
