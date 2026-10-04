"""Command line interface.

    python -m jobsearch --help

Output is ASCII-only so it renders correctly in a default Windows console codepage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from . import (
    answers,
    db,
    generate,
    graph,
    linking,
    llm,
    matching,
    pipeline,
    policy,
    render,
    retrieval,
    schedule as scheduling,
    schema,
    sourcing,
    verify,
)
from .config import CONFIG_NAME, EXAMPLE_NAME, Config, ConfigError
from .dispatch import email_gmail, find_apply_email
from .dispatch import linkedin as linkedin_draft
from .ingest import documents as ingest_documents
from .ingest import github as ingest_github
from .ingest import linkedin as ingest_linkedin

BAR_WIDTH = 24
RULE = "=" * 78

LISTABLE = {
    "experiences": ("experiences", "title"),
    "achievements": ("achievements", "id"),
    "projects": ("projects", "name"),
    "education": ("education", "id"),
    "skills": ("skills", "name"),
    "certifications": ("certifications", "name"),
    "awards": ("awards", "name"),
    "publications": ("publications", "title"),
    "languages": ("languages", "name"),
    "volunteering": ("volunteering", "id"),
    "recommendations": ("recommendations", "id"),
    "organizations": ("organizations", "name"),
}


# --------------------------------------------------------------------------- helpers


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _read_job_description(args: argparse.Namespace) -> str:
    if getattr(args, "job_text", None):
        return args.job_text
    if getattr(args, "job_file", None):
        path = Path(args.job_file).expanduser()
        if not path.is_file():
            raise SystemExit(f"Job description file not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace")
    if sys.stdin.isatty():
        eof = "Ctrl+Z then Enter" if sys.platform == "win32" else "Ctrl+D"
        _err(f"Paste the job description, then press {eof}:")
    text = sys.stdin.read()
    if not text.strip():
        raise SystemExit("No job description provided. Use --job-file, --job-text, or pipe it in.")
    return text


def _bar(percent: float) -> str:
    filled = int(round(BAR_WIDTH * max(0.0, min(percent, 100.0)) / 100.0))
    return "#" * filled + "." * (BAR_WIDTH - filled)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        _err("Refusing to proceed without confirmation (not a terminal). Pass --yes.")
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _wrap(text: str, indent: str = "      ", width: int = 92, hanging: int = 0) -> list[str]:
    return textwrap.wrap(
        str(text),
        width=width,
        initial_indent=indent,
        subsequent_indent=indent + " " * hanging,
    )


def _report_counts(created: dict[str, int]) -> None:
    if not created:
        _out("  nothing created")
        return
    width = max(len(k) for k in created)
    for key in sorted(created):
        _out(f"  {key.ljust(width)}  {created[key]}")


# --------------------------------------------------------------------------- profile view


def cmd_init(args: argparse.Namespace) -> int:
    turso_url = None if args.db else os.environ.get("TURSO_DATABASE_URL")
    path = db.resolve_db_path(args.db)
    existed = path.exists()
    with db.session(args.db) as conn:
        counts = db.profile_counts(conn)
    where = turso_url or path
    _out(f"{'Verified' if (existed or turso_url) else 'Created'} database at {where}")
    _out(f"  schema v{schema.SCHEMA_VERSION}")
    _report_counts({k: v for k, v in counts.items() if v})
    if not counts.get("experiences"):
        _out("")
        _out("Empty profile. Fill it from what you already have:")
        _out("  python -m jobsearch import linkedin <path-to-LinkedIn-export.zip>")
        _out("  python -m jobsearch import document <path-to-resume.pdf>")
        _out("  python -m jobsearch import inbox inbox/")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        action = args.profile_action
        if action == "set":
            key = db.set_profile_field(conn, args.key, args.value)
            _out(f"profile.{key} = {args.value.strip()}")
            return 0
        if action == "delete":
            if db.delete_profile_field(conn, args.key):
                _out(f"Cleared profile field '{args.key}'.")
                return 0
            _err(f"No profile field '{args.key}'.")
            return 1

        profile_graph = graph.ProfileGraph.load(conn)
        if args.json:
            _out(json.dumps(profile_graph.profile, indent=2, ensure_ascii=False))
            return 0
        _render_profile(profile_graph, full=args.full)
        return 0


def _render_profile(g: graph.ProfileGraph, *, full: bool = False) -> None:
    profile = g.profile
    name = profile.get("full_name") or "(no name set)"
    _out(RULE)
    _out(name)
    if profile.get("headline"):
        _out(profile["headline"])
    contact = " | ".join(
        str(profile[k])
        for k in ("email", "phone", "location", "website", "github", "linkedin_url")
        if profile.get(k)
    )
    if contact:
        _out(contact)
    _out(RULE)

    if profile.get("summary"):
        _out("")
        for line in _wrap(profile["summary"], indent=""):
            _out(line)

    if g.experiences:
        _out("")
        _out(f"EXPERIENCE ({len(g.experiences)})")
        for node in g.experiences:
            flag = "" if node.row.get("verified") else "  [unverified]"
            _out(f"  [{node.id}] {node.title}")
            _out(f"      {node.organization or '(no organization)'}   {node.dates}{flag}")
            bullets = node.achievements if full else node.achievements[:3]
            for bullet in bullets:
                for line in _wrap(f"- {bullet.row.get('description') or bullet.title}", hanging=2):
                    _out(line)
                if bullet.row.get("quantified_impact"):
                    for line in _wrap(f"  impact: {bullet.row['quantified_impact']}", indent="        "):
                        _out(line)
            hidden = len(node.achievements) - len(bullets)
            if hidden > 0:
                _out(f"      ... {hidden} more accomplishment(s), --full to see them")
            if node.skills:
                _out(f"      skills: {', '.join(node.skills)}")

    if g.projects:
        _out("")
        _out(f"PROJECTS ({len(g.projects)})")
        for node in g.projects:
            _out(f"  [{node.id}] {node.name}   {node.dates}")
            if node.row.get("description"):
                for line in _wrap(node.row["description"]):
                    _out(line)
            if node.skills:
                _out(f"      skills: {', '.join(node.skills)}")

    if g.education:
        _out("")
        _out(f"EDUCATION ({len(g.education)})")
        for node in g.education:
            _out(f"  [{node.id}] {node.label}   {node.dates}")

    if g.certifications:
        _out("")
        _out(f"CERTIFICATIONS ({len(g.certifications)})")
        for node in g.certifications:
            issuer = node.row.get("issuer")
            _out(f"  [{node.id}] {node.label}" + (f" - {issuer}" if issuer else ""))

    if g.skills:
        evidenced = g.evidenced_skills
        _out("")
        _out(f"SKILLS ({len(evidenced)} evidenced of {len(g.skills)})")
        shown = sorted(evidenced, key=lambda s: (-s.get("evidence_count", 0), s["name"]))
        for skill in shown if full else shown[:20]:
            _out(f"  {skill['name']}  ({skill['evidence_count']} evidence)")
        if not full and len(shown) > 20:
            _out(f"  ... {len(shown) - 20} more, --full to see them")
        naked = [s for s in g.skills if not s.get("evidence_count")]
        if naked:
            _out("")
            _out(f"  {len(naked)} skill(s) with no supporting record -- these never reach a resume:")
            for line in _wrap(", ".join(s["name"] for s in naked), indent="    "):
                _out(line)
            _out("    Fix with: python -m jobsearch link    (or attach one by hand:")
            _out("    python -m jobsearch skill evidence <name> experience <id>)")

    counts = g.counts()
    _out("")
    _out(
        f"{counts['experiences']} positions, {counts['accomplishments']} accomplishments, "
        f"{counts['projects']} projects, {counts['evidenced_skills']} usable skills"
    )


# --------------------------------------------------------------------------- ingestion


def cmd_import(args: argparse.Namespace) -> int:
    if args.import_action == "linkedin":
        return _import_linkedin(args)
    if args.import_action == "document":
        return _import_document(args)
    if args.import_action == "inbox":
        return _import_inbox(args)
    if args.import_action == "github":
        return _import_github(args)
    _err("Choose: import linkedin | import document | import inbox | import github")
    return 1


def _import_linkedin(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        try:
            report = ingest_linkedin.import_archive(conn, args.path)
        except FileNotFoundError as exc:
            _err(str(exc))
            return 1
    _out(f"Imported LinkedIn export (source {report.source_id})")
    _report_counts(report.created)
    if report.files_seen:
        _out("")
        _out(f"  files read: {', '.join(report.files_seen[:12])}")
        if len(report.files_seen) > 12:
            _out(f"  ... and {len(report.files_seen) - 12} more")
    for note in report.skipped:
        _out("")
        _out(f"NOTE: {note}")
    if report.created.get("skills"):
        _out("")
        _out("LinkedIn does not record where a skill was used, so those skills arrived with")
        _out("no supporting record and cannot reach a resume yet. Link them:")
        _out("  python -m jobsearch link")
    return 0


def _import_github(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        try:
            report = ingest_github.import_profile(
                conn, args.username, include_forks=args.include_forks
            )
        except ValueError as exc:
            _err(str(exc))
            return 1
    _out(f"Imported {report.username}'s public repos (source {report.source_id})")
    _report_counts(report.created)
    if report.skipped_forks:
        _out("")
        _out(f"  skipped {len(report.skipped_forks)} fork(s): {', '.join(report.skipped_forks[:8])}")
        if len(report.skipped_forks) > 8:
            _out(f"  ... and {len(report.skipped_forks) - 8} more")
        _out("  (a fork proves you ran it, not that you built it -- use --include-forks to import anyway)")
    return 0


def _import_document(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        try:
            report = ingest_documents.import_document(
                conn,
                args.path,
                hint=args.hint,
                model=args.model,
                verified=1 if args.trust else 0,
            )
        except (FileNotFoundError, ValueError) as exc:
            _err(str(exc))
            return 1
        except generate.GenerationError as exc:
            _err(str(exc))
            return 1
    _out(f"Extracted from {Path(report.path).name} (source {report.source_id})")
    _report_counts(report.created)
    if report.notes:
        _out("")
        _out("Model notes on what did not fit the schema:")
        for line in _wrap(report.notes, indent="  "):
            _out(line)
    for warning in report.warnings:
        _out(f"WARNING: {warning}")
    if not args.trust and report.total():
        _out("")
        _out("These rows are unverified -- a model read them out of a file. Check them:")
        _out("  python -m jobsearch review")
    return 0


def _import_inbox(args: argparse.Namespace) -> int:
    folder = Path(args.path).expanduser()
    if not folder.is_dir():
        _err(f"Not a directory: {folder}")
        return 1

    files = sorted(
        p
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in ingest_documents.SUPPORTED_SUFFIXES
    )
    archives = sorted(p for p in folder.rglob("*.zip"))
    if not files and not archives:
        _err(f"Nothing importable in {folder}.")
        _err(f"Supported: {', '.join(sorted(ingest_documents.SUPPORTED_SUFFIXES))}, plus .zip exports")
        return 1

    totals: dict[str, int] = {}
    failures: list[str] = []
    with db.session(args.db) as conn:
        for archive in archives:
            try:
                report = ingest_linkedin.import_archive(conn, archive)
                _out(f"[linkedin] {archive.name}: {report.total()} row(s)")
                for key, value in report.created.items():
                    totals[key] = totals.get(key, 0) + value
            except Exception as exc:  # keep going through the rest of the folder
                failures.append(f"{archive.name}: {type(exc).__name__}: {exc}")
        for path in files:
            try:
                report = ingest_documents.import_document(
                    conn, path, model=args.model, verified=1 if args.trust else 0
                )
                _out(f"[document] {path.name}: {report.total()} row(s)")
                for key, value in report.created.items():
                    totals[key] = totals.get(key, 0) + value
            except Exception as exc:
                failures.append(f"{path.name}: {type(exc).__name__}: {exc}")

    _out("")
    _out("Totals:")
    _report_counts(totals)
    if failures:
        _out("")
        _out(f"{len(failures)} file(s) failed:")
        for failure in failures:
            _out(f"  {failure}")
    _out("")
    _out("Next: python -m jobsearch link    then    python -m jobsearch review")
    return 0 if not failures else 1


def cmd_link(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        created = linking.autolink_skills(conn, commit=False)
        naked = db.unevidenced_skills(conn)
    if created:
        _out(f"Linked {len(created)} skill(s) to records that name them:")
        for link in created[:40]:
            _out(f"  {link}")
        if len(created) > 40:
            _out(f"  ... and {len(created) - 40} more")
    else:
        _out("No new links found.")
    if naked:
        _out("")
        _out(f"{len(naked)} skill(s) still have no supporting record and will never")
        _out("appear on a resume:")
        for line in _wrap(", ".join(s["name"] for s in naked), indent="  "):
            _out(line)
        _out("")
        _out("Either attach one by hand:")
        _out("  python -m jobsearch skill evidence \"Kubernetes\" experience 3")
        _out("or add the accomplishment that proves it.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.review_action == "confirm":
            if args.table not in schema.ENTITY_TABLES:
                _err(f"Table must be one of: {', '.join(schema.ENTITY_TABLES)}")
                return 1
            if args.id is None:
                cur = conn.execute(f"UPDATE {args.table} SET verified = 1 WHERE verified = 0")
                _out(f"Confirmed {cur.rowcount} row(s) in {args.table}.")
            else:
                if not db.update_row(conn, args.table, args.id, {"verified": 1}):
                    _err(f"No {args.table} row {args.id}.")
                    return 1
                _out(f"Confirmed {args.table} {args.id}.")
            return 0

        total = 0
        for table in schema.ENTITY_TABLES:
            rows = db.rows_to_dicts(
                conn.execute(f"SELECT * FROM {table} WHERE verified = 0 ORDER BY id")
            )
            if not rows:
                continue
            total += len(rows)
            _out(f"=== {table} ({len(rows)} unverified) ===")
            for row in rows:
                label = (
                    row.get("title")
                    or row.get("name")
                    or row.get("degree")
                    or row.get("author_name")
                    or f"row {row['id']}"
                )
                _out(f"  [{row['id']}] {label}")
                detail = row.get("description") or row.get("quantified_impact")
                if detail:
                    for line in _wrap(str(detail), indent="        "):
                        _out(line)
            _out("")

    if not total:
        _out("Nothing awaiting review -- every row in the profile is confirmed.")
        return 0
    _out(f"{total} row(s) extracted by a model and not yet confirmed by you.")
    _out("Confirm a table:  python -m jobsearch review confirm experiences")
    _out("Confirm one row:  python -m jobsearch review confirm experiences 4")
    _out("Fix a wrong one:  python -m jobsearch rm experiences 4")
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.sources_action == "undo":
            source = db.get_row(conn, "sources", args.id)
            if not source:
                _err(f"No source {args.id}.")
                return 1
            summary = db.source_summary(conn, args.id)
            _out(f"Source {args.id}: {source['kind']} - {source.get('label') or source.get('location')}")
            _report_counts(summary)
            if not _confirm("Delete every row this import created?", args.yes):
                _out("Cancelled.")
                return 1
            removed = db.delete_source_rows(conn, args.id)
            _out("Removed:")
            _report_counts(removed)
            return 0

        sources = db.list_sources(conn)
        if not sources:
            _out("No imports recorded.")
            return 0
        for source in sources:
            summary = db.source_summary(conn, int(source["id"]))
            rows = ", ".join(f"{v} {k}" for k, v in summary.items()) or "no rows"
            _out(f"[{source['id']}] {source['kind']}  {source['imported_at']}")
            _out(f"      {source.get('label') or source.get('location') or ''}")
            _out(f"      {rows}")
    return 0


# --------------------------------------------------------------------------- manual editing


def cmd_add(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        kind = args.entity
        if kind == "experience":
            experience_id = db.insert_row(
                conn,
                "experiences",
                {
                    "organization_id": db.upsert_organization(conn, args.org, kind="company"),
                    "title": args.title,
                    "employment_type": args.type,
                    "location": args.location,
                    "start_date": args.start,
                    "end_date": args.end,
                    "is_current": 0 if args.end else 1,
                    "description": args.description,
                    "field": args.field,
                    "verified": 1,
                },
            )
            db.link_skills_to(conn, _split_list(args.skills), "experience", experience_id, verified=1)
            _out(f"Added experience {experience_id}: {args.title}" + (f" @ {args.org}" if args.org else ""))
            return 0

        if kind == "achievement":
            parents = [
                ("experience_id", args.experience),
                ("project_id", args.project),
                ("education_id", args.education),
            ]
            chosen = [(column, value) for column, value in parents if value]
            if len(chosen) != 1:
                _err("An accomplishment needs exactly one parent: --experience, --project, or --education.")
                return 1
            column, parent_id = chosen[0]
            table = {"experience_id": "experiences", "project_id": "projects", "education_id": "education"}[column]
            if not db.get_row(conn, table, parent_id):
                _err(f"No {table} row {parent_id}.")
                return 1
            achievement_id = db.insert_row(
                conn,
                "achievements",
                {
                    column: parent_id,
                    "title": args.title,
                    "description": args.description,
                    "quantified_impact": args.impact,
                    "verified": 1,
                },
            )
            db.link_skills_to(conn, _split_list(args.skills), "achievement", achievement_id, verified=1)
            _out(f"Added accomplishment {achievement_id} under {table} {parent_id}.")
            return 0

        if kind == "project":
            project_id = db.insert_row(
                conn,
                "projects",
                {
                    "name": args.title,
                    "description": args.description,
                    "role": args.role,
                    "url": args.url,
                    "start_date": args.start,
                    "end_date": args.end,
                    "field": args.field,
                    "verified": 1,
                },
            )
            db.link_skills_to(conn, _split_list(args.skills), "project", project_id, verified=1)
            _out(f"Added project {project_id}: {args.title}")
            return 0

        if kind == "education":
            education_id = db.insert_row(
                conn,
                "education",
                {
                    "organization_id": db.upsert_organization(conn, args.org, kind="school"),
                    "degree": args.title,
                    "field_of_study": args.field,
                    "start_date": args.start,
                    "end_date": args.end,
                    "description": args.description,
                    "verified": 1,
                },
            )
            _out(f"Added education {education_id}: {args.title}")
            return 0

        if kind == "certification":
            certification_id = db.insert_row(
                conn,
                "certifications",
                {
                    "name": args.title,
                    "issuer": args.org,
                    "issue_date": args.start,
                    "expiry_date": args.end,
                    "url": args.url,
                    "verified": 1,
                },
            )
            _out(f"Added certification {certification_id}: {args.title}")
            return 0
    return 1


def cmd_skill(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.skill_action == "evidence":
            row = conn.execute(
                "SELECT id, name FROM skills WHERE normalized_name = ?",
                (db.normalize_skill(args.name),),
            ).fetchone()
            if not row:
                _err(f"No skill named '{args.name}'. Add it with: add-skill, or import it first.")
                return 1
            if args.target_type not in db.EVIDENCE_TARGETS:
                _err(f"Target type must be one of: {', '.join(db.EVIDENCE_TARGETS)}")
                return 1
            table = {
                "experience": "experiences",
                "project": "projects",
                "achievement": "achievements",
                "education": "education",
                "certification": "certifications",
            }[args.target_type]
            if not db.get_row(conn, table, args.target_id):
                _err(f"No {table} row {args.target_id}.")
                return 1
            db.add_skill_evidence(conn, int(row["id"]), args.target_type, args.target_id, note="manual")
            _out(f"'{row['name']}' is now evidenced by {args.target_type} {args.target_id}.")
            return 0

        if args.skill_action == "add":
            skill_id = db.upsert_skill(
                conn, args.name, category=args.category, proficiency=args.proficiency, verified=1
            )
            _out(f"Skill {skill_id}: {args.name}")
            if not args.evidence:
                _out("No evidence attached yet, so it cannot appear on a resume.")
            else:
                target_type, _, target_id = args.evidence.partition(":")
                if target_type in db.EVIDENCE_TARGETS and target_id.isdigit():
                    db.add_skill_evidence(conn, int(skill_id), target_type, int(target_id), note="manual")
                    _out(f"Evidenced by {target_type} {target_id}.")
                else:
                    _err("--evidence must look like experience:3")
                    return 1
            return 0
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    if args.table not in LISTABLE:
        _err(f"Choose one of: {', '.join(sorted(LISTABLE))}")
        return 1
    table, order = LISTABLE[args.table]
    with db.session(args.db) as conn:
        if table == "experiences":
            rows = db.list_experiences(conn)
        elif table == "projects":
            rows = db.list_projects(conn)
        elif table == "education":
            rows = db.list_education(conn)
        elif table == "skills":
            rows = db.list_skills(conn)
        else:
            rows = db.list_table(conn, table, order)
    if args.json:
        _out(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        _out(f"No {args.table}.")
        return 0
    for row in rows:
        label = row.get("title") or row.get("name") or row.get("degree") or row.get("author_name") or ""
        extra = row.get("organization") or row.get("issuer") or row.get("publisher") or ""
        suffix = f"  ({row['evidence_count']} evidence)" if "evidence_count" in row else ""
        _out(f"  [{row['id']}] {label}" + (f" - {extra}" if extra else "") + suffix)
    _out("")
    _out(f"{len(rows)} row(s).")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    if args.table not in LISTABLE:
        _err(f"Choose one of: {', '.join(sorted(LISTABLE))}")
        return 1
    table, _ = LISTABLE[args.table]
    with db.session(args.db) as conn:
        row = db.get_row(conn, table, args.id)
        if not row:
            _err(f"No {args.table} row {args.id}.")
            return 1
        payload: dict[str, Any] = dict(row)
        if table in ("experiences", "projects", "education"):
            column = {"experiences": "experience_id", "projects": "project_id", "education": "education_id"}[table]
            payload["accomplishments"] = db.list_achievements(conn, **{column: args.id})
    _out(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    if args.table not in LISTABLE:
        _err(f"Choose one of: {', '.join(sorted(LISTABLE))}")
        return 1
    table, _ = LISTABLE[args.table]
    with db.session(args.db) as conn:
        row = db.get_row(conn, table, args.id)
        if not row:
            _err(f"No {args.table} row {args.id}.")
            return 1
        _out(json.dumps(row, indent=2, ensure_ascii=False))
        if table in ("experiences", "projects", "education"):
            column = {"experiences": "experience_id", "projects": "project_id", "education": "education_id"}[table]
            children = db.list_achievements(conn, **{column: args.id})
            if children:
                _out(f"This will also delete {len(children)} accomplishment(s).")
        if not _confirm(f"Delete {args.table} {args.id}?", args.yes):
            _out("Cancelled.")
            return 1
        db.delete_row(conn, table, args.id)
    _out(f"Deleted {args.table} {args.id}.")
    return 0


# --------------------------------------------------------------------------- matching


def _print_plan(plan: retrieval.ResumePlan, *, verbose: bool = True) -> None:
    _out(f"Fit score: {plan.fit:.1f} / 100   {_bar(plan.fit)}")
    _out("")
    if plan.experiences:
        _out("Positions selected, with the accomplishments chosen for this posting:")
        for planned in plan.experiences:
            tag = "  (kept for timeline continuity)" if planned.kept_for_continuity else ""
            _out(f"  [{planned.node.id}] {planned.node.label}   rel {planned.relative:.0f}%{tag}")
            for bullet in planned.bullets:
                text = bullet.row.get("description") or bullet.title
                for line in _wrap(f"- {text}", indent="        ", width=88, hanging=2):
                    _out(line)
            if verbose and planned.matched_terms:
                _out(f"        matched: {', '.join(planned.matched_terms[:8])}")
    else:
        _out("No positions cleared the relevance threshold.")

    if plan.projects:
        _out("")
        _out("Projects:")
        for project in plan.projects:
            _out(f"  [{project.node.id}] {project.node.name}   rel {project.relative:.0f}%")

    _out("")
    _out(f"Skills it may claim ({len(plan.skills)}, all evidenced and relevant):")
    for line in _wrap(", ".join(plan.skills) or "(none matched)", indent="  "):
        _out(line)

    if plan.unevidenced_requests:
        _out("")
        _out("Asked for by the posting, NOT evidenced anywhere in your profile:")
        for line in _wrap(", ".join(plan.unevidenced_requests), indent="  "):
            _out(line)
        _out("  These will not be claimed. They go in the fit notes instead.")

    if plan.gaps:
        _out("")
        _out("Uncovered posting requirements:")
        for line in _wrap(", ".join(plan.gaps), indent="  "):
            _out(line)

    if plan.missing_profile_fields:
        _out("")
        _out(f"WARNING: profile is missing {', '.join(plan.missing_profile_fields)} --")
        _out("         the resume header will be incomplete.")
        _out('         python -m jobsearch profile set full_name "Your Name"')


def _load_plan(args: argparse.Namespace, job_description: str) -> tuple[graph.ProfileGraph, retrieval.ResumePlan] | None:
    with db.session(args.db) as conn:
        g = graph.ProfileGraph.load(conn)
    if g.is_empty():
        _err("The profile graph is empty. Import your history first:")
        _err("  python -m jobsearch import linkedin <export.zip>")
        _err("  python -m jobsearch import document <resume.pdf>")
        return None
    plan = retrieval.build_plan(
        g,
        job_description,
        company=args.company,
        role=args.role,
        max_experiences=args.max_experiences,
        max_bullets=args.max_bullets,
        min_relative=args.min_score,
        verified_only=args.verified_only,
    )
    return g, plan


def cmd_match(args: argparse.Namespace) -> int:
    job_description = _read_job_description(args)
    loaded = _load_plan(args, job_description)
    if loaded is None:
        return 1
    _, plan = loaded
    _print_plan(plan)
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    job_description = _read_job_description(args)
    loaded = _load_plan(args, job_description)
    if loaded is None:
        return 1
    _, plan = loaded

    _print_plan(plan, verbose=False)
    _out("")

    if plan.is_empty():
        _err("Nothing in your profile matched this posting.")
        _err(f"Lower --min-score (currently {args.min_score}) if you disagree.")
        return 1

    if args.dry_run:
        _out(RULE)
        _out("DRY RUN -- no API call. Exact payload that would be sent:")
        _out(RULE)
        _out("")
        _out("--- system prompt ---")
        _out(generate.SYSTEM_PROMPT)
        _out("--- user message ---")
        _out(generate.build_user_message(job_description, plan.to_facts()))
        return 0

    # `--model` is normally unset, meaning "whatever the configured provider
    # defaults to". Printing the raw argument in that case says "Calling None".
    model_name = args.model or llm.default_model()
    _out(f"Calling {model_name} with {len(plan.experiences)} position(s), {plan.bullet_count()} bullet(s)...")
    try:
        result = generate.generate(
            job_description, plan, model=args.model, max_tokens=args.max_tokens
        )
    except generate.GenerationError as exc:
        _err("")
        _err(str(exc))
        return 1

    today = date.today().isoformat()
    slug = f"{today}_{generate.slugify(args.company, 'company')}_{generate.slugify(args.role, 'role')}"
    out_dir = Path(args.out).expanduser() if args.out else db.PROJECT_ROOT / "output" / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for name, content in (
        ("resume.md", result.resume),
        ("cover_letter.md", result.cover_letter),
        ("fit_notes.md", result.fit_notes),
        ("job_description.txt", job_description),
        ("raw_response.md", result.raw),
    ):
        if content and content.strip():
            path = out_dir / name
            path.write_text(content.strip() + "\n", encoding="utf-8")
            written.append(path)

    facts = plan.to_facts()
    (out_dir / "sources.json").write_text(
        json.dumps(
            {
                "generated": today,
                "model": result.model,
                "usage": result.usage,
                "company": args.company,
                "role": args.role,
                "job_url": args.job_url,
                "fit_score": plan.fit,
                "experiences_used": [
                    {"id": p.node.id, "label": p.node.label, "relative": p.relative,
                     "bullet_ids": [b.id for b in p.bullets]}
                    for p in plan.experiences
                ],
                "skills_claimed": plan.skills,
                "uncovered_requirements": plan.gaps,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    findings = verify.verify_plan(
        {"resume": result.resume, "cover_letter": result.cover_letter},
        facts,
        target_company=args.company,
    )

    _out("")
    _out(RULE)
    _out(f"Wrote {len(written)} document(s) to {out_dir}")
    _out(RULE)
    if result.usage.get("stop_reason") == "max_tokens":
        _out("WARNING: response hit max_tokens and may be truncated. Retry with --max-tokens.")
    if not result.is_complete():
        _out("WARNING: the model did not return both documents; check raw_response.md.")

    _out("")
    if findings:
        _out(f"GROUNDING CHECK: {len(findings)} thing(s) to verify before sending:")
        for finding in findings:
            _out(f"  {finding}")
    else:
        _out("GROUNDING CHECK: clean -- every metric, employer, and skill traces to a stored record.")

    if result.fit_notes:
        _out("")
        _out("--- fit notes (internal, do not send) ---")
        _out(result.fit_notes)

    application_id = None
    if not args.no_record:
        with db.session(args.db) as conn:
            application_id = db.insert_application(
                conn,
                {
                    "company": args.company,
                    "role": args.role,
                    "source": args.source,
                    "job_url": args.job_url,
                    "resume_version": slug,
                    "status": "drafted",
                    "fit_score": plan.fit,
                    "grounding_status": "flagged" if findings else "clean",
                },
            )
    if application_id:
        _out("")
        _out(f"Logged application {application_id} (drafted, fit {plan.fit:.0f}, "
             f"grounding {'flagged' if findings else 'clean'}).")
        _out(f"  python -m jobsearch apps status {application_id} approved")

    if args.print_docs:
        for name, content in (("RESUME", result.resume), ("COVER LETTER", result.cover_letter)):
            if content:
                _out("")
                _out(RULE)
                _out(name)
                _out(RULE)
                _out(content)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        _err(f"File not found: {path}")
        return 1
    html_path = render.write_html(path, args.title)
    _out(f"Wrote {html_path}")
    if args.pdf:
        pdf_path, message = render.write_pdf(html_path)
        _out(f"Wrote {pdf_path} ({message})" if pdf_path else f"No PDF: {message}")
    else:
        _out("Open it in a browser and print to PDF, or re-run with --pdf.")
    return 0


def cmd_apps(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.apps_action == "show":
            row = db.get_application(conn, args.id)
            if not row:
                _err(f"No application with id {args.id}.")
                return 1
            _out(json.dumps(row, indent=2, ensure_ascii=False))
            if row.get("resume_version"):
                folder = db.PROJECT_ROOT / "output" / row["resume_version"]
                _out("")
                _out(f"Documents: {folder}{'' if folder.is_dir() else '  (missing)'}")
            return 0

        if args.apps_action == "status":
            status = args.status_value.strip().lower()
            if status not in db.APPLICATION_STATUSES:
                _err(f"Status must be one of: {', '.join(db.APPLICATION_STATUSES)}")
                return 1
            row = db.get_application(conn, args.id)
            if not row:
                _err(f"No application with id {args.id}.")
                return 1
            changes: dict[str, Any] = {"status": status}
            if status == "approved":
                changes["approved_at"] = db.now()
            if status == "sent" and not row.get("sent_date"):
                changes["sent_date"] = date.today().isoformat()
            db.update_application(conn, args.id, changes)
            _out(f"Application {args.id}: {row['status']} -> {status}")
            return 0

        if args.apps_action == "respond":
            if not db.get_application(conn, args.id):
                _err(f"No application with id {args.id}.")
                return 1
            db.update_application(conn, args.id, {"response": args.text, "status": "responded"})
            _out(f"Recorded response on application {args.id}.")
            return 0

        rows = db.list_applications(conn, status=args.status)
        if not rows:
            _out("No applications recorded.")
            return 0
        _out("  id  status     fit   ground   company                role")
        _out(" ---- ---------- ----- -------- --------------------- -------------------------")
        for row in rows:
            fit = f"{row['fit_score']:.0f}" if row.get("fit_score") is not None else "-"
            _out(
                f" {row['id']:>4}  {(row['status'] or '-'):<9} {fit:>4}  "
                f"{(row.get('grounding_status') or '-'):<8} "
                f"{(row['company'] or '-')[:21]:<21} {(row['role'] or '-')[:25]}"
            )
        _out("")
        _out(f"{len(rows)} application(s).")
    return 0


# --------------------------------------------------------------------------- pipeline


def _load_config(args: argparse.Namespace) -> Config | None:
    try:
        config = Config.load(getattr(args, "config", None))
    except ConfigError as exc:
        _err(str(exc))
        return None
    # Publish the configured provider before anything reaches for a model.
    config.llm.apply_to_env()
    return config


def cmd_answers(args: argparse.Namespace) -> int:
    """Standing answers to the form questions a resume cannot cover."""
    with db.session(args.db) as conn:
        action = args.answers_action

        if action == "add":
            try:
                answer_id = answers.add(
                    conn, args.pattern, args.answer,
                    kind=args.kind, company=args.company, notes=args.notes,
                )
            except ValueError as exc:
                _err(str(exc))
                return 1
            scope = f" (only for {args.company})" if args.company else ""
            _out(f"[{answer_id}] {answers.normalize(args.pattern)!r}{scope}")
            _out(f"      -> {args.answer}")
            cleared = answers.prune_answered(conn)
            if cleared:
                _out(f"That covers {cleared} question(s) that had blocked a form before.")
            return 0

        if action == "rm":
            if not answers.remove(conn, args.id):
                _err(f"No answer {args.id}.")
                return 1
            _out(f"Removed answer {args.id}.")
            return 0

        if action == "gaps":
            rows = answers.gaps(conn)
            if not rows:
                _out("No unanswered questions recorded yet.")
                _out("They accumulate when a form asks something nothing covers.")
                return 0
            _out("Questions that have blocked an application, most frequent first:")
            _out("")
            for row in rows:
                scope = f"  [{row['company']}]" if row.get("company") else ""
                _out(f"  {row['seen_count']:3d}x  {row['question']}{scope}")
            _out("")
            _out("Answer one with:")
            _out('  python -m jobsearch answers add "<part of the question>" "<your answer>"')
            return 0

        stored = answers.list_all(conn)
        if not stored:
            _out("No stored answers.")
            _out("")
            _out("Application forms ask things a resume does not cover -- work")
            _out("authorization, notice period, why this company. Record each answer")
            _out("once and forms fill from what you wrote:")
            _out('  python -m jobsearch answers add "visa sponsorship" "No"')
            _out("")
            _out("`answers gaps` lists the questions that have actually blocked you.")
            return 0
        for item in stored:
            scope = f"  [{item.company}]" if item.company else ""
            _out(f"[{item.id}] {item.pattern!r}  ({item.kind}){scope}")
            _out(f"      -> {item.answer}")
            if item.notes:
                _out(f"      note: {item.notes}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Launch the MCP server over stdio. No output here is for a human --
    stdout is the protocol channel, so anything printed would corrupt it.
    The tools resolve their own db path via $JOBSEARCH_DB / the project
    default, same as every other interface; --db just sets that variable
    for them since MCPServer.run() takes no db argument of its own.
    """
    if getattr(args, "db", None):
        os.environ["JOBSEARCH_DB"] = str(db.resolve_db_path(args.db))
    from .mcp.server import mcp
    mcp.run()
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve
    from .web.server import WebError

    config = _load_config(args)
    if config is None:
        return 1
    explicit_db = getattr(args, "db", None)
    db_path = db.resolve_db_path(explicit_db)
    using_turso = bool(os.environ.get("TURSO_DATABASE_URL")) and not explicit_db
    if not using_turso and not db_path.is_file():
        _err(f"No database at {db_path}. Run `python -m jobsearch init` first.")
        return 1

    public = args.host not in ("127.0.0.1", "localhost", "::1")

    def announce(url: str) -> None:
        _out(f"Review UI: {url}")
        if public:
            _out("Listening publicly, password required. Anyone with the password can")
            _out("read your history and approve applications.")
        else:
            _out("Loopback only -- nothing outside this machine can reach it.")
        _out("Ctrl-C to stop.")

    try:
        serve(
            db_path=db_path if explicit_db else None,  # None lets Turso apply when it is configured
            config=config,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser and not public,
            ready=announce,
        )
    except WebError as exc:
        _err(str(exc))
        return 1
    except OSError as exc:
        _err(f"Could not start the server on port {args.port}: {exc}")
        return 1
    _out("Stopped.")
    return 0


def cmd_competitions(args: argparse.Namespace) -> int:
    """Find open competitions worth entering, and list what is already tracked."""
    from .sourcing import competitions as comp

    with db.session(args.db) as conn:
        if args.competitions_action == "discover":
            found, errors = comp.discover(
                limit=args.limit,
                online_only=args.online_only,
                include_manual=not args.no_bookmarks,
            )
            for message in errors:
                _out(f"  skipped {message}")
            if not found:
                _out("Nothing found. Every source was unreachable.")
                return 1
            added, skipped = comp.save(conn, found)
            _out(f"Found {len(found)}; added {added}, already tracked {skipped}.")
            if added:
                _out("  python -m jobsearch competitions list")
            return 0

        rows = conn.execute(
            "SELECT name, category, deadline, status, tracks FROM competitions "
            "ORDER BY (deadline IS NULL), deadline, id DESC"
        ).fetchall()
        if not rows:
            _out("Nothing tracked yet.")
            _out("  python -m jobsearch competitions discover")
            return 0
        for r in rows:
            marker = "open" if r["status"] == "discovered" else "  - "
            deadline = f"  due {r['deadline']}" if r["deadline"] else ""
            _out(f"{marker} {r['name']}{deadline}")
            if r["tracks"]:
                _out(f"       tracks: {r['tracks']}")
        return 0


def cmd_config(args: argparse.Namespace) -> int:
    config = _load_config(args)
    if config is None:
        return 1

    if not config.exists():
        _out(f"No {CONFIG_NAME} yet.")
        example = db.PROJECT_ROOT / EXAMPLE_NAME
        if example.is_file():
            _out(f"Start from the annotated template:")
            _out(f"  copy {EXAMPLE_NAME} {CONFIG_NAME}")
        return 1

    _out(f"Config: {config.path}")
    _out("")
    _out(f"  model                 {llm.describe()}")
    _out(f"  autonomous            {config.autonomous}"
         + ("" if config.autonomous else "   <- nothing will be sent"))
    _out(f"  min_fit               {config.search.min_fit}")
    _out(f"  titles                {', '.join(config.search.titles) or '(any)'}")
    _out(f"  locations             {', '.join(config.search.locations) or '(any)'}"
         + ("   remote only" if config.search.remote_only else ""))
    if config.search.exclude_locations:
        _out(f"  never in              {', '.join(config.search.exclude_locations)}")
    if config.search.exclude_companies:
        _out(f"  exclude_companies     {', '.join(config.search.exclude_companies)}")
    if config.search.exclude_keywords:
        _out(f"  exclude_keywords      {', '.join(config.search.exclude_keywords)}")
    if config.search.exclude_title_keywords:
        _out(f"  exclude in titles     {', '.join(config.search.exclude_title_keywords)}")
    if config.search.require_title_keywords:
        _out(f"  title must say        {', '.join(config.search.require_title_keywords)}")
    if config.search.max_experience_years is not None:
        _out(f"  max experience asked  {config.search.max_experience_years} years")
    if config.search.include_freelance:
        _out("  freelance & contract  included, judged apart from the title and level rules")
    _out("")
    _out("  sources")
    if not config.sources:
        _out("    (none configured)")
    for source in config.sources.values():
        mark = "on " if source.enabled else "off"
        values: list[str] = []
        for key, value in source.settings.items():
            if key not in ("boards", "companies", "country") or not value:
                continue
            values.append(", ".join(str(v) for v in value) if isinstance(value, list) else str(value))
        detail = "  ".join(values)
        _out(f"    [{mark}] {source.name:<12} {detail}")
    _out("")
    _out("  limits")
    _out(f"    per day             {config.limits.max_applications_per_day}")
    _out(f"    per run             {config.limits.max_applications_per_run}")
    _out(f"    per company/week    {config.limits.max_per_company_per_week}")
    _out(f"    tailor per run      {config.limits.max_tailor_per_run}")
    _out("")
    _out("  dispatch")
    _out(f"    channel order       {' -> '.join(config.dispatch.channel_order)}")
    _out(f"    clean grounding     {config.dispatch.require_clean_grounding}")
    _out(f"    verified records    {config.dispatch.require_verified_records}")
    _out(f"    email               {'enabled' if config.dispatch.email.enabled else 'disabled'}")
    _out(f"    ats form            {'enabled' if config.dispatch.ats.enabled else 'disabled'}")

    problems = config.problems()
    warnings = config.warnings()
    if problems:
        _out("")
        _out(f"{len(problems)} problem(s) that would stop a run doing useful work:")
        for problem in problems:
            for line in _wrap(f"- {problem}", indent="  ", hanging=2):
                _out(line)
    if warnings:
        _out("")
        for warning in warnings:
            for line in _wrap(f"note: {warning}", indent="  ", hanging=2):
                _out(line)
    if not problems:
        _out("")
        _out("Config looks usable.")
    check_failed = False
    if getattr(args, "check_model", False):
        _out("")
        check_failed = not _check_model()
    return 1 if problems or check_failed else 0


def _check_model() -> bool:
    """One tiny call, to see that the model's key works; says why when it does not."""
    try:
        _text, usage = llm.call("Reply with the single word OK.", "ping", max_tokens=64)
    except llm.ModelError as exc:
        _out(f"Model check FAILED: {str(exc).splitlines()[0][:300]}")
        return False
    _out(f"Model check: {usage.get('provider')} answered ({usage.get('model')}).")
    return True


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    if config is None:
        return 1

    problems = config.problems()
    if problems and not args.force:
        _err("Config problems:")
        for problem in problems:
            _err(f"  - {problem}")
        _err("")
        _err("Fix them, or re-run with --force to proceed anyway.")
        return 1

    if args.autonomous:
        config.autonomous = True
    if args.review_only:
        config.autonomous = False

    mode = "dry-run" if args.dry_run else ("AUTONOMOUS" if config.autonomous else "review-only")
    _out(RULE)
    _out(f"Pipeline run -- mode: {mode}")
    if config.autonomous and not args.dry_run:
        _out("Applications passing every policy check will be SENT without further prompting.")
    else:
        _out("Nothing will be sent; everything lands in the review queue.")
    _out(RULE)
    _out("")

    with db.session(args.db) as conn:
        report = pipeline.run(
            conn,
            config,
            dry_run=args.dry_run,
            skip_sourcing=args.no_sourcing,
            limit=args.limit,
        )

    for line in report.log:
        _out(line)

    _out("")
    _out(RULE)
    _out(
        f"sourced {report.sourced} ({report.duplicates} dup) | competitions {report.competitions_found} | "
        f"scored {report.scored} | screened out {report.screened_out} | tailored {report.tailored} | "
        f"queued {report.queued} | sent {report.sent} | errors {len(report.errors)}"
    )
    _out(RULE)
    if report.queued:
        _out("")
        _out("Review what is waiting:")
        _out("  python -m jobsearch apps list --status drafted")
    return 1 if report.errors else 0


def cmd_jobs(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.jobs_action == "show":
            job = db.get_row(conn, "jobs", args.id)
            if not job:
                _err(f"No job {args.id}.")
                return 1
            description = job.pop("description", "") or ""
            _out(json.dumps(job, indent=2, ensure_ascii=False))
            if args.full and description:
                _out("")
                _out(description)
            return 0

        if args.jobs_action == "purge":
            cur = conn.execute("DELETE FROM jobs WHERE status IN ('skipped','failed')")
            _out(f"Removed {cur.rowcount} skipped/failed posting(s).")
            return 0

        if args.jobs_action == "retry":
            # A posting is marked failed when the model would not write it, and that is not always
            # the posting's doing: a model key that was refused once wrote off hundreds in one run.
            # This puts them back, with the score they had, to be tailored on the next run.
            sql = "UPDATE jobs SET status = 'scored', skip_reason = NULL WHERE status = 'failed'"
            params: tuple[str, ...] = ()
            if args.reason:
                sql += " AND skip_reason LIKE ?"
                params = (f"%{args.reason}%",)
            cur = conn.execute(sql, params)
            _out(f"Put {cur.rowcount} failed posting(s) back, to be tailored on the next run.")
            return 0

        if args.jobs_action == "rescore":
            # Skip decisions are only as good as the config and scoring that
            # produced them. Tune either one and the old verdicts are stale.
            cur = conn.execute(
                "UPDATE jobs SET status = 'new', skip_reason = NULL, fit_score = NULL "
                "WHERE status IN ('skipped', 'scored', 'failed')"
            )
            _out(f"Reset {cur.rowcount} posting(s) for rescoring.")
            _out("Next run will score and screen them again:")
            _out("  python -m jobsearch run --no-sourcing")
            return 0

        rows = sourcing.list_jobs(conn, status=args.status, limit=args.limit)
        if not rows:
            _out("No postings stored. Run: python -m jobsearch run")
            return 0
        _out("  id  fit   status    source      company               title")
        _out(" ---- ----  --------  ----------  --------------------  " + "-" * 34)
        for row in rows:
            fit = f"{row['fit_score']:.0f}" if row.get("fit_score") is not None else "-"
            _out(
                f" {row['id']:>4} {fit:>4}  {(row['status'] or '-'):<8}  "
                f"{(row['source'] or '-'):<10}  {(row['company'] or '-')[:20]:<20}  "
                f"{(row['title'] or '-')[:34]}"
            )
        _out("")
        _out(f"{len(rows)} posting(s).")
        if args.status is None:
            skipped = conn.execute(
                "SELECT skip_reason, COUNT(*) AS n FROM jobs WHERE status = 'skipped' "
                "GROUP BY skip_reason ORDER BY n DESC LIMIT 8"
            ).fetchall()
            if skipped:
                _out("")
                _out("Most common skip reasons:")
                for row in skipped:
                    _out(f"  {row['n']:>4}  {(row['skip_reason'] or '')[:80]}")
    return 0


def cmd_outreach(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        if args.outreach_action == "draft":
            application = db.get_application(conn, args.application)
            if not application:
                _err(f"No application {args.application}.")
                return 1
            job = db.get_row(conn, "jobs", application["job_id"]) if application.get("job_id") else None
            posting = (job or {}).get("description") or ""
            if not posting and application.get("resume_version"):
                candidate = db.PROJECT_ROOT / "output" / application["resume_version"] / "job_description.txt"
                if candidate.is_file():
                    posting = candidate.read_text(encoding="utf-8", errors="replace")
            if not posting:
                _err("Could not find the posting text for that application.")
                return 1

            g = graph.ProfileGraph.load(conn)
            plan = retrieval.build_plan(
                g, posting, company=application.get("company"), role=application.get("role")
            )
            try:
                result = linkedin_draft.draft(
                    posting,
                    plan.to_facts(),
                    channel=args.channel,
                    company=application.get("company") or "",
                    role=application.get("role") or "",
                    recipient_name=args.name or "",
                )
            except generate.GenerationError as exc:
                _err(str(exc))
                return 1

            recipient = args.to or (
                find_apply_email(posting) if args.channel == "email" else None
            )
            message_id = db.insert_row(
                conn,
                "messages",
                {
                    "application_id": args.application,
                    "channel": args.channel,
                    "recipient": recipient,
                    "recipient_name": args.name,
                    "subject": result.subject,
                    "body": result.body,
                    "deep_link": result.deep_link,
                    "status": "drafted",
                    "created_at": db.now(),
                },
            )
            _out(f"Drafted message {message_id} ({args.channel}, {len(result.body)} chars)")
            _out("")
            if result.subject:
                _out(f"Subject: {result.subject}")
                _out("")
            _out(result.body)
            if result.deep_link:
                _out("")
                _out(f"Open: {result.deep_link}")
                _out("Paste and send it yourself -- this tool never touches LinkedIn.")
            return 0

        if args.outreach_action == "send":
            message = db.get_row(conn, "messages", args.id)
            if not message:
                _err(f"No message {args.id}.")
                return 1
            if message["channel"] != "email":
                _err(
                    f"Message {args.id} is a {message['channel']}. LinkedIn messages are never "
                    "sent by this tool -- use `outreach show` and send it yourself."
                )
                return 1
            if not message.get("recipient"):
                _err("No recipient on that message. Re-draft with --to.")
                return 1
            config = _load_config(args)
            if config is None:
                return 1
            result = email_gmail.send(
                config.dispatch.email,
                db.PROJECT_ROOT,
                to=message["recipient"],
                subject=message.get("subject") or "Hello",
                body=message["body"],
                dry_run=args.dry_run,
            )
            _out(str(result))
            if result.ok and not args.dry_run:
                db.update_row(conn, "messages", args.id, {"status": "sent", "sent_at": db.now()})
            return 0 if result.ok else 1

        if args.outreach_action == "show":
            message = db.get_row(conn, "messages", args.id)
            if not message:
                _err(f"No message {args.id}.")
                return 1
            if message.get("subject"):
                _out(f"Subject: {message['subject']}")
                _out("")
            _out(message["body"])
            if message.get("deep_link"):
                _out("")
                _out(f"Open: {message['deep_link']}")
            return 0

        rows = db.list_table(conn, "messages", "id DESC")
        if not rows:
            _out("No outreach drafted yet.")
            _out("  python -m jobsearch outreach draft <application-id> --channel linkedin_note")
            return 0
        for row in rows:
            _out(f"[{row['id']}] {row['channel']:<14} {row['status']:<8} "
                 f"{(row.get('recipient') or row.get('recipient_name') or '-')}")
            for line in _wrap((row["body"] or "").replace("\n", " ")[:160], indent="      "):
                _out(line)
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if args.schedule_action == "install":
        result = scheduling.install(at=args.at, db_path=args.db)
        _out(result.detail)
        if not result.ok and result.command:
            _out("")
            _out("Equivalent command if you would rather run it yourself:")
            _out(f"  {result.command}")
        return 0 if result.ok else 1
    if args.schedule_action == "remove":
        result = scheduling.remove()
        _out(result.detail)
        return 0 if result.ok else 1
    result = scheduling.status()
    _out(result.detail)
    return 0 if result.ok else 1


def cmd_runs(args: argparse.Namespace) -> int:
    with db.session(args.db) as conn:
        rows = db.list_table(conn, "pipeline_runs", "id DESC")
    if not rows:
        _out("No pipeline runs recorded.")
        return 0
    _out("  id  started              mode          sourced  tailored  queued  sent  errors")
    _out(" ---- -------------------  ------------  -------  --------  ------  ----  ------")
    for row in rows[:20]:
        _out(
            f" {row['id']:>4}  {(row['started_at'] or '')[:19]:<19}  {(row['mode'] or '-'):<12}  "
            f"{row['sourced']:>7}  {row['tailored']:>8}  {row['queued']:>6}  "
            f"{row['sent']:>4}  {row['errors']:>6}"
        )
    return 0


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=None, help="SQLite file (default: <project>/jobsearch.db, or $JOBSEARCH_DB)")

    job_input = argparse.ArgumentParser(add_help=False)
    job_input.add_argument("--job-file", help="path to a file holding the job description")
    job_input.add_argument("--job-text", help="the job description inline")
    job_input.add_argument("--company")
    job_input.add_argument("--role")
    job_input.add_argument("--max-experiences", type=int, default=4)
    job_input.add_argument("--max-bullets", type=int, default=4, help="bullets per position (default: 4)")
    job_input.add_argument("--min-score", type=float, default=12.0,
                           help="drop records below this %% of the best match (default: 12)")
    job_input.add_argument("--verified-only", action="store_true",
                           help="use only rows a human has confirmed")

    parser = argparse.ArgumentParser(
        prog="python -m jobsearch",
        description="Personal job-search platform: a profile graph, and resumes generated from it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            getting started:
              python -m jobsearch init
              python -m jobsearch import linkedin ~/Downloads/LinkedInDataExport.zip
              python -m jobsearch import inbox inbox/
              python -m jobsearch link
              python -m jobsearch review
              python -m jobsearch profile

            applying:
              python -m jobsearch match  --job-file posting.txt
              python -m jobsearch tailor --job-file posting.txt --company Acme --role "Engineer"
            """
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", parents=[common], help="create or verify the database")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("profile", parents=[common], help="view or edit the profile")
    psub = p.add_subparsers(dest="profile_action")
    q = psub.add_parser("set", parents=[common], help="set a profile field")
    q.add_argument("key")
    q.add_argument("value")
    q = psub.add_parser("delete", parents=[common])
    q.add_argument("key")
    q = psub.add_parser("show", parents=[common])
    q.add_argument("--full", action="store_true")
    q.add_argument("--json", action="store_true")
    p.add_argument("--full", action="store_true", help="show every accomplishment and skill")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profile, profile_action="show")

    p = sub.add_parser("import", parents=[common], help="load career history from files")
    isub = p.add_subparsers(dest="import_action", required=True)
    q = isub.add_parser("linkedin", parents=[common], help="a LinkedIn data export (.zip or folder)")
    q.add_argument("path")
    q = isub.add_parser("document", parents=[common], help="a resume, review, or notes (pdf/docx/txt/md)")
    q.add_argument("path")
    q.add_argument("--hint", help="context to help extraction, e.g. 'my 2019 performance review'")
    q.add_argument("--model", default=generate.DEFAULT_MODEL,
                   help="override the model (default: the configured provider's own)")
    q.add_argument("--trust", action="store_true", help="mark extracted rows verified without review")
    q = isub.add_parser("inbox", parents=[common], help="every supported file in a folder")
    q.add_argument("path")
    q.add_argument("--model", default=generate.DEFAULT_MODEL,
                   help="override the model (default: the configured provider's own)")
    q.add_argument("--trust", action="store_true")
    q = isub.add_parser("github", parents=[common],
                        help="public repos as projects, language as skill evidence")
    q.add_argument("username")
    q.add_argument("--include-forks", action="store_true",
                   help="import forked repos too (skipped by default)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("link", parents=[common], help="attach skills to records that name them")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("review", parents=[common], help="confirm rows a model extracted")
    rsub = p.add_subparsers(dest="review_action")
    q = rsub.add_parser("confirm", parents=[common])
    q.add_argument("table")
    q.add_argument("id", type=int, nargs="?")
    p.set_defaults(func=cmd_review, review_action="list")

    p = sub.add_parser("sources", parents=[common], help="import history, and how to undo one")
    ssub = p.add_subparsers(dest="sources_action")
    q = ssub.add_parser("undo", parents=[common], help="delete every row an import created")
    q.add_argument("id", type=int)
    q.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_sources, sources_action="list")

    p = sub.add_parser("add", parents=[common], help="add a record by hand")
    p.add_argument("entity", choices=["experience", "achievement", "project", "education", "certification"])
    p.add_argument("--title", required=True)
    p.add_argument("--description")
    p.add_argument("--org", help="company or school")
    p.add_argument("--impact", help="quantified result (accomplishments)")
    p.add_argument("--skills", help="comma separated")
    p.add_argument("--field", help='e.g. "software engineering"')
    p.add_argument("--type", help="employment type")
    p.add_argument("--location")
    p.add_argument("--role")
    p.add_argument("--url")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--experience", type=int, help="parent position id (accomplishments)")
    p.add_argument("--project", type=int, help="parent project id (accomplishments)")
    p.add_argument("--education", type=int, help="parent education id (accomplishments)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("skill", parents=[common], help="skills and the records that prove them")
    ksub = p.add_subparsers(dest="skill_action", required=True)
    q = ksub.add_parser("add", parents=[common])
    q.add_argument("name")
    q.add_argument("--category")
    q.add_argument("--proficiency", choices=list(schema.PROFICIENCY_LEVELS))
    q.add_argument("--evidence", help="attach proof immediately, e.g. experience:3")
    q = ksub.add_parser("evidence", parents=[common], help="record where a skill was used")
    q.add_argument("name")
    q.add_argument("target_type", choices=list(db.EVIDENCE_TARGETS))
    q.add_argument("target_id", type=int)
    p.set_defaults(func=cmd_skill)

    p = sub.add_parser("list", parents=[common], help="list records of one kind")
    p.add_argument("table", choices=sorted(LISTABLE))
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", parents=[common], help="one record as JSON")
    p.add_argument("table", choices=sorted(LISTABLE))
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("rm", parents=[common], help="delete a record")
    p.add_argument("table", choices=sorted(LISTABLE))
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("match", parents=[common, job_input],
                       help="show what would be pulled for a posting, no API call")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("tailor", parents=[common, job_input],
                       help="generate a tailored resume + cover letter")
    p.add_argument("--job-url")
    p.add_argument("--source", default="manual")
    p.add_argument("--model", default=generate.DEFAULT_MODEL,
                   help="override the model (default: the configured provider's own)")
    p.add_argument("--max-tokens", type=int, default=generate.DEFAULT_MAX_TOKENS)
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true", help="show the plan and prompt, no API call")
    p.add_argument("--no-record", action="store_true")
    p.add_argument("--print", dest="print_docs", action="store_true")
    p.set_defaults(func=cmd_tailor)

    p = sub.add_parser("render", parents=[common], help="markdown -> print HTML (and PDF if possible)")
    p.add_argument("path")
    p.add_argument("--pdf", action="store_true")
    p.add_argument("--title")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("apps", parents=[common], help="the application log and approval gate")
    asub = p.add_subparsers(dest="apps_action")
    q = asub.add_parser("list", parents=[common])
    q.add_argument("--status", choices=list(db.APPLICATION_STATUSES))
    q = asub.add_parser("show", parents=[common])
    q.add_argument("id", type=int)
    q = asub.add_parser("status", parents=[common])
    q.add_argument("id", type=int)
    q.add_argument("status_value", metavar="status", help="/".join(db.APPLICATION_STATUSES))
    q = asub.add_parser("respond", parents=[common])
    q.add_argument("id", type=int)
    q.add_argument("text")
    p.set_defaults(func=cmd_apps, apps_action="list", status=None)

    # ---------------------------------------------------------------- pipeline

    config_opt = argparse.ArgumentParser(add_help=False)
    config_opt.add_argument("--config", help=f"path to {CONFIG_NAME} (default: alongside the database)")

    p = sub.add_parser("config", parents=[common, config_opt],
                       help="show the effective pipeline settings and any problems")
    p.add_argument("--check-model", action="store_true",
                   help="also make one tiny call to the model, to see that its key works")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("competitions", parents=[common],
                       help="find and track hackathons and case competitions")
    csub = p.add_subparsers(dest="competitions_action")
    d = csub.add_parser("discover", parents=[common],
                        help="search public sources for open competitions")
    d.add_argument("--limit", type=int, default=100,
                   help="stop after this many (default 100)")
    d.add_argument("--online-only", action="store_true",
                   help="skip anything that requires being somewhere in person")
    d.add_argument("--no-bookmarks", action="store_true",
                   help="omit rows for platforms that cannot be read automatically")
    d.set_defaults(func=cmd_competitions, competitions_action="discover")
    listp = csub.add_parser("list", parents=[common], help="show what is tracked, soonest deadline first")
    listp.set_defaults(func=cmd_competitions, competitions_action="list")
    p.set_defaults(func=cmd_competitions, competitions_action="list",
                   limit=100, online_only=False, no_bookmarks=False)

    p = sub.add_parser(
        "run",
        parents=[common, config_opt],
        help="one full pass: source, score, screen, tailor, verify, decide, dispatch",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="do everything except actually send (forms filled but not submitted)")
    p.add_argument("--autonomous", action="store_true",
                   help="force autonomous on for this run, whatever the config says")
    p.add_argument("--review-only", action="store_true",
                   help="force autonomous off for this run")
    p.add_argument("--no-sourcing", action="store_true",
                   help="skip fetching; work through postings already stored")
    p.add_argument("--limit", type=int, help="tailor at most N postings this run")
    p.add_argument("--force", action="store_true", help="run despite config problems")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("jobs", parents=[common], help="postings the pipeline has sourced")
    jsub = p.add_subparsers(dest="jobs_action")
    q = jsub.add_parser("list", parents=[common])
    q.add_argument("--status", choices=list(schema.JOB_STATUSES))
    q.add_argument("--limit", type=int, default=40)
    q = jsub.add_parser("show", parents=[common])
    q.add_argument("id", type=int)
    q.add_argument("--full", action="store_true", help="include the full posting text")
    jsub.add_parser("purge", parents=[common], help="delete skipped and failed postings")
    q = jsub.add_parser("retry", parents=[common],
                        help="put failed postings back to be tailored again on the next run")
    q.add_argument("--reason", help="only those whose failure message contains this text")
    jsub.add_parser("rescore", parents=[common],
                    help="re-evaluate stored postings after changing config or scoring")
    p.add_argument("--status", choices=list(schema.JOB_STATUSES))
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_jobs, jobs_action="list")

    p = sub.add_parser("runs", parents=[common], help="history of pipeline runs")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("answers", parents=[common],
                       help="standing answers to form questions a resume cannot cover")
    asub = p.add_subparsers(dest="answers_action")
    q = asub.add_parser("add", parents=[common], help="record one answer")
    q.add_argument("pattern", help="part of the question, e.g. 'visa sponsorship'")
    q.add_argument("answer", help="exactly what should be entered")
    q.add_argument("--kind", default="text", choices=list(answers.KINDS))
    q.add_argument("--company", help="restrict to one employer")
    q.add_argument("--notes")
    q = asub.add_parser("rm", parents=[common])
    q.add_argument("id", type=int)
    asub.add_parser("gaps", parents=[common],
                    help="questions that blocked an application and have no answer")
    asub.add_parser("list", parents=[common])
    p.set_defaults(func=cmd_answers, answers_action="list")

    p = sub.add_parser("web", parents=[common, config_opt],
                       help="local browser UI for reviewing jobs, drafts, and the profile")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8765),
                   help="defaults to $PORT, which is what a host like Render sets")
    p.add_argument("--host", default="127.0.0.1",
                   help="0.0.0.0 to accept outside connections. Needs JOBSEARCH_PASSWORD set.")
    p.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("mcp", parents=[common],
                       help="run as an MCP server (stdio) for Claude Code / Claude Desktop")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("outreach", parents=[common, config_opt],
                       help="cold email and LinkedIn message drafts")
    osub = p.add_subparsers(dest="outreach_action")
    q = osub.add_parser("draft", parents=[common], help="write a message for an application")
    q.add_argument("application", type=int)
    q.add_argument("--channel", default="linkedin_note", choices=list(schema.MESSAGE_CHANNELS))
    q.add_argument("--to", help="recipient email (email channel)")
    q.add_argument("--name", help="recipient's name, if you know it")
    q = osub.add_parser("show", parents=[common])
    q.add_argument("id", type=int)
    q = osub.add_parser("send", parents=[common, config_opt],
                        help="send a drafted email (LinkedIn messages are never sent)")
    q.add_argument("id", type=int)
    q.add_argument("--dry-run", action="store_true")
    osub.add_parser("list", parents=[common])
    p.set_defaults(func=cmd_outreach, outreach_action="list")

    p = sub.add_parser("schedule", parents=[common], help="run the pipeline on a daily timer")
    schsub = p.add_subparsers(dest="schedule_action")
    q = schsub.add_parser("install", parents=[common])
    q.add_argument("--at", default="08:00", help="HH:MM, 24-hour (default: 08:00)")
    schsub.add_parser("remove", parents=[common])
    schsub.add_parser("status", parents=[common])
    p.set_defaults(func=cmd_schedule, schedule_action="status", at="08:00")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        # Every command needs TURSO_*/JOBSEARCH_PASSWORD/etc from .env, not just
        # `web` -- but only a real invocation reads it. Code that hands main() its
        # own argv (the tests) must not inherit the production credentials.
        from .llm import load_dotenv

        load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _err("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
