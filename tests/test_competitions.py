"""The Unstop connector. No network: `fetch_json` is replaced with pages shaped like its public
listing (made-up events, the same fields)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch.sourcing import competitions as comp  # noqa: E402
from jobsearch.sourcing.base import SourceError  # noqa: E402


def row(n: int = 1, **over):
    """One listing row, with the fields the connector reads."""
    base = {
        "id": n,
        "title": f"Sample Hack {n}",
        "type": "hackathons",
        "subtype": "online_coding_challenge",
        "public_url": f"hackathons/sample-hack-{n}-{n}",
        "region": "online",
        "organisation": {"name": "Example Institute of Technology"},
        "regnRequirements": {
            "end_regn_dt": "2026-10-19T00:00:00+05:30",
            "min_team_size": 1,
            "max_team_size": 4,
        },
        "end_date": "2026-10-20T11:05:00+05:30",
        "filters": [
            {"type": "eligible", "name": "Engineering Students"},
            {"type": "eligible", "name": "Undergraduate"},
        ],
        "payment_services": [],
        "prizes": [],
        "workfunction": [{"name": "Software Development"}],
        "required_skills": [{"skill_name": "Python"}],
        "address_with_country_logo": {"address": "", "city": "", "state": "", "country": None},
    }
    base.update(over)
    return base


def pages(by_kind: dict[str, list[list[dict]]]):
    """A fetch_json that serves `by_kind[kind][page - 1]` and records what it was asked."""
    asked: list[dict] = []

    def fetch(url, *, params=None, **_):
        asked.append(dict(params or {}))
        batches = by_kind.get(params["opportunity"], [])
        page = params["page"]
        rows = batches[page - 1] if page <= len(batches) else []
        return {"data": {"data": rows, "last_page": max(len(batches), 1), "total": sum(map(len, batches))}}

    fetch.asked = asked
    return fetch


def read(by_kind, **kwargs):
    fetch = pages(by_kind)
    with mock.patch.object(comp, "fetch_json", fetch):
        return list(comp.unstop(**kwargs)), fetch


class MappingTests(unittest.TestCase):
    def test_a_hackathon_carries_what_the_card_shows(self) -> None:
        found, _ = read({"hackathons": [[row(7)]]})
        (opp,) = found
        self.assertEqual(opp.name, "Sample Hack 7")
        self.assertEqual(opp.category, "hackathon")
        self.assertEqual(opp.url, "https://unstop.com/hackathons/sample-hack-7-7")
        self.assertEqual(opp.apply_url, opp.url)
        self.assertEqual(opp.team_size, "1-4")
        self.assertEqual(opp.source, "unstop")
        self.assertEqual(opp.description, "by Example Institute of Technology -- online -- open to Engineering Students, Undergraduate")
        self.assertEqual(opp.tracks, ["Software Development", "Python"])

    def test_the_deadline_is_the_last_day_not_the_midnight_after_it(self) -> None:
        # 2026-10-19T00:00:00+05:30 is the end of the 18th; Unstop's own card counts down to that.
        (opp,), _ = read({"hackathons": [[row()]]})
        self.assertEqual(opp.deadline, "2026-10-18")
        # a stamp that is not midnight is the day it says
        later = row(regnRequirements={"end_regn_dt": "2026-10-19T18:30:00+05:30", "max_team_size": 2})
        (opp,), _ = read({"hackathons": [[later]]})
        self.assertEqual(opp.deadline, "2026-10-19")

    def test_it_says_when_the_event_runs_to_if_that_is_after_registration_closes(self) -> None:
        (opp,), _ = read({"hackathons": [[row()]]})
        self.assertEqual(opp.period, "until Oct 20, 2026")
        same = row(end_date="2026-10-19T00:00:00+05:30")
        (opp,), _ = read({"hackathons": [[same]]})
        self.assertIsNone(opp.period)

    def test_team_sizes(self) -> None:
        for low, high, wanted in ((1, 1, "solo"), (2, 4, "2-4"), (3, 3, "3"), (None, 5, "1-5"), (None, None, None)):
            with self.subTest(low=low, high=high):
                reg = {"end_regn_dt": "2026-10-19T18:00:00+05:30", "min_team_size": low, "max_team_size": high}
                (opp,), _ = read({"hackathons": [[row(regnRequirements=reg)]]})
                self.assertEqual(opp.team_size, wanted)

    def test_who_may_enter(self) -> None:
        everyone = row(filters=[{"type": "eligible", "name": "All"}])
        (opp,), _ = read({"hackathons": [[everyone]]})
        self.assertIn("open to everyone", opp.description)
        many = row(filters=[{"type": "eligible", "name": n} for n in "ABCDEFG"])
        (opp,), _ = read({"hackathons": [[many]]})
        self.assertIn("open to A, B, C, D and 3 more", opp.description)
        # a filter that is not about who may enter is not eligibility
        other = row(filters=[{"type": "domain", "name": "Finance"}])
        (opp,), _ = read({"hackathons": [[other]]})
        self.assertNotIn("open to", opp.description)

    def test_fee_and_biggest_prize(self) -> None:
        paid = row(
            payment_services=[{"amount": 400}, {"amount": 250}],
            prizes=[{"cash": 50000, "currency": "fa-rupee"}, {"cash": 125000, "currency": "fa-rupee"}, {"cash": 0}],
        )
        (opp,), _ = read({"hackathons": [[paid]]})
        self.assertIn("fee 250", opp.description)
        self.assertIn("prize up to ₹125,000", opp.description)
        self.assertEqual(opp.prize, "₹125,000")
        (opp,), _ = read({"hackathons": [[row(prizes=[{"cash": 900, "currency": "fa-dollar"}])]]})
        self.assertEqual(opp.prize, "$900")
        (opp,), _ = read({"hackathons": [[row(prizes=[{"cash": 900, "currency": "fa-mystery"}])]]})
        self.assertEqual(opp.prize, "900")

    def test_where_it_happens(self) -> None:
        town = {"address": "Some University", "city": "Jaipur", "state": "Rajasthan", "country": {"name": "India"}}
        cases = (
            ({"region": "online"}, "online"),
            ({"region": "offline", "address_with_country_logo": town}, "Jaipur, Rajasthan, India"),
            ({"region": "hybrid", "address_with_country_logo": town}, "hybrid, Jaipur, Rajasthan, India"),
            ({"region": "offline"}, "on site"),
        )
        for over, wanted in cases:
            with self.subTest(wanted=wanted):
                (opp,), _ = read({"hackathons": [[row(**over)]]})
                self.assertIn(f"-- {wanted} --", opp.description)

    def test_categories(self) -> None:
        cases = (
            (row(), "hackathon"),
            (row(type="competitions", subtype="case_competition", title="Strategy Sprint"), "case_competition"),
            (row(type="competitions", subtype="innovation_challenge", title="Bridge Challenge"), "other"),
            (row(type="competitions", subtype="case_competition", title="Equity Research and Trading Cup"), "finance_competition"),
        )
        for sample, wanted in cases:
            with self.subTest(title=sample["title"]):
                found, _ = read({"hackathons": [[sample]] if sample["type"] == "hackathons" else [], "competitions": [[sample]]})
                self.assertEqual(found[0].category, wanted)


class SelectionTests(unittest.TestCase):
    def test_school_only_events_are_left_out(self) -> None:
        school = row(1, filters=[{"type": "eligible", "name": "School Students"}])
        mixed = row(2, filters=[{"type": "eligible", "name": "School Students"}, {"type": "eligible", "name": "Undergraduate"}])
        found, _ = read({"hackathons": [[school, mixed]]})
        self.assertEqual([o.name for o in found], ["Sample Hack 2"])

    def test_online_only(self) -> None:
        found, _ = read({"hackathons": [[row(1, region="offline"), row(2, region="hybrid"), row(3)]]}, online_only=True)
        self.assertEqual([o.name for o in found], ["Sample Hack 3"])

    def test_the_competitions_list_gives_case_competitions_and_innovation_challenges_only(self) -> None:
        listing = [
            row(10, type="competitions", subtype="case_competition", title="Case One"),
            row(11, type="competitions", subtype="general_competition", title="Dance Off"),
            row(12, type="quizzes", subtype="", title="Trivia Night"),
            row(13, type="competitions", subtype="innovation_challenge", title="Bridge Challenge"),
        ]
        found, _ = read({"hackathons": [], "competitions": [listing]})
        self.assertEqual([o.name for o in found], ["Case One", "Bridge Challenge"])

    def test_a_hackathon_in_both_lists_is_read_once(self) -> None:
        found, _ = read({"hackathons": [[row(1), row(2)]], "competitions": [[row(2), row(3)]]})
        self.assertEqual([o.name for o in found], ["Sample Hack 1", "Sample Hack 2", "Sample Hack 3"])

    def test_a_row_without_a_title_or_a_link_is_skipped(self) -> None:
        found, _ = read({"hackathons": [[row(1, title=""), row(2, public_url=""), row(3)]]})
        self.assertEqual([o.name for o in found], ["Sample Hack 3"])


class PagingTests(unittest.TestCase):
    def test_it_walks_the_pages_and_stops_at_the_last_one(self) -> None:
        by_kind = {"hackathons": [[row(1)], [row(2)], [row(3)]], "competitions": []}
        found, fetch = read(by_kind)
        self.assertEqual(len(found), 3)
        asked = [(p["opportunity"], p["page"]) for p in fetch.asked]
        self.assertEqual(asked, [("hackathons", 1), ("hackathons", 2), ("hackathons", 3), ("competitions", 1)])
        # what its own pages ask for, and only what is open
        self.assertTrue(all(p["per_page"] == 18 and p["oppstatus"] == "open" for p in fetch.asked))

    def test_it_stops_at_the_limit(self) -> None:
        by_kind = {"hackathons": [[row(i) for i in range(1, 6)], [row(i) for i in range(6, 11)]]}
        found, fetch = read(by_kind, limit=3)
        self.assertEqual(len(found), 3)
        self.assertEqual(len(fetch.asked), 1)  # the second page was never asked for

    def test_an_empty_answer_ends_a_list(self) -> None:
        found, fetch = read({"hackathons": [], "competitions": []})
        self.assertEqual(found, [])
        self.assertEqual(len(fetch.asked), 2)  # one question per list, then done

    def test_an_answer_that_is_not_the_listing_is_an_empty_one(self) -> None:
        with mock.patch.object(comp, "fetch_json", return_value=["not", "a", "dict"]):
            self.assertEqual(list(comp.unstop()), [])


class DiscoverTests(unittest.TestCase):
    def test_a_failure_part_way_keeps_what_was_read_and_is_reported(self) -> None:
        calls = {"n": 0}

        def fetch(url, *, params=None, **_):
            calls["n"] += 1
            if "unstop" not in url:
                return {"hackathons": []}  # Devpost: nothing open
            if calls["n"] > 2:
                raise SourceError("unstop: HTTP 503")
            return {"data": {"data": [row(calls["n"])], "last_page": 5}}

        with mock.patch.object(comp, "fetch_json", fetch):
            found, errors = comp.discover(include_manual=False)
        self.assertEqual([o.source for o in found], ["unstop"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("unstop:"))

    def test_unstop_is_a_feed_now_not_a_bookmark(self) -> None:
        names = [m.name for m in comp.MANUAL_PLATFORMS]
        self.assertFalse(any("Unstop" in n for n in names))
        self.assertTrue(any("Devfolio" in n for n in names))
        self.assertTrue(any("MLH" in n for n in names))
        with mock.patch.object(comp, "devpost", return_value=iter(())), mock.patch.object(comp, "unstop", return_value=iter(())):
            found, errors = comp.discover()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(o.name for o in found), sorted(names))

    def test_unstop_has_a_limit_of_its_own(self) -> None:
        with mock.patch.object(comp, "devpost", return_value=iter(())) as devpost, \
                mock.patch.object(comp, "unstop", return_value=iter(())) as unstop:
            comp.discover(limit=20, include_manual=False)
            self.assertEqual(devpost.call_args.kwargs["limit"], 20)
            self.assertEqual(unstop.call_args.kwargs["limit"], comp.UNSTOP_LIMIT)
            comp.discover(limit=20, unstop_limit=50, online_only=True, include_manual=False)
            self.assertEqual(unstop.call_args.kwargs, {"limit": 50, "online_only": True})


if __name__ == "__main__":
    unittest.main()
