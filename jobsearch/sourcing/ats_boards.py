"""Greenhouse, Lever, and Ashby public job-board endpoints.

All three publish a documented, unauthenticated read API intended for exactly
this: one request per company board, full posting text included. No key, no
scraping, no terms problem.
"""

from __future__ import annotations

from typing import Any

from .base import Posting, SourceError, SourceResult, employment_type, fetch_json, html_to_text, iso_date

GREENHOUSE_BOARD = "https://boards-api.greenhouse.io/v1/boards/{token}"
GREENHOUSE_JOBS = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
LEVER_POSTINGS = "https://api.lever.co/v0/postings/{company}"
ASHBY_BOARD = "https://api.ashbyhq.com/posting-api/job-board/{name}"


# --------------------------------------------------------------------------- greenhouse


def _greenhouse_company(token: str) -> str:
    try:
        board = fetch_json(GREENHOUSE_BOARD.format(token=token))
        name = (board or {}).get("name")
        if name:
            return str(name)
    except SourceError:
        pass
    return token.replace("-", " ").title()


def fetch_greenhouse(tokens: list[str]) -> SourceResult:
    result = SourceResult(source="greenhouse")
    for token in tokens:
        try:
            company = _greenhouse_company(token)
            payload = fetch_json(GREENHOUSE_JOBS.format(token=token), params={"content": "true"})
        except SourceError as exc:
            result.errors.append(str(exc))
            continue

        for job in (payload or {}).get("jobs", []) or []:
            location = ((job.get("location") or {}).get("name")) or ""
            job_id = str(job.get("id", ""))
            # `absolute_url` usually points at the company's own careers page,
            # which is a marketing wrapper, not the form. The canonical hosted
            # application lives on boards.greenhouse.io and is what the ATS
            # submitter knows how to fill.
            apply_url = (
                f"https://boards.greenhouse.io/{token}/jobs/{job_id}" if job_id else ""
            )
            result.postings.append(
                Posting(
                    source="greenhouse",
                    external_id=job_id,
                    company=company,
                    title=str(job.get("title") or "").strip(),
                    location=str(location).strip(),
                    url=str(job.get("absolute_url") or apply_url),
                    apply_url=apply_url,
                    description=html_to_text(job.get("content")),
                    posted_at=iso_date(job.get("updated_at") or job.get("first_published")),
                )
            )
    result.complete = not result.errors
    return result


# --------------------------------------------------------------------------- lever


def _lever_description(job: dict[str, Any]) -> str:
    parts = [html_to_text(job.get("descriptionPlain") or job.get("description"))]
    for block in job.get("lists") or []:
        heading = str(block.get("text") or "").strip()
        body = html_to_text(block.get("content"))
        if heading or body:
            parts.append(f"\n{heading}\n{body}".rstrip())
    closing = html_to_text(job.get("additionalPlain") or job.get("additional"))
    if closing:
        parts.append(closing)
    return "\n".join(p for p in parts if p).strip()


def fetch_lever(companies: list[str]) -> SourceResult:
    result = SourceResult(source="lever")
    for company in companies:
        try:
            payload = fetch_json(LEVER_POSTINGS.format(company=company), params={"mode": "json"})
        except SourceError as exc:
            result.errors.append(str(exc))
            continue

        for job in payload or []:
            categories = job.get("categories") or {}
            result.postings.append(
                Posting(
                    source="lever",
                    external_id=str(job.get("id", "")),
                    company=company.replace("-", " ").title(),
                    title=str(job.get("text") or "").strip(),
                    location=str(categories.get("location") or "").strip(),
                    url=str(job.get("hostedUrl") or ""),
                    apply_url=str(job.get("applyUrl") or job.get("hostedUrl") or ""),
                    description=_lever_description(job),
                    compensation=(job.get("salaryRange") or {}).get("currency")
                    and str(job.get("salaryRange")),
                    posted_at=iso_date(job.get("createdAt")),
                    employment_type=employment_type(categories.get("commitment")),
                )
            )
    result.complete = not result.errors
    return result


# --------------------------------------------------------------------------- ashby


def _ashby_compensation(job: dict[str, Any]) -> str | None:
    compensation = job.get("compensation") or {}
    summary = compensation.get("compensationTierSummary") or compensation.get("summary")
    return str(summary) if summary else None


def fetch_ashby(boards: list[str]) -> SourceResult:
    result = SourceResult(source="ashby")
    for name in boards:
        try:
            payload = fetch_json(
                ASHBY_BOARD.format(name=name), params={"includeCompensation": "true"}
            )
        except SourceError as exc:
            result.errors.append(str(exc))
            continue

        for job in (payload or {}).get("jobs", []) or []:
            result.postings.append(
                Posting(
                    source="ashby",
                    external_id=str(job.get("id", "")),
                    company=str((payload or {}).get("name") or name).replace("-", " ").title(),
                    title=str(job.get("title") or "").strip(),
                    location=str(job.get("location") or "").strip(),
                    remote=bool(job.get("isRemote")),
                    url=str(job.get("jobUrl") or ""),
                    apply_url=str(job.get("applyUrl") or job.get("jobUrl") or ""),
                    description=html_to_text(job.get("descriptionHtml"))
                    or str(job.get("descriptionPlain") or ""),
                    compensation=_ashby_compensation(job),
                    posted_at=iso_date(job.get("publishedAt")),
                    employment_type=employment_type(job.get("employmentType")),
                )
            )
    result.complete = not result.errors
    return result
