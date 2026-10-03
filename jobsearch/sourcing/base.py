"""Shared plumbing for job-board connectors.

Every connector returns `Posting` objects and nothing else, so the pipeline does
not care where a job came from. Connectors only ever read documented public
endpoints -- there is no scraping here, and adding a connector that scrapes a
site which forbids it is not a supported extension.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

import requests

USER_AGENT = "jobsearch-personal-tool/0.2 (+individual job seeker; contact via application)"
REQUEST_TIMEOUT = 20
POLITE_DELAY_SECONDS = 0.6

REMOTE_HINTS = ("remote", "anywhere", "distributed", "work from home", "wfh")

# Ends a description a connector wrote itself because the posting's own text was out
# of reach. The fit score measures a posting's words against a profile; a stub has
# none worth measuring, so such a posting is left unscored rather than scored 0.
STUB_MARKER = "(posting text not fetched)"


class SourceError(RuntimeError):
    """A connector could not fetch. Never fatal -- the run continues without it."""


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "head"}
    BREAK_AFTER = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BREAK_AFTER:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """Job descriptions arrive as HTML; the matcher wants readable text."""
    if not raw:
        return ""
    if "<" not in raw:
        return html.unescape(raw).strip()
    parser = _TextExtractor()
    try:
        parser.feed(html.unescape(raw))
        parser.close()
    except Exception:  # malformed markup should never kill a run
        return re.sub(r"<[^>]+>", " ", html.unescape(raw)).strip()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


# What a source's own word for a posting's kind maps to. Checked in this order, so
# "Intern, part time" is an internship.
_EMPLOYMENT_TYPES = (
    ("internship", ("intern", "student", "trainee", "apprentice")),
    ("freelance", ("freelance",)),
    ("contract", ("contract", "temporary")),
    ("part_time", ("part time", "part-time", "parttime")),
    ("full_time", ("full time", "full-time", "fulltime", "permanent")),
)


def employment_type(*raw: Any) -> str | None:
    """internship / freelance / contract / part_time / full_time, from whatever a source
    calls it ("Contractor", "full_time", ["Student", "Intern"]), or None."""
    words: list[str] = []
    for value in raw:
        if isinstance(value, (list, tuple, set)):
            words.extend(str(v) for v in value)
        elif value:
            words.append(str(value))
    text = " ".join(words).lower().replace("_", " ")
    for kind, needles in _EMPLOYMENT_TYPES:
        if any(needle in text for needle in needles):
            return kind
    return None


def _normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


@dataclass
class Posting:
    source: str
    external_id: str
    company: str
    title: str
    location: str = ""
    remote: bool = False
    url: str = ""
    apply_url: str = ""
    description: str = ""
    compensation: str | None = None
    posted_at: str | None = None
    # internship / full_time / part_time / contract / freelance, when the source says.
    employment_type: str | None = None
    # Last day to apply or enter (YYYY-MM-DD), for the gigs that have one.
    deadline: str | None = None

    def __post_init__(self) -> None:
        if not self.apply_url:
            self.apply_url = self.url
        if not self.remote:
            haystack = f"{self.location} {self.title}".lower()
            self.remote = any(hint in haystack for hint in REMOTE_HINTS)

    def fingerprint(self) -> str:
        key = "|".join(
            (_normalize(self.company), _normalize(self.title), _normalize(self.location))
        )
        return hashlib.sha1(key.encode("utf-8")).hexdigest()

    def to_row(self, discovered_at: str) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "remote": 1 if self.remote else 0,
            "url": self.url,
            "apply_url": self.apply_url,
            "description": self.description,
            "compensation": self.compensation,
            "posted_at": self.posted_at,
            "employment_type": self.employment_type,
            "deadline": self.deadline,
            "discovered_at": discovered_at,
            "fingerprint": self.fingerprint(),
            "status": "new",
        }

    @property
    def text(self) -> str:
        """Everything the matcher should read."""
        return f"{self.title}\n{self.company}\n{self.location}\n\n{self.description}"


@dataclass
class SourceResult:
    source: str
    postings: list[Posting] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # True when `postings` is everything the source has open, so a posting stored
    # earlier and missing now has closed. A board that shows only its latest N cannot
    # say that, and neither can a source that failed part of the way.
    complete: bool = False
    # Ids a source reports as closed, for the sources that can say so directly.
    closed_ids: list[str] = field(default_factory=list)


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _session


def fetch_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    timeout: int = REQUEST_TIMEOUT,
) -> Any:
    """One polite request. Raises SourceError on anything that is not usable JSON."""
    time.sleep(POLITE_DELAY_SECONDS)
    try:
        response = session().request(
            method, url, params=params, headers=headers, timeout=timeout
        )
    except requests.RequestException as exc:
        raise SourceError(f"{url}: {type(exc).__name__}: {exc}") from exc

    if response.status_code == 404:
        raise SourceError(f"{url}: 404 -- check the board token or company slug")
    if response.status_code == 429:
        raise SourceError(f"{url}: 429 rate limited -- back off and try the next run")
    if not response.ok:
        raise SourceError(f"{url}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"{url}: response was not JSON") from exc


def iso_date(value: Any) -> str | None:
    """Normalize the assorted date shapes these APIs return to YYYY-MM-DD."""
    if not value:
        return None
    text = str(value)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    if text.isdigit() and len(text) >= 10:  # epoch millis or seconds
        seconds = int(text[:10])
        return time.strftime("%Y-%m-%d", time.gmtime(seconds))
    return None


def dedupe(postings: Iterable[Posting]) -> list[Posting]:
    seen: set[str] = set()
    out: list[Posting] = []
    for posting in postings:
        key = posting.fingerprint()
        if key in seen:
            continue
        seen.add(key)
        out.append(posting)
    return out
