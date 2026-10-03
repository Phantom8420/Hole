"""The autonomy decision.

Two gates, deliberately separated.

`screen` runs before tailoring and answers "is this worth an API call". It is
cheap and only looks at the posting.

`decide_dispatch` runs after tailoring, verification, and grounding checks, and
answers "does this go out without a human". It is the only place that can return
SEND, and it says no unless every check passes.

Every decision carries its reasons. An unattended pipeline that cannot explain
itself afterwards is not something you should let email employers on your behalf.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from . import db, matching
from .config import Config
from .dispatch import ats_form

SEND = "send"
QUEUE = "queue"
SKIP = "skip"

# Postings that are a piece of work to take on, not a job to apply for.
FREELANCE_TYPES = ("freelance", "contract")

# Placeholders boards use when they have not filled the location in.
UNKNOWN_LOCATIONS = {
    "n/a", "n a", "na", "none", "tbd", "tba", "unknown", "various",
    "multiple", "multiple locations", "-", "--",
}

# Job boards give a city/state, not a country -- "San Francisco, CA" never
# contains the literal substring "united states", so a plain substring check
# against a configured "United States" rejects the large majority of real US
# postings. Recognized as a whole word/token so "CA" doesn't also match
# "Casablanca" or a stray "us" doesn't match "campus".
US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
}
US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}


# Countries whose name shares a token with a US state ("Georgia") or that are
# otherwise mistaken for the US. Named outright, they win over a coincidental
# state-name match -- "London, United Kingdom" must not read as US just because
# a later clause finds a state word. ("georgia" is left out on purpose: vetoing
# it would also drop Atlanta, GA, a far more common posting than Tbilisi.)
_NON_US_COUNTRY_MARKERS = (
    "united kingdom", "canada", "australia", "india", "germany", "france",
    "ireland", "netherlands", "singapore", "new zealand", "mexico", "brazil",
    "spain", "poland", "portugal",
)
_NON_US_COUNTRY_TOKENS = {"uk", "england", "scotland", "wales"}

# Cities that boards write on their own ("NYC", "Hybrid - San Francisco") with no
# state or country beside them. Only the unambiguous ones: no "Cambridge" or
# "Birmingham", which are as much English towns.
US_CITIES = (
    "new york", "nyc", "brooklyn", "manhattan", "san francisco", "south san francisco", "sf",
    "los angeles", "san diego", "san jose", "palo alto", "menlo park", "mountain view",
    "sunnyvale", "santa clara", "cupertino", "redwood city", "san mateo", "irvine",
    "seattle", "bellevue", "redmond", "austin", "boston", "chicago", "atlanta", "denver",
    "boulder", "washington dc", "pittsburgh", "philadelphia", "dallas", "houston", "miami",
    "raleigh", "minneapolis", "salt lake city",
)


def _is_us_location(location_norm: str) -> bool:
    # "US-WA-Bellevue", "DE-Berlin", "IN-Pune": a leading country code. Read as a
    # state it made Germany "Delaware" and India "Indiana".
    code = re.match(r"([a-z]{2})-", location_norm)
    if code:
        return code.group(1) == "us"
    tokens = [t for t in re.split(r"[,\s/|()-]+", location_norm) if t]
    if "united states" in location_norm or "usa" in tokens or "us" in tokens:
        return True
    # A non-US country named outright wins over a coincidental state-name match.
    if any(m in location_norm for m in _NON_US_COUNTRY_MARKERS) or (
        set(tokens) & _NON_US_COUNTRY_TOKENS
    ):
        return False
    if any(_mentions(location_norm, city) for city in US_CITIES):
        return True
    if any(t in US_STATE_NAMES for t in (" ".join(tokens[i : i + 2]) for i in range(len(tokens)))):
        return True
    # A 2-letter state code only counts next to a state-shaped rest of the
    # string ("san francisco, ca" or "us-ca-menlo park") -- alone it is too
    # likely to be a language code, an initialism, or noise ("N/A" already
    # short-circuits earlier, but this guards the rest).
    return any(t in US_STATE_ABBR for t in tokens) and len(tokens) > 1


@dataclass
class Decision:
    action: str
    reasons: list[str] = field(default_factory=list)

    @property
    def sends(self) -> bool:
        return self.action == SEND

    def __str__(self) -> str:
        return f"{self.action}: " + "; ".join(self.reasons)


@dataclass
class PolicyContext:
    """Counters the caps are measured against. Loaded once per run."""

    sent_today: int = 0
    sent_this_run: int = 0
    tailored_this_run: int = 0
    per_company_week: dict[str, int] = field(default_factory=dict)
    applied_fingerprints: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "PolicyContext":
        today = date.today().isoformat()
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()

        sent_today = int(
            conn.execute(
                "SELECT COUNT(*) FROM applications WHERE sent_date = ?", (today,)
            ).fetchone()[0]
        )
        per_company: dict[str, int] = {}
        for row in conn.execute(
            "SELECT company, COUNT(*) AS n FROM applications "
            "WHERE sent_date >= ? GROUP BY company",
            (week_ago,),
        ):
            if row["company"]:
                per_company[_norm(row["company"])] = int(row["n"])

        # A draft counts: the same role listed twice (a company's board and an internship
        # list) must not be tailored, and then sent, a second time.
        applied: set[str] = set()
        for row in conn.execute(
            "SELECT company, role FROM applications "
            "WHERE status IN ('drafted','approved','sent','responded')"
        ):
            applied.add(_pair(row["company"], row["role"]))
        return cls(
            sent_today=sent_today,
            per_company_week=per_company,
            applied_fingerprints=applied,
        )

    def record_application(self, company: str | None, role: str | None) -> None:
        """Note that this run now has an application for the role."""
        self.applied_fingerprints.add(_pair(company, role))

    def record_sent(self, company: str | None) -> None:
        self.sent_today += 1
        self.sent_this_run += 1
        if company:
            key = _norm(company)
            self.per_company_week[key] = self.per_company_week.get(key, 0) + 1


def _norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _pair(company: str | None, role: str | None) -> str:
    return f"{_norm(company)}|{_norm(role)}"


# What a remote posting says when it names no place at all.
_PLACE_WORDS = re.compile(
    r"\b(remote(ly)?|anywhere|worldwide|global(ly)?|distributed|wfh|work from home|home based|"
    r"hybrid|flexible|in|or|any|of|the|from|with|and)\b"
)


def _mentions(location_norm: str, place: str) -> bool:
    """`place` as a whole word or phrase: "india" is not in "indiana"."""
    wanted = _norm(place)
    return bool(wanted) and re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", location_norm) is not None


def location_skip(location: str | None, remote: bool, search: Any) -> str | None:
    """Why a posting's place rules it out, or None when it stands.

    One answer for screen() and for the connectors that would rather not fetch or
    store what screen() is going to drop. `search` is a SearchConfig.
    """
    norm = _norm(location)
    banned = next((p for p in search.exclude_locations if _mentions(norm, p)), None)
    if banned:
        return f"location '{location}' is on the excluded list ({banned})"
    # Boards commonly emit "N/A" or leave it blank. Unknown is not the same as
    # wrong -- let the fit score decide those rather than dropping them.
    if not search.locations or not norm or norm in UNKNOWN_LOCATIONS:
        return None

    def wanted(configured: str) -> bool:
        if _mentions(norm, configured):
            return True
        return _norm(configured) == "united states" and _is_us_location(norm)

    if remote:
        if not search.locations_apply_to_remote:
            return None
        # "Remote" and "Worldwide" name no place, so they stand; "Remote in Canada"
        # has to be open to somewhere on the list. The list's own "Remote" entry is
        # not a place, or every remote posting would pass on it.
        if not _PLACE_WORDS.sub(" ", norm).strip(" -,/()|"):
            return None
        places = [p for p in search.locations if _norm(p) not in {"remote", "anywhere", "worldwide"}]
        if places and not any(wanted(place) for place in places):
            return f"remote, but only from '{location}', outside the configured list"
        return None

    if not any(wanted(place) for place in search.locations):
        return f"location '{location}' is outside the configured list"
    return None


# --------------------------------------------------------------------------- screening


def title_matches(title: str, wanted: Sequence[str]) -> bool:
    """Loose title match. No configured titles means everything passes.

    Guards against the obvious failure of following a company's board and then
    applying to its warehouse openings.
    """
    if not wanted:
        return True
    posting_tokens = set(matching.content_tokens(title))
    if not posting_tokens:
        return False
    for candidate in wanted:
        if _norm(candidate) in _norm(title):
            return True
        tokens = set(matching.content_tokens(candidate))
        if not tokens:
            continue
        overlap = len(tokens & posting_tokens) / len(tokens)
        if overlap >= 0.6:
            return True
    return False


def title_word(title: str, words: Sequence[str]) -> str | None:
    """The first of `words` that is in the title as a whole word, so "lead" does
    not hit "Leadership" nor "vp" "VPN"."""
    lowered = title.lower()
    for word in words:
        if word and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
            return word
    return None


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_NUMBER = r"\d{1,2}|" + "|".join(_NUMBER_WORDS)
_YEARS = re.compile(
    rf"(?<![\w.$])({_NUMBER})(?:\s*\(\d{{1,2}}\))?"  # "five (5) years"
    rf"(\s*\+|\s+plus|\s+or\s+more|\s*(?:-|–|—|to)\s*(?:{_NUMBER}))?"  # 3+, 3-5, 3 to 5
    r"\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
# In front of the number these make it something other than a requirement.
_NOT_A_REQUIREMENT = re.compile(
    r"(less than|fewer than|up to|under|within|past|last|next|per|every|each|"
    r"in the|for the|over the|after|before)\s*$",
    re.IGNORECASE,
)
_AT_LEAST = re.compile(r"(at least|minimum( of)?|min\.?)\s*$", re.IGNORECASE)
# "2 years of university" is schooling, which an intern is in the middle of.
_SCHOOLING = re.compile(
    r"\s*(of\s+)?(university|college|higher education|education|school|study|studies|"
    r"schooling|coursework|undergraduate|postgraduate|degree|academic)",
    re.IGNORECASE,
)


def years_of_experience(text: str) -> int | None:
    """Fewest years of experience the posting asks for, or None if it names none.

    The smallest figure, not the largest: a posting that asks for "3+ years" of one
    thing and mentions "10 years" of the team's history is a 3-year posting, and a
    stray big number should not sink it. A figure only counts when it reads as a
    requirement -- "3+ years", "at least 3 years", or "experience" in the same
    sentence -- so "founded 10 years ago" and "the last 5 years" are left alone.
    """
    found: list[int] = []
    for match in _YEARS.finditer(text or ""):
        before = text[max(0, match.start() - 24) : match.start()]
        if _NOT_A_REQUIREMENT.search(before) or _SCHOOLING.match(text[match.end() : match.end() + 30]):
            continue
        after = re.split(r"[.\n]", text[match.end() : match.end() + 70])[0]
        asked = (
            match.group(2) is not None
            or _AT_LEAST.search(before)
            or re.search(r"\bexperience\b", after, re.IGNORECASE)
            or re.search(r"\bexperience\b\W*$", before, re.IGNORECASE)
        )
        if asked:
            low = match.group(1).lower()
            found.append(_NUMBER_WORDS.get(low) or int(low))
    return min(found) if found else None


def _too_old(posted_at: str | None, max_age_days: int) -> bool:
    if not posted_at or max_age_days <= 0:
        return False
    try:
        posted = datetime.strptime(posted_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - posted).days > max_age_days


def screen(job: dict[str, Any], config: Config, context: PolicyContext) -> Decision:
    """Cheap pre-tailoring filter. Only SKIP or QUEUE (meaning 'proceed')."""
    search = config.search
    company = job.get("company") or ""
    title = job.get("title") or ""
    text = f"{title}\n{job.get('description') or ''}".lower()

    if _norm(company) in {_norm(c) for c in search.exclude_companies}:
        return Decision(SKIP, [f"{company} is on the exclude list"])

    hit = next((k for k in search.exclude_keywords if k and k in text), None)
    if hit:
        return Decision(SKIP, [f"posting mentions excluded keyword '{hit}'"])

    word = title_word(title, search.exclude_title_keywords)
    if word:
        return Decision(SKIP, [f"title has the excluded word '{word}'"])

    # A gig is judged on what it asks and where, not on the level and role names meant
    # for jobs: the posting for a Python script is not titled "Software Engineer Intern".
    freelance = search.include_freelance and job.get("employment_type") in FREELANCE_TYPES

    if (
        search.require_title_keywords
        and not freelance
        and job.get("employment_type") != "internship"  # a source that says so is as good as the title
        and not title_word(title, search.require_title_keywords)
    ):
        return Decision(SKIP, [f"title '{title}' names none of the levels you asked for"])

    if not freelance and not title_matches(title, search.titles):
        return Decision(SKIP, [f"title '{title}' does not match any configured title"])

    if search.max_experience_years is not None:
        years = years_of_experience(job.get("description") or "")
        if years is not None and years > search.max_experience_years:
            return Decision(
                SKIP,
                [f"asks for {years}+ years of experience, over max_experience_years "
                 f"({search.max_experience_years})"],
            )

    if search.remote_only and not job.get("remote"):
        return Decision(SKIP, ["remote_only is set and this posting is not remote"])

    place = location_skip(job.get("location"), bool(job.get("remote")), search)
    if place:
        return Decision(SKIP, [place])

    if _too_old(job.get("posted_at"), search.max_age_days):
        return Decision(SKIP, [f"posted {job.get('posted_at')}, older than {search.max_age_days} days"])

    if job.get("deadline") and str(job["deadline"])[:10] < date.today().isoformat():
        return Decision(SKIP, [f"the deadline, {str(job['deadline'])[:10]}, has passed"])

    if _pair(company, title) in context.applied_fingerprints:
        return Decision(SKIP, ["already have an application for this role"])

    fit = job.get("fit_score")
    if fit is not None and fit < search.min_fit:
        return Decision(SKIP, [f"fit {fit:.0f} below min_fit {search.min_fit:.0f}"])

    # The per-run cap on tailoring is not checked here. A posting that passes is
    # wanted whether or not this run has room for it, and run() leaves it scored for
    # the next one. Skipping it here took it off the list for good.
    reasons = [f"fit {fit:.0f}" if fit is not None else "not scored yet"]
    return Decision(QUEUE, reasons)


# --------------------------------------------------------------------------- dispatch


def available_channel(job: dict[str, Any], config: Config) -> str | None:
    """First configured channel that can actually handle this posting."""
    for channel in config.dispatch.channel_order:
        if channel == "ats_form":
            # Having an apply_url is not enough -- it has to be a form this tool
            # can actually drive. Aggregator boards (Remotive, RemoteOK,
            # Himalayas) point their apply link at their own listing page, so
            # choosing ats_form for those means launching a browser to discover
            # the host is unrecognized, once per posting. Check first.
            if (
                config.dispatch.ats.enabled
                and job.get("apply_url")
                and ats_form.detect_ats(str(job["apply_url"])) != "unknown"
            ):
                return "ats_form"
        elif channel == "email":
            if config.dispatch.email.enabled:
                return "email"
    return None


def decide_dispatch(
    job: dict[str, Any],
    config: Config,
    context: PolicyContext,
    *,
    grounding_findings: Sequence[Any] = (),
    missing_profile_fields: Sequence[str] = (),
    used_unverified: bool = False,
) -> Decision:
    """The only function that can authorize sending. Defaults to holding back."""
    limits = config.limits
    reasons: list[str] = []

    if not config.autonomous:
        return Decision(QUEUE, ["autonomous is off -- prepared but not sent"])

    if grounding_findings and config.dispatch.require_clean_grounding:
        kinds = sorted({getattr(f, "kind", "finding") for f in grounding_findings})
        return Decision(QUEUE, [f"grounding check flagged {len(grounding_findings)} item(s): {', '.join(kinds)}"])

    if missing_profile_fields:
        return Decision(QUEUE, [f"profile is missing {', '.join(missing_profile_fields)}"])

    if used_unverified and config.dispatch.require_verified_records:
        return Decision(QUEUE, ["draft used profile rows you have not confirmed"])

    if context.sent_this_run >= limits.max_applications_per_run:
        return Decision(QUEUE, [f"hit max_applications_per_run ({limits.max_applications_per_run})"])

    if context.sent_today >= limits.max_applications_per_day:
        return Decision(QUEUE, [f"hit max_applications_per_day ({limits.max_applications_per_day})"])

    company_key = _norm(job.get("company"))
    if context.per_company_week.get(company_key, 0) >= limits.max_per_company_per_week:
        return Decision(
            QUEUE,
            [f"already sent {limits.max_per_company_per_week} to {job.get('company')} this week"],
        )

    channel = available_channel(job, config)
    if not channel:
        return Decision(QUEUE, ["no dispatch channel is configured for this posting"])

    fit = job.get("fit_score")
    reasons.append(f"fit {fit:.0f} clears {config.search.min_fit:.0f}" if fit is not None else "scored")
    reasons.append("grounding clean")
    reasons.append(f"channel {channel}")
    return Decision(SEND, reasons)
