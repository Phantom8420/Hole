"""Remote-job aggregator boards with documented public APIs.

These sit at a different layer from `ats_boards`. An ATS board is where an
application is *submitted*; these are where postings are *found*. A listing here
almost always links out to a company's Greenhouse, Lever, Ashby, or Workable
form -- so widening this file widens what gets seen, and widening `dispatch`
widens what can be applied to. They are not substitutes for each other.

Every endpoint below is a public API the site publishes for exactly this use.
Nothing here scrapes. Boards that forbid automated access (Wellfound, FlexJobs)
or that are freelance marketplaces requiring an authenticated account (Upwork,
Fiverr, Toptal, PeoplePerHour) are deliberately absent, for the same reason
LinkedIn and Indeed are: an account ban costs more than the listings are worth.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .base import (
    Posting,
    SourceError,
    SourceResult,
    employment_type,
    fetch_json,
    html_to_text,
    iso_date,
)

REMOTIVE_API = "https://remotive.com/api/remote-jobs"
REMOTEOK_API = "https://remoteok.com/api"
ARBEITNOW_API = "https://www.arbeitnow.com/api/job-board-api"
JOBICY_API = "https://jobicy.com/api/v2/remote-jobs"
HIMALAYAS_API = "https://himalayas.app/jobs/api"
WORKING_NOMADS_API = "https://www.workingnomads.com/api/exposed_jobs/"
SUPERTEAM_API = "https://earn.superteam.fun/api/listings/"
SUPERTEAM_DETAIL = "https://earn.superteam.fun/api/listings/details/{slug}"
SUPERTEAM_PAGE = "https://earn.superteam.fun/listing/{slug}"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _salary(low: Any, high: Any, currency: str = "USD", period: str = "") -> str | None:
    """A readable range, or nothing. Zeroes mean "not stated", not "unpaid"."""
    try:
        low_n = float(low or 0)
        high_n = float(high or 0)
    except (TypeError, ValueError):
        return None
    if low_n <= 0 and high_n <= 0:
        return None
    suffix = f" / {period}" if period else ""
    if low_n > 0 and high_n > 0:
        return f"{currency} {low_n:,.0f}-{high_n:,.0f}{suffix}"
    return f"{currency} {max(low_n, high_n):,.0f}{suffix}"


# --------------------------------------------------------------------------- connectors


def fetch_remotive(*, limit: int = 100, search: str = "") -> SourceResult:
    result = SourceResult(source="remotive")
    params: dict[str, Any] = {"limit": limit}
    if search:
        params["search"] = search
    try:
        payload = fetch_json(REMOTIVE_API, params=params)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    for row in payload.get("jobs", []) or []:
        result.postings.append(
            Posting(
                source="remotive",
                external_id=_text(row.get("id")),
                company=_text(row.get("company_name")),
                title=_text(row.get("title")),
                location=_text(row.get("candidate_required_location")),
                remote=True,  # the whole board is remote-only
                url=_text(row.get("url")),
                description=html_to_text(row.get("description")),
                compensation=_text(row.get("salary")) or None,
                posted_at=iso_date(row.get("publication_date")),
                employment_type=employment_type(row.get("job_type")),
            )
        )
    return result


def fetch_remoteok(*, limit: int = 100) -> SourceResult:
    """RemoteOK returns a legal/attribution notice as the first element."""
    result = SourceResult(source="remoteok")
    try:
        payload = fetch_json(REMOTEOK_API)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    rows = [r for r in (payload or []) if isinstance(r, dict) and r.get("id")]
    for row in rows[:limit]:
        result.postings.append(
            Posting(
                source="remoteok",
                external_id=_text(row.get("id")),
                company=_text(row.get("company")),
                title=_text(row.get("position")),
                location=_text(row.get("location")),
                remote=True,
                url=_text(row.get("url")) or _text(row.get("apply_url")),
                apply_url=_text(row.get("apply_url")),
                description=html_to_text(row.get("description")),
                compensation=_salary(row.get("salary_min"), row.get("salary_max")),
                posted_at=iso_date(row.get("date") or row.get("epoch")),
                employment_type=employment_type(row.get("tags")),
            )
        )
    return result


def fetch_arbeitnow(*, limit: int = 100, remote_only: bool = True) -> SourceResult:
    result = SourceResult(source="arbeitnow")
    try:
        payload = fetch_json(ARBEITNOW_API)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    # Scan the whole payload, not a slice of it. Arbeitnow is a general German
    # job board where only a small fraction is remote -- 7 of 175 in a sample --
    # so taking the first N rows and then filtering returns nothing at all.
    for row in payload.get("data") or []:
        is_remote = bool(row.get("remote"))
        if remote_only and not is_remote:
            continue
        result.postings.append(
            Posting(
                source="arbeitnow",
                external_id=_text(row.get("slug")),
                company=_text(row.get("company_name")),
                title=_text(row.get("title")),
                location=_text(row.get("location")),
                remote=is_remote,
                url=_text(row.get("url")),
                description=html_to_text(row.get("description")),
                posted_at=iso_date(row.get("created_at")),
                employment_type=employment_type(row.get("job_types")),
            )
        )
        if len(result.postings) >= limit:
            break
    return result


def fetch_jobicy(*, limit: int = 50, geo: str = "", industry: str = "") -> SourceResult:
    result = SourceResult(source="jobicy")
    params: dict[str, Any] = {"count": min(limit, 50)}
    if geo:
        params["geo"] = geo
    if industry:
        params["industry"] = industry
    try:
        payload = fetch_json(JOBICY_API, params=params)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    for row in payload.get("jobs", []) or []:
        result.postings.append(
            Posting(
                source="jobicy",
                external_id=_text(row.get("id")),
                company=_text(row.get("companyName")),
                title=_text(row.get("jobTitle")),
                location=_text(row.get("jobGeo")),
                remote=True,
                url=_text(row.get("url")),
                description=html_to_text(row.get("jobDescription") or row.get("jobExcerpt")),
                compensation=_salary(
                    row.get("annualSalaryMin"), row.get("annualSalaryMax"),
                    _text(row.get("salaryCurrency")) or "USD",
                ),
                posted_at=iso_date(row.get("pubDate")),
                employment_type=employment_type(row.get("jobType")),
            )
        )
    return result


def fetch_himalayas(*, limit: int = 50) -> SourceResult:
    result = SourceResult(source="himalayas")
    try:
        payload = fetch_json(HIMALAYAS_API, params={"limit": min(limit, 100)})
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    for row in payload.get("jobs", []) or []:
        locations = row.get("locationRestrictions") or []
        result.postings.append(
            Posting(
                source="himalayas",
                external_id=_text(row.get("guid") or row.get("applicationLink")),
                company=_text(row.get("companyName")),
                title=_text(row.get("title")),
                location=", ".join(str(x) for x in locations[:3]),
                remote=True,
                url=_text(row.get("applicationLink")),
                description=html_to_text(row.get("description") or row.get("excerpt")),
                compensation=_salary(
                    row.get("minSalary"), row.get("maxSalary"),
                    _text(row.get("currency")) or "USD",
                    _text(row.get("salaryPeriod")),
                ),
                posted_at=iso_date(row.get("pubDate")),
                employment_type=employment_type(row.get("employmentType")),
            )
        )
    return result


def fetch_working_nomads(*, limit: int = 100) -> SourceResult:
    result = SourceResult(source="workingnomads")
    try:
        payload = fetch_json(WORKING_NOMADS_API)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result

    for row in (payload or [])[:limit]:
        url = _text(row.get("url"))
        result.postings.append(
            Posting(
                source="workingnomads",
                external_id=_text(row.get("id")) or url,
                company=_text(row.get("company_name")),
                title=_text(row.get("title")),
                location=_text(row.get("location")),
                remote=True,
                url=url,
                description=html_to_text(row.get("description")),
                posted_at=iso_date(row.get("pub_date")),
            )
        )
    return result


def fetch_superteam(*, limit: int = 50) -> SourceResult:
    """Paid gigs and bounties on Superteam Earn, rewarded in USDC or USDG (a dollar).

    Open to anyone unless a listing names a region -- many belong to one country's
    chapter -- so those are left out. Hackathons are competitions and are found
    elsewhere. The list has no text, so each listing's page is read for it.
    """
    result = SourceResult(source="superteam")
    try:
        rows = fetch_json(SUPERTEAM_API)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result
    if not isinstance(rows, list):
        result.errors.append(f"{SUPERTEAM_API}: expected a list of listings")
        return result

    for row in rows[:limit]:
        slug = _text(row.get("slug"))
        if not slug or row.get("type") == "hackathon":
            continue
        try:
            detail = fetch_json(SUPERTEAM_DETAIL.format(slug=slug)) or {}
        except SourceError as exc:
            result.errors.append(str(exc))
            continue
        region = _text(detail.get("region"))
        if region and region.lower() not in {"global", "worldwide"}:
            continue

        token = _text(row.get("token")) or "USD"
        pay = None
        if row.get("compensationType") == "range":
            pay = _salary(row.get("minRewardAsk"), row.get("maxRewardAsk"), token)
        pay = pay or _salary(0, row.get("rewardAmount"), token)
        deadline = iso_date(row.get("deadline"))
        skills = "; ".join(
            f"{s.get('skills')}" + (f" ({', '.join(s.get('subskills') or [])})" if s.get("subskills") else "")
            for s in detail.get("skills") or [] if isinstance(s, dict) and s.get("skills")
        )
        text = "\n\n".join(part for part in (
            html_to_text(detail.get("description")),
            html_to_text(detail.get("requirements")),
            f"Skills: {skills}" if skills else "",
            f"Reward: {pay}. Deadline: {deadline}." if pay or deadline else "",
        ) if part)
        page = SUPERTEAM_PAGE.format(slug=slug)
        result.postings.append(
            Posting(
                source="superteam",
                external_id=_text(row.get("id")) or slug,
                company=_text((row.get("sponsor") or {}).get("name")),
                title=_text(row.get("title")),
                location="Remote (worldwide)",
                remote=True,
                url=page,
                description=text,
                compensation=pay,
                posted_at=iso_date(detail.get("publishedAt")),
                employment_type="freelance",
                deadline=deadline,
            )
        )
    result.complete = not result.errors
    return result


# Name -> connector, for config-driven runs. Each takes only keyword arguments
# and never raises: a board being down degrades the run, it does not end it.
REMOTE_BOARDS: dict[str, Callable[..., SourceResult]] = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "arbeitnow": fetch_arbeitnow,
    "jobicy": fetch_jobicy,
    "himalayas": fetch_himalayas,
    "workingnomads": fetch_working_nomads,
    "superteam": fetch_superteam,
}


def fetch_boards(names: list[str], *, limit: int = 100) -> list[SourceResult]:
    """Run several boards, keeping whatever succeeded."""
    results: list[SourceResult] = []
    for name in names:
        connector = REMOTE_BOARDS.get(name.strip().lower())
        if connector is None:
            unknown = SourceResult(source=name)
            unknown.errors.append(
                f"unknown board '{name}'. Known: {', '.join(sorted(REMOTE_BOARDS))}"
            )
            results.append(unknown)
            continue
        try:
            results.append(connector(limit=limit))
        except Exception as exc:  # a broken board never ends the run
            failed = SourceResult(source=name)
            failed.errors.append(f"{type(exc).__name__}: {exc}")
            results.append(failed)
    return results
