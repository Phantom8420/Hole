"""Finding competitions to enter, the same way `sourcing/` finds jobs to apply to.

The Competitions page was manual entry only: you saw a hackathon somewhere, you
typed it in. This module is the other half -- it goes and looks, so the row
appears before the deadline rather than after it.

Same rules as the job connectors next door: public endpoints only, no scraping a
site that forbids it, and a dead source is skipped rather than fatal. Unstop is
read from the public listing its own pages are built on: its robots.txt allows
/api/public/, it answers without a key or cookies, and its terms say nothing against
it. (This module used to say it blocked programs; it does not.) Notably absent:

- Devfolio   listings are client-rendered; the HTML a fetch returns is empty.
- LinkedIn   User Agreement bans automated access.

For those, `discover` records the platform as a bookmark row instead, so the
dashboard still tells you where to go look by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterator

from .base import SourceError, fetch_json, iso_date

DEVPOST_API = "https://devpost.com/api/hackathons"
UNSTOP_API = "https://unstop.com/api/public/opportunity/search-result"
UNSTOP_SITE = "https://unstop.com/"
UNSTOP_PAGE_SIZE = 18  # what its own pages ask for
UNSTOP_PAGES = 20  # hard stop per list
UNSTOP_LIMIT = 300  # some 270 hackathons are open at a time, then the competitions worth having
# Its competitions list is mostly college-fest events (dance, essays, olympiads) and quizzes.
# Case competitions and innovation challenges are the ones for a career; hackathons have a
# list of their own.
UNSTOP_COMPETITION_KINDS = {"case_competition", "innovation_challenge"}
# Prize currencies arrive as Font Awesome class names.
UNSTOP_CURRENCY = {
    "fa-rupee": "₹", "fa-inr": "₹", "fa-dollar": "$", "fa-usd": "$",
    "fa-euro": "€", "fa-eur": "€", "fa-gbp": "£",
}

# Devpost theme names -> the category the Competitions page groups by. Anything
# unmapped stays a hackathon, which is what Devpost is mostly for.
FINANCE_THEMES = {"fintech", "finance", "blockchain", "cryptocurrency"}
CASE_THEMES = {"business", "entrepreneurship", "social good"}


@dataclass
class Opportunity:
    """One competition worth considering. Mirrors the `competitions` table."""

    name: str
    category: str = "hackathon"
    description: str | None = None
    url: str | None = None
    apply_url: str | None = None
    deadline: str | None = None
    period: str | None = None
    team_size: str | None = None
    tracks: list[str] = field(default_factory=list)
    prize: str | None = None
    source: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "url": self.url,
            "apply_url": self.apply_url,
            "deadline": self.deadline,
            "period": self.period,
            "team_size": self.team_size,
            "tracks": ", ".join(self.tracks) or None,
            "discovery_source": self.source,
            "status": "discovered",
        }


def _strip_tags(value: str | None) -> str | None:
    """Devpost returns prize amounts wrapped in markup: "$<span ...>740,000</span>"."""
    if not value:
        return None
    return re.sub(r"<[^>]+>", "", value).strip() or None


def _category_for(themes: list[str]) -> str:
    lowered = {t.lower() for t in themes}
    if lowered & FINANCE_THEMES:
        return "finance_competition"
    if lowered & CASE_THEMES:
        return "case_competition"
    return "hackathon"


MONTH_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*", re.IGNORECASE
)


def _deadline_from(period: str | None) -> str | None:
    """Devpost gives a range; the deadline is its end.

    Two shapes, and the second is why this is not a one-liner:
        "Jul 31 - Oct 01, 2026"   both halves name a month
        "Aug 01 - 31, 2026"       the end half does not, and reading it alone
                                  would produce a monthless "31, 2026"
    """
    if not period:
        return None
    head, _, tail = period.rpartition("-")
    tail = tail.strip()
    if not tail:
        return period.strip() or None
    if not MONTH_RE.search(tail):
        month = MONTH_RE.search(head)
        if month:
            tail = f"{month.group(0)} {tail}"
    return tail or None


def devpost(*, limit: int = 100, online_only: bool = False) -> Iterator[Opportunity]:
    """Open hackathons on Devpost. Public JSON API, no key required."""
    seen = 0
    for page in range(1, 12):  # hard stop; the API pages 9 at a time
        if seen >= limit:
            return
        try:
            payload = fetch_json(DEVPOST_API, params={"status[]": "open", "page": page})
        except SourceError:
            raise
        entries = payload.get("hackathons") or []
        if not entries:
            return
        for entry in entries:
            if seen >= limit:
                return
            location = (entry.get("displayed_location") or {}).get("location") or ""
            if online_only and "online" not in location.lower():
                continue
            themes = [t.get("name", "") for t in (entry.get("themes") or []) if t.get("name")]
            period = entry.get("submission_period_dates")
            prize = _strip_tags(entry.get("prize_amount"))
            bits = [b for b in (location, f"prize {prize}" if prize else None) if b]
            yield Opportunity(
                name=entry.get("title") or "untitled",
                category=_category_for(themes),
                description=" -- ".join(bits) or None,
                url=entry.get("url"),
                apply_url=entry.get("start_a_submission_url") or entry.get("url"),
                deadline=_deadline_from(period),
                period=period,
                tracks=themes,
                prize=prize,
                source="devpost",
            )
            seen += 1


def _unstop_last_day(value: Any) -> str | None:
    """The last day of something. Unstop stamps an end at the midnight that starts a day
    (2026-10-09T00:00:00+05:30 is the end of the 8th) and its cards count down to that, so
    the date a list shows has to be the day before or it promises a day that is not there."""
    text = str(value or "")
    day = iso_date(text)
    if day and re.search(r"T00:00:\d\d", text):
        return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return day


def _unstop_eligible(row: dict[str, Any]) -> list[str]:
    return [
        f["name"] for f in row.get("filters") or []
        if f.get("type") == "eligible" and f.get("name")
    ]


def _unstop_open_to(names: list[str]) -> str | None:
    """Who may enter, the way its cards say it: "All" is everyone."""
    if not names:
        return None
    if "All" in names:
        return "everyone"
    shown = ", ".join(names[:4])
    return f"{shown} and {len(names) - 4} more" if len(names) > 4 else shown


def _unstop_where(row: dict[str, Any]) -> str:
    region = row.get("region")
    if region == "online":
        return "online"
    place = row.get("address_with_country_logo") or {}
    country = (place.get("country") or {}).get("name")
    town = ", ".join(p for p in (place.get("city"), place.get("state"), country) if p)
    town = town or place.get("address") or ""
    if region == "hybrid":
        return f"hybrid, {town}" if town else "hybrid"
    return town or "on site"


def _unstop_team(reg: dict[str, Any]) -> str | None:
    low, high = reg.get("min_team_size") or 1, reg.get("max_team_size")
    if not high:
        return None
    if high == 1:
        return "solo"
    return str(high) if low == high else f"{low}-{high}"


def _unstop_prize(row: dict[str, Any]) -> str | None:
    """The biggest cash prize, with its currency sign when it names one."""
    best: tuple[float, str] | None = None
    for prize in row.get("prizes") or []:
        try:
            cash = float(prize.get("cash") or 0)
        except (TypeError, ValueError):
            continue
        if cash > 0 and (best is None or cash > best[0]):
            best = (cash, UNSTOP_CURRENCY.get(prize.get("currency") or "", ""))
    return f"{best[1]}{best[0]:,.0f}" if best else None


def _unstop_fee(row: dict[str, Any]) -> str | None:
    amounts = []
    for service in row.get("payment_services") or []:
        try:
            amount = float(service.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            amounts.append(amount)
    return f"{min(amounts):,.0f}" if amounts else None


def _unstop_category(row: dict[str, Any]) -> str:
    text = f"{row.get('subtype') or ''} {row.get('title') or ''}".lower()
    if row.get("type") == "hackathons" or "hackathon" in text:
        return "hackathon"
    if re.search(r"financ|invest|trading|fintech", text):
        return "finance_competition"
    if "case" in text:
        return "case_competition"
    return "other"


def _unstop_opportunity(row: dict[str, Any]) -> Opportunity | None:
    title = (row.get("title") or "").strip()
    link = (row.get("public_url") or "").strip().lstrip("/")
    if not title or not link:
        return None
    reg = row.get("regnRequirements") or {}
    ends = _unstop_last_day(row.get("end_date"))
    closes = _unstop_last_day(reg.get("end_regn_dt")) or ends
    org = (row.get("organisation") or {}).get("name")
    open_to = _unstop_open_to(_unstop_eligible(row))
    fee, prize = _unstop_fee(row), _unstop_prize(row)
    bits = [
        f"by {org}" if org else None,
        _unstop_where(row),
        f"open to {open_to}" if open_to else None,
        f"fee {fee}" if fee else None,
        f"prize up to {prize}" if prize else None,
    ]
    tracks = [w["name"] for w in row.get("workfunction") or [] if w.get("name")]
    tracks += [s["skill_name"] for s in (row.get("required_skills") or [])[:3] if s.get("skill_name")]
    # Registration closes before a long competition ends; say when it does end.
    period = None
    if ends and closes and ends > closes:
        period = "until " + datetime.strptime(ends, "%Y-%m-%d").strftime("%b %d, %Y")
    return Opportunity(
        name=title,
        category=_unstop_category(row),
        description=" -- ".join(b for b in bits if b) or None,
        url=UNSTOP_SITE + link,
        apply_url=UNSTOP_SITE + link,
        deadline=closes,
        period=period,
        team_size=_unstop_team(reg),
        tracks=list(dict.fromkeys(tracks)),
        prize=prize,
        source="unstop",
    )


def unstop(*, limit: int = UNSTOP_LIMIT, online_only: bool = False) -> Iterator[Opportunity]:
    """Open hackathons, then case competitions and innovation challenges, on Unstop.

    Events for school students only are left out -- not for someone in college -- and
    each row carries what the page's cards show: when registration closes, team size,
    who may enter, the fee and the top prize.
    """
    seen: set[Any] = set()
    kept = 0
    for kind in ("hackathons", "competitions"):
        for page in range(1, UNSTOP_PAGES + 1):
            payload = fetch_json(
                UNSTOP_API,
                params={"opportunity": kind, "page": page, "per_page": UNSTOP_PAGE_SIZE,
                        "oppstatus": "open"},
            )
            listing = payload.get("data") if isinstance(payload, dict) else None
            listing = listing if isinstance(listing, dict) else {}
            rows = listing.get("data") or []
            for row in rows:
                if row.get("id") is not None:
                    if row["id"] in seen:  # hackathons are in the competitions list too
                        continue
                    seen.add(row["id"])
                if row.get("type") != "hackathons" and row.get("subtype") not in UNSTOP_COMPETITION_KINDS:
                    continue
                names = _unstop_eligible(row)
                if names and all("school" in n.lower() for n in names):
                    continue
                if online_only and row.get("region") != "online":
                    continue
                opportunity = _unstop_opportunity(row)
                if opportunity is None:
                    continue
                yield opportunity
                kept += 1
                if kept >= limit:
                    return
            if not rows or page >= (listing.get("last_page") or page):
                break


# Platforms that cannot be read programmatically. Recorded as bookmark rows so
# the dashboard still points at them rather than silently omitting them.
MANUAL_PLATFORMS = (
    Opportunity(
        name="Devfolio -- browse by hand",
        category="other",
        description="India's main Web3/blockchain hackathon platform. Listings are client-rendered, so they cannot be fetched.",
        url="https://devfolio.co/hackathons/upcoming",
        source="manual",
    ),
    Opportunity(
        name="MLH -- browse by hand",
        category="other",
        description="200+ student hackathons per season, mostly remote-eligible.",
        url="https://mlh.com/events",
        source="manual",
    ),
)


def discover(*, limit: int = 100, online_only: bool = False, include_manual: bool = True,
             unstop_limit: int = UNSTOP_LIMIT) -> tuple[list[Opportunity], list[str]]:
    """Every connector, failures collected rather than raised."""
    found: list[Opportunity] = []
    errors: list[str] = []
    try:
        found.extend(devpost(limit=limit, online_only=online_only))
    except SourceError as exc:
        errors.append(f"devpost: {exc}")
    try:
        found.extend(unstop(limit=unstop_limit, online_only=online_only))
    except SourceError as exc:  # what was read before it failed is kept
        errors.append(f"unstop: {exc}")
    if include_manual:
        found.extend(MANUAL_PLATFORMS)
    return found, errors


def save(conn: Any, opportunities: list[Opportunity]) -> tuple[int, int]:
    """Insert what is new, leave what is already there alone.

    Dedup is on `name` because that is what a person recognises, and because a
    row typed in by hand should not be duplicated by the scraper finding the
    same event later. A hand-entered row keeps its own text -- discovery never
    overwrites something a person wrote.
    """
    existing = {
        (r["name"] or "").strip().lower()
        for r in conn.execute("SELECT name FROM competitions")
    }
    added = skipped = 0
    for opp in opportunities:
        if opp.name.strip().lower() in existing:
            skipped += 1
            continue
        row = opp.to_row()
        conn.execute(
            """INSERT INTO competitions
               (name, category, description, url, apply_url, deadline, period,
                team_size, tracks, discovery_source, status, discovered_at)
               VALUES (:name, :category, :description, :url, :apply_url, :deadline,
                       :period, :team_size, :tracks, :discovery_source, :status,
                       datetime('now'))""",
            row,
        )
        existing.add(opp.name.strip().lower())
        added += 1
    conn.commit()
    return added, skipped
