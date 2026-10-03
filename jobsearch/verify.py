"""Post-generation grounding checks.

The system prompt tells the model not to invent facts. This checks whether it
listened. Findings are warnings, not hard failures -- a human still reads the
draft -- but an unsourced number is the single most common way a tailored resume
turns into a lie, so it gets flagged loudly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

NUMBER_RE = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|[kKmMbB]\b)?)")
ORG_SUFFIX_RE = re.compile(
    r"\b([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3}\s+"
    r"(?:Inc\.?|LLC|Ltd\.?|Corp\.?|Corporation|GmbH|PLC|Technologies|Labs|Systems))\b"
)
PLACEHOLDER_RE = re.compile(r"\[(?:your |the |insert |company |hiring )[^\]\n]{0,40}\]|\bTODO\b|\bXX+\b", re.IGNORECASE)

# Numbers that are structural rather than factual claims.
IGNORED_NUMBERS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "0"}


@dataclass
class Finding:
    kind: str
    value: str
    context: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.value}  --  {self.context}"


def _normalize_number(token: str) -> str:
    return token.replace(",", "").replace("$", "").replace(" ", "").rstrip(".").lower()


def _facts_text(profile: dict[str, str], achievements: Sequence[dict[str, Any]]) -> str:
    return json.dumps({"profile": profile, "achievements": list(achievements)}, ensure_ascii=False)


def _line_context(text: str, index: int, width: int = 70) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    line = text[start:end].strip()
    return line[:width] + ("..." if len(line) > width else "")


def _number_tokens(text: str) -> set[str]:
    """The set of distinct numbers that appear in `text`, each normalized the
    same way a generated token is. Membership against this set is exact -- a
    plain `"50" in facts_string` substring test matched '50' inside '1500', a
    phone number, or an id, and let fabricated metrics through.
    """
    out: set[str] = set()
    for match in NUMBER_RE.finditer(text):
        norm = _normalize_number(match.group(1).strip())
        if not norm:
            continue
        out.add(norm)
        out.add(norm.rstrip("%").rstrip("kmb").replace("percent", ""))
    out.discard("")
    return out


def check_numbers(
    generated: str,
    profile: dict[str, str],
    achievements: Sequence[dict[str, Any]],
) -> list[Finding]:
    """Every metric in the draft should appear somewhere in the source facts."""
    fact_numbers = _number_tokens(_facts_text(profile, achievements))
    findings: list[Finding] = []
    seen: set[str] = set()

    for match in NUMBER_RE.finditer(generated):
        token = match.group(1).strip()
        norm = _normalize_number(token)
        bare = norm.rstrip("%").rstrip("kmb").replace("percent", "")
        if not bare or bare in IGNORED_NUMBERS or norm in seen:
            continue
        line = _line_context(generated, match.start())
        if line.lstrip().startswith(("#", "-", "*")) and line.lstrip()[:4].strip(" -*#").startswith(token):
            continue  # list marker, not a claim
        seen.add(norm)
        if norm in fact_numbers or bare in fact_numbers:
            continue
        findings.append(Finding("unsourced-number", token, line))
    return findings


def check_employers(
    generated: str,
    achievements: Sequence[dict[str, Any]],
    *,
    target_company: str | None = None,
) -> list[Finding]:
    """Company-shaped names that are neither a known employer nor the target."""
    known = {str(a.get("employer") or "").lower() for a in achievements}
    known.discard("")
    if target_company:
        known.add(target_company.lower())

    findings: list[Finding] = []
    seen: set[str] = set()
    for match in ORG_SUFFIX_RE.finditer(generated):
        name = match.group(1).strip()
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        if any(low in k or k in low for k in known):
            continue
        findings.append(Finding("unknown-organization", name, _line_context(generated, match.start())))
    return findings


def check_placeholders(generated: str) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for match in PLACEHOLDER_RE.finditer(generated):
        value = match.group(0)
        if value.lower() in seen:
            continue
        seen.add(value.lower())
        findings.append(Finding("placeholder", value, _line_context(generated, match.start())))
    return findings


def check_forbidden_skills(generated: str, forbidden: Sequence[str]) -> list[Finding]:
    """Skills retrieval explicitly refused to claim, appearing in the draft anyway.

    This is the highest-signal check in the file. The planner already worked out
    exactly which things the posting wants that no record supports, so any of
    them turning up in the resume is an unambiguous overclaim -- no heuristics,
    no guessing.
    """
    findings: list[Finding] = []
    for term in forbidden:
        pattern = re.compile(
            r"(?<!\w)" + r"\s+".join(re.escape(part) for part in term.split()) + r"(?!\w)",
            re.IGNORECASE,
        )
        match = pattern.search(generated)
        if match:
            findings.append(
                Finding("unevidenced-claim", term, _line_context(generated, match.start()))
            )
    return findings


def _organizations_from_facts(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Shape the plan payload into what the employer check expects."""
    rows: list[dict[str, Any]] = []
    for entry in facts.get("experience", []) or []:
        rows.append({"employer": entry.get("organization")})
    for entry in facts.get("education", []) or []:
        rows.append({"employer": entry.get("school")})
    for entry in facts.get("certifications", []) or []:
        rows.append({"employer": entry.get("issuer")})
    for entry in facts.get("projects", []) or []:
        rows.append({"employer": entry.get("organization")})
    return rows


def verify_plan(
    documents: dict[str, str],
    facts: dict[str, Any],
    *,
    target_company: str | None = None,
) -> list[Finding]:
    """Check outbound documents against the closed fact set they were built from.

    Fit notes are excluded by the caller -- they are internal, and they are
    *supposed* to name the gaps.
    """
    profile = facts.get("candidate_profile") or {}
    sources = [facts]  # the whole payload is the ground truth for numbers
    organizations = _organizations_from_facts(facts)
    forbidden = list(facts.get("skills_the_posting_wants_that_you_cannot_evidence") or [])

    findings: list[Finding] = []
    for name, text in documents.items():
        if not text:
            continue
        for finding in (
            check_numbers(text, profile, sources)
            + check_employers(text, organizations, target_company=target_company)
            + check_placeholders(text)
            + check_forbidden_skills(text, forbidden)
        ):
            findings.append(Finding(finding.kind, finding.value, f"{name}: {finding.context}"))
    return findings


def verify(
    documents: dict[str, str],
    profile: dict[str, str],
    achievements: Sequence[dict[str, Any]],
    *,
    target_company: str | None = None,
) -> list[Finding]:
    """Run every check over each outbound document. Fit notes are internal, so skipped."""
    findings: list[Finding] = []
    for name, text in documents.items():
        if not text:
            continue
        for finding in (
            check_numbers(text, profile, achievements)
            + check_employers(text, achievements, target_company=target_company)
            + check_placeholders(text)
        ):
            findings.append(Finding(finding.kind, finding.value, f"{name}: {finding.context}"))
    return findings
