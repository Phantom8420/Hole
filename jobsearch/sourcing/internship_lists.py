"""Community-kept internship lists, read as the public data files they publish.

SimplifyJobs/Summer2027-Internships on GitHub keeps one JSON file of every
internship its maintainers have found (`.github/scripts/listings.json`): company,
title, locations, season, degree, whether the employer sponsors visas, and the
employer's own apply link. That makes it a directory rather than a board -- it
says where a posting is, not what it says -- so the text is fetched from the ATS
the link points at when that ATS publishes an open API (Greenhouse, Lever, Ashby),
which also confirms the posting is still open. Postings on Workday and the like
get a one-line description built from the listing's own fields and are leads to
open by hand.

Nothing here scrapes a site: it reads one public file per run.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .. import policy
from . import ats_boards
from .base import STUB_MARKER, Posting, SourceError, SourceResult, fetch_json, html_to_text, iso_date

SIMPLIFY_LISTINGS = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)

# The real file has about 17,000 listings; one far shorter was cut off or replaced.
MIN_LISTINGS_TO_TRUST = 1000

_GREENHOUSE_URL = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/(?P<org>[\w-]+)/jobs/(?P<id>\d+)", re.IGNORECASE
)
_LEVER_URL = re.compile(r"jobs\.lever\.co/(?P<org>[\w.-]+)/(?P<id>[0-9a-f-]{36})", re.IGNORECASE)
_ASHBY_URL = re.compile(
    r"jobs\.ashbyhq\.com/(?P<org>[^/?#]+)/(?P<id>[0-9a-f-]{36})", re.IGNORECASE
)


def _lower(values: Iterable[Any]) -> set[str]:
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _keep(
    row: dict[str, Any],
    *,
    terms: set[str],
    categories: set[str],
    degrees: set[str],
    banned_sponsorship: set[str],
) -> bool:
    """The listing's own fields: still open, a kind of role, a season, a degree level."""
    if not row.get("active") or row.get("is_visible") is False:
        return False
    if categories and str(row.get("category") or "").strip().lower() not in categories:
        return False
    if terms and not (_lower(row.get("terms") or []) & terms):
        return False
    listed = _lower(row.get("degrees") or [])
    # A listing that names no degree is open to any; one that names some has to
    # name one the candidate can say yes to.
    if degrees and listed and not (listed & degrees):
        return False
    return str(row.get("sponsorship") or "").strip().lower() not in banned_sponsorship


def _places(row: dict[str, Any], search: Any) -> list[str]:
    """The listing's locations that screen() would let stand."""
    places = [str(p).strip() for p in row.get("locations") or [] if str(p).strip()] or [""]
    return [p for p in places if not policy.location_skip(p, "remote" in p.lower(), search)]


def _summary(row: dict[str, Any]) -> str:
    bits = [f"{row.get('title')} at {row.get('company_name')}."]
    if row.get("category"):
        bits.append(f"Category: {row['category']}.")
    if row.get("terms"):
        bits.append("Term: " + ", ".join(str(t) for t in row["terms"]) + ".")
    if row.get("degrees"):
        bits.append("Degrees: " + ", ".join(str(d) for d in row["degrees"]) + ".")
    if row.get("sponsorship") and row["sponsorship"] != "Other":
        bits.append(f"Sponsorship: {row['sponsorship']}.")
    bits.append(f"From the Simplify internship list {STUB_MARKER}.")
    return " ".join(bits)


class _Closed(Exception):
    """The ATS no longer has this posting."""


def _detail(url: str, ashby: dict[str, dict[str, Any]]) -> tuple[str, str | None] | None:
    """Text and pay from the ATS's open API, None when the ATS has none to give.

    Raises _Closed when the ATS answers that the posting is gone, SourceError when
    it could not be asked.
    """
    match = _GREENHOUSE_URL.search(url)
    if match:
        try:
            job = fetch_json(
                f"{ats_boards.GREENHOUSE_JOBS.format(token=match['org'])}/{match['id']}"
            )
        except SourceError as exc:
            if "404" in str(exc):
                raise _Closed() from exc
            raise
        return html_to_text((job or {}).get("content")), None

    match = _LEVER_URL.search(url)
    if match:
        try:
            job = fetch_json(
                f"{ats_boards.LEVER_POSTINGS.format(company=match['org'])}/{match['id']}"
            )
        except SourceError as exc:
            if "404" in str(exc):
                raise _Closed() from exc
            raise
        return ats_boards._lever_description(job or {}), None

    match = _ASHBY_URL.search(url)
    if match:
        org = match["org"]
        if org not in ashby:
            try:
                payload = fetch_json(
                    ats_boards.ASHBY_BOARD.format(name=org),
                    params={"includeCompensation": "true"},
                )
            except SourceError as exc:
                if "404" not in str(exc):
                    raise
                payload = {}
            ashby[org] = {str(j.get("id")): j for j in (payload or {}).get("jobs") or []}
        job = ashby[org].get(match["id"].lower()) or ashby[org].get(match["id"])
        if job is None:
            raise _Closed()
        text = html_to_text(job.get("descriptionHtml")) or str(job.get("descriptionPlain") or "")
        return text, ats_boards._ashby_compensation(job)
    return None


def fetch_simplify(
    search: Any,
    *,
    terms: Iterable[str] = (),
    categories: Iterable[str] = (),
    degrees: Iterable[str] = (),
    exclude_sponsorship: Iterable[str] = (),
    limit: int = 300,
    known: Iterable[str] = (),
    url: str = SIMPLIFY_LISTINGS,
) -> SourceResult:
    """Internships from the list that screen() will not throw straight out.

    Cheap checks come first (the listing's fields, then place and title against the
    same config screen() reads), so only a few hundred postings cost an API call
    for their text. `known` is the set of listing ids already stored: they are not
    returned again, and the newest `limit` of the rest are taken, so a long list is
    worked through over several runs instead of all at once.
    """
    result = SourceResult(source="simplify")
    try:
        rows = fetch_json(url, timeout=90)
    except SourceError as exc:
        result.errors.append(str(exc))
        return result
    if not isinstance(rows, list):
        result.errors.append(f"{url}: expected a list of listings")
        return result

    wanted = dict(
        terms=_lower(terms),
        categories=_lower(categories),
        degrees=_lower(degrees),
        banned_sponsorship=_lower(exclude_sponsorship),
    )
    seen = {str(k) for k in known}
    if len(rows) >= MIN_LISTINGS_TO_TRUST:
        # The list says directly which listings have closed, so a posting stored on an
        # earlier run does not stay "to apply" after it is gone. (A cut-off file must
        # not close everything, hence the floor.)
        still_open = {
            str(r.get("id")) for r in rows
            if isinstance(r, dict) and r.get("active") and r.get("is_visible") is not False
        }
        result.closed_ids = sorted(seen - still_open)
    candidates: list[tuple[dict[str, Any], list[str]]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("id")) in seen or not _keep(row, **wanted):
            continue
        title = str(row.get("title") or "").strip()
        if not title or policy.title_word(title, search.exclude_title_keywords):
            continue
        if not policy.title_matches(title, search.titles):
            continue
        places = _places(row, search)
        if places:
            candidates.append((row, places))
    candidates.sort(key=lambda pair: pair[0].get("date_posted") or 0, reverse=True)

    ashby: dict[str, dict[str, Any]] = {}
    failures = 0
    for row, places in candidates:
        if len(result.postings) >= limit:
            break
        link = str(row.get("url") or "").strip()
        description, compensation = _summary(row), None
        try:
            detail = _detail(link, ashby)
        except _Closed:
            continue
        except SourceError:
            failures += 1  # not asked, not answered: try again next run
            continue
        if detail and detail[0]:
            description, compensation = detail

        result.postings.append(
            Posting(
                source="simplify",
                external_id=str(row.get("id")),
                company=str(row.get("company_name") or "").strip(),
                title=str(row.get("title") or "").strip(),
                location="; ".join(places),
                url=link,
                apply_url=link,
                description=description,
                compensation=compensation,
                posted_at=iso_date(row.get("date_posted")),
                employment_type="internship",
            )
        )
    if failures:
        result.errors.append(f"simplify: {failures} posting(s) could not be checked and were left for the next run")
    return result
