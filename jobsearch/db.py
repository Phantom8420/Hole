"""Database access for the profile graph.

Thin, explicit helpers over sqlite3 -- no ORM. The interesting logic lives in
three places: organization/skill deduplication, the skill-evidence link, and the
migration off the old flat table.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from . import schema

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_DB_NAME = "jobsearch.db"

APPLICATION_STATUSES = schema.APPLICATION_STATUSES

# Trailing corporate noise, so "Google LLC" and "Google, Inc." are one node.
ORG_SUFFIX_RE = re.compile(
    r"[\s,]+(?:inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|limited|corp|corp\.|corporation|"
    r"gmbh|plc|co|co\.|company|s\.a\.|b\.v\.|pvt|pvt\.|private)$",
    re.IGNORECASE,
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_org(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[‘’“”]", "", text)
    previous = None
    while previous != text:  # "Foo Inc. Ltd." -> "foo"
        previous = text
        text = ORG_SUFFIX_RE.sub("", text).strip(" ,.")
    return re.sub(r"\s+", " ", text).strip()


def normalize_skill(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[‘’“”]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,")


# --------------------------------------------------------------------------- connection


def resolve_db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("JOBSEARCH_DB")
    if env:
        return Path(env).expanduser().resolve()
    return PROJECT_ROOT / DEFAULT_DB_NAME


def connect(db_path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """The app's own database. Local sqlite normally; routed through Turso
    instead when TURSO_DATABASE_URL is set, so nothing is lost to a redeploy
    or spin-down on a host with no persistent disk (e.g. Render Free).
    """
    # A path someone named means that file. Turso is only what an unqualified
    # call resolves to when it is configured -- never an override of a file, which
    # is how a test run once wrote its fixtures into the production database.
    turso_url = os.environ.get("TURSO_DATABASE_URL") if db_path is None else None
    if turso_url:
        from . import libsql_shim

        conn = libsql_shim.connect(turso_url, os.environ.get("TURSO_AUTH_TOKEN", ""))
        fresh = not conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    else:
        path = resolve_db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not fresh:
        migrate_v1_to_v2(conn)
    conn.executescript(schema.DDL)
    add_missing_columns(conn)
    conn.execute("PRAGMA foreign_keys = ON")  # executescript resets pragmas
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(schema.SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


# Columns added to existing tables after a schema version shipped. CREATE TABLE
# IF NOT EXISTS will not add them to a database that already exists, so they get
# ALTERed in. Additive only -- nothing here ever drops or rewrites data.
ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "competitions": [
        ("deadline", "TEXT"),
        ("team_size", "TEXT"),
        ("tracks", "TEXT"),
        ("apply_url", "TEXT"),
        ("discovered_at", "TEXT"),
        ("discovery_source", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'entered'"),
    ],
    "applications": [
        ("job_id", "INTEGER"),
        ("channel", "TEXT"),
        ("fit_score", "REAL"),
        ("grounding_status", "TEXT"),
        ("decision_reasons", "TEXT"),
        ("approved_at", "TEXT"),
        ("dispatch_error", "TEXT"),
        ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ],
}


def add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    added: list[str] = []
    for table, columns in ADDITIVE_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for name, declaration in columns:
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            added.append(f"{table}.{name}")
    return added


@contextmanager
def session(db_path: str | os.PathLike[str] | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return {k: row[k] for k in row.keys()} if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in r.keys()} for r in rows]


# --------------------------------------------------------------------------- generic CRUD


def insert_row(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    payload = {k: v for k, v in data.items() if v is not None}
    if not payload:
        raise ValueError(f"Nothing to insert into {table}")
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{k}" for k in payload)
    cur = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", payload)
    return int(cur.lastrowid)


def update_row(conn: sqlite3.Connection, table: str, row_id: int, changes: dict[str, Any]) -> bool:
    if not changes:
        return False
    payload = dict(changes)
    assignments = ", ".join(f"{col} = :{col}" for col in payload)
    payload["__id"] = row_id
    cur = conn.execute(f"UPDATE {table} SET {assignments} WHERE id = :__id", payload)
    return cur.rowcount > 0


def get_row(conn: sqlite3.Connection, table: str, row_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def delete_row(conn: sqlite3.Connection, table: str, row_id: int) -> bool:
    cur = conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    return cur.rowcount > 0


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# --------------------------------------------------------------------------- provenance


def create_source(
    conn: sqlite3.Connection,
    kind: str,
    *,
    location: str | None = None,
    label: str | None = None,
    notes: str | None = None,
) -> int:
    return insert_row(
        conn,
        "sources",
        {
            "kind": kind,
            "location": location,
            "label": label,
            "imported_at": now(),
            "notes": notes,
        },
    )


def list_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute("SELECT * FROM sources ORDER BY id DESC"))


def source_summary(conn: sqlite3.Connection, source_id: int) -> dict[str, int]:
    """How many rows each import produced, so an import can be audited or undone."""
    counts: dict[str, int] = {}
    for table in schema.ENTITY_TABLES:
        found = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
        if found:
            counts[table] = int(found)
    return counts


def delete_source_rows(conn: sqlite3.Connection, source_id: int) -> dict[str, int]:
    """Undo an import. Cascades take care of dependent achievements and evidence."""
    removed: dict[str, int] = {}
    for table in ("achievements", "skill_evidence_orphans", *schema.ENTITY_TABLES):
        if table == "skill_evidence_orphans":
            continue
        cur = conn.execute(f"DELETE FROM {table} WHERE source_id = ?", (source_id,))
        if cur.rowcount:
            removed[table] = removed.get(table, 0) + cur.rowcount
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return removed


# --------------------------------------------------------------------------- profile


def get_profile(conn: sqlite3.Connection) -> dict[str, Any]:
    row = row_to_dict(conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()) or {}
    row.pop("id", None)
    attributes = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM profile_attributes ORDER BY key")
    }
    return {**{k: v for k, v in row.items() if v is not None}, **attributes}


PROFILE_COLUMNS = (
    "full_name", "headline", "summary", "email", "phone", "location",
    "website", "github", "linkedin_url", "work_authorization",
)

# Accept the shorthand people actually type.
PROFILE_ALIASES = {
    "name": "full_name",
    "linkedin": "linkedin_url",
    "site": "website",
    "url": "website",
    "about": "summary",
    "title": "headline",
    "city": "location",
}


def set_profile_field(conn: sqlite3.Connection, key: str, value: str) -> str:
    key = PROFILE_ALIASES.get(key.strip().lower(), key.strip().lower())
    value = value.strip()
    if key in PROFILE_COLUMNS:
        conn.execute("INSERT OR IGNORE INTO profile(id) VALUES (1)")
        conn.execute(f"UPDATE profile SET {key} = ?, updated_at = ? WHERE id = 1", (value, now()))
    else:
        conn.execute(
            "INSERT INTO profile_attributes(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    return key


def delete_profile_field(conn: sqlite3.Connection, key: str) -> bool:
    key = PROFILE_ALIASES.get(key.strip().lower(), key.strip().lower())
    if key in PROFILE_COLUMNS:
        cur = conn.execute(f"UPDATE profile SET {key} = NULL WHERE id = 1")
        return cur.rowcount > 0
    cur = conn.execute("DELETE FROM profile_attributes WHERE key = ?", (key,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- organizations


def upsert_organization(
    conn: sqlite3.Connection,
    name: str | None,
    *,
    kind: str | None = None,
    url: str | None = None,
    industry: str | None = None,
) -> int | None:
    if not name or not name.strip():
        return None
    normalized = normalize_org(name)
    if not normalized:
        return None
    existing = conn.execute(
        "SELECT id FROM organizations WHERE normalized_name = ?", (normalized,)
    ).fetchone()
    if existing:
        org_id = int(existing["id"])
        # Fill in blanks discovered by a later import without clobbering good data.
        for column, value in (("kind", kind), ("url", url), ("industry", industry)):
            if value:
                conn.execute(
                    f"UPDATE organizations SET {column} = COALESCE({column}, ?) WHERE id = ?",
                    (value, org_id),
                )
        return org_id
    return insert_row(
        conn,
        "organizations",
        {
            "name": name.strip(),
            "normalized_name": normalized,
            "kind": kind,
            "url": url,
            "industry": industry,
        },
    )


def organization_name(conn: sqlite3.Connection, org_id: int | None) -> str | None:
    if not org_id:
        return None
    row = conn.execute("SELECT name FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return row["name"] if row else None


# --------------------------------------------------------------------------- skills


def upsert_skill(
    conn: sqlite3.Connection,
    name: str,
    *,
    category: str | None = None,
    proficiency: str | None = None,
    years_experience: float | None = None,
    last_used: str | None = None,
    source_id: int | None = None,
    verified: int = 0,
) -> int | None:
    normalized = normalize_skill(name)
    if not normalized:
        return None
    existing = conn.execute(
        "SELECT id FROM skills WHERE normalized_name = ?", (normalized,)
    ).fetchone()
    if existing:
        skill_id = int(existing["id"])
        for column, value in (
            ("category", category),
            ("proficiency", proficiency),
            ("years_experience", years_experience),
            ("last_used", last_used),
        ):
            if value is not None:
                conn.execute(
                    f"UPDATE skills SET {column} = COALESCE({column}, ?) WHERE id = ?",
                    (value, skill_id),
                )
        if verified:
            conn.execute("UPDATE skills SET verified = 1 WHERE id = ?", (skill_id,))
        return skill_id
    return insert_row(
        conn,
        "skills",
        {
            "name": name.strip(),
            "normalized_name": normalized,
            "category": category,
            "proficiency": proficiency,
            "years_experience": years_experience,
            "last_used": last_used,
            "source_id": source_id,
            "verified": verified,
        },
    )


EVIDENCE_TARGETS = ("experience", "project", "achievement", "education", "certification")


def add_skill_evidence(
    conn: sqlite3.Connection,
    skill_id: int,
    target_type: str,
    target_id: int,
    *,
    note: str | None = None,
) -> int | None:
    """Link a skill to the record that proves it. Idempotent."""
    if target_type not in EVIDENCE_TARGETS:
        raise ValueError(f"target_type must be one of {EVIDENCE_TARGETS}, got {target_type!r}")
    column = f"{target_type}_id"
    existing = conn.execute(
        f"SELECT id FROM skill_evidence WHERE skill_id = ? AND {column} = ?",
        (skill_id, target_id),
    ).fetchone()
    if existing:
        return int(existing["id"])
    return insert_row(
        conn, "skill_evidence", {"skill_id": skill_id, column: target_id, "note": note}
    )


def skill_evidence_counts(conn: sqlite3.Connection) -> dict[int, int]:
    return {
        int(r["skill_id"]): int(r["n"])
        for r in conn.execute(
            "SELECT skill_id, COUNT(*) AS n FROM skill_evidence GROUP BY skill_id"
        )
    }


def unevidenced_skills(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Skills nothing in the profile actually demonstrates. These never reach a resume."""
    return rows_to_dicts(
        conn.execute(
            "SELECT s.* FROM skills s "
            "LEFT JOIN skill_evidence e ON e.skill_id = s.id "
            "WHERE e.id IS NULL ORDER BY s.name"
        )
    )


def link_skills_to(
    conn: sqlite3.Connection,
    skill_names: Sequence[str],
    target_type: str,
    target_id: int,
    *,
    source_id: int | None = None,
    verified: int = 0,
) -> list[int]:
    """Create/find each skill and attach evidence pointing at one record."""
    ids: list[int] = []
    for name in skill_names:
        skill_id = upsert_skill(conn, name, source_id=source_id, verified=verified)
        if skill_id is None:
            continue
        add_skill_evidence(conn, skill_id, target_type, target_id)
        ids.append(skill_id)
    return ids


# --------------------------------------------------------------------------- reads


def list_experiences(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT e.*, o.name AS organization FROM experiences e "
            "LEFT JOIN organizations o ON o.id = e.organization_id "
            "ORDER BY e.is_current DESC, IFNULL(e.end_date, '9999') DESC, e.start_date DESC"
        )
    )


def list_education(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT ed.*, o.name AS organization FROM education ed "
            "LEFT JOIN organizations o ON o.id = ed.organization_id "
            "ORDER BY IFNULL(ed.end_date, '9999') DESC"
        )
    )


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return rows_to_dicts(
        conn.execute(
            "SELECT p.*, o.name AS organization FROM projects p "
            "LEFT JOIN organizations o ON o.id = p.organization_id "
            "ORDER BY IFNULL(p.end_date, '9999') DESC, p.id DESC"
        )
    )


def list_achievements(
    conn: sqlite3.Connection,
    *,
    experience_id: int | None = None,
    project_id: int | None = None,
    education_id: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("experience_id", experience_id),
        ("project_id", project_id),
        ("education_id", education_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    sql = "SELECT * FROM achievements"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id"
    return rows_to_dicts(conn.execute(sql, params))


def list_skills(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    counts = skill_evidence_counts(conn)
    skills = rows_to_dicts(conn.execute("SELECT * FROM skills ORDER BY name"))
    for skill in skills:
        skill["evidence_count"] = counts.get(int(skill["id"]), 0)
    return skills


def list_table(conn: sqlite3.Connection, table: str, order: str = "id") -> list[dict[str, Any]]:
    return rows_to_dicts(conn.execute(f"SELECT * FROM {table} ORDER BY {order}"))


def profile_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {table: count_rows(conn, table) for table in schema.ENTITY_TABLES}
    counts["organizations"] = count_rows(conn, "organizations")
    counts["skill_evidence"] = count_rows(conn, "skill_evidence")
    return counts


# --------------------------------------------------------------------------- applications


def insert_application(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    payload = {"status": "drafted", "source": "manual", **data}
    return insert_row(conn, "applications", payload)


def list_applications(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applications"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    return rows_to_dicts(conn.execute(sql, params))


def get_application(conn: sqlite3.Connection, application_id: int) -> dict[str, Any] | None:
    return get_row(conn, "applications", application_id)


def update_application(conn: sqlite3.Connection, application_id: int, changes: dict[str, Any]) -> bool:
    return update_row(conn, "applications", application_id, changes)


# --------------------------------------------------------------------------- migration


def migrate_v1_to_v2(conn: sqlite3.Connection) -> bool:
    """Lift a Phase 1 flat `achievements` table into the graph.

    Phase 1 stored employer, title, dates, and skills on every bullet. Each
    distinct employer becomes an organization and one experience; the bullets
    become achievements hanging off it. Nothing is discarded.
    """
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "achievements" not in tables:
        return False
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(achievements)")}
    if "employer" not in columns:  # already the graph shape
        return False

    legacy = rows_to_dicts(conn.execute("SELECT * FROM achievements"))
    legacy_profile: dict[str, str] = {}
    if "profile" in tables:
        profile_columns = {r["name"] for r in conn.execute("PRAGMA table_info(profile)")}
        if profile_columns == {"key", "value"}:
            legacy_profile = {
                r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM profile")
            }
            conn.execute("ALTER TABLE profile RENAME TO profile_v1_backup")

    conn.execute("ALTER TABLE achievements RENAME TO achievements_v1_backup")
    conn.executescript(schema.DDL)
    conn.execute("PRAGMA foreign_keys = ON")

    source_id = create_source(
        conn, "phase1_migration", label="Phase 1 flat achievements table", notes="automatic"
    )

    for key, value in legacy_profile.items():
        set_profile_field(conn, key, value)

    # Group bullets by (employer, field) -- that pair is the closest thing the
    # old schema had to a position.
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in legacy:
        grouped.setdefault((row.get("employer") or "", row.get("field") or ""), []).append(row)

    import json as _json

    for (employer, field), rows in grouped.items():
        starts = sorted(r["start_date"] for r in rows if r.get("start_date"))
        ends = sorted((r["end_date"] for r in rows if r.get("end_date")), reverse=True)
        is_education = field.strip().lower() in ("education", "school", "degree")

        org_id = upsert_organization(
            conn, employer or None, kind="school" if is_education else "company"
        )

        if is_education:
            parent_column = "education_id"
            parent_id = insert_row(
                conn,
                "education",
                {
                    "organization_id": org_id,
                    "degree": rows[0].get("title"),
                    "start_date": starts[0] if starts else None,
                    "end_date": ends[0] if ends else None,
                    "source_id": source_id,
                    "verified": 1,
                },
            )
            evidence_type = "education"
        else:
            parent_column = "experience_id"
            parent_id = insert_row(
                conn,
                "experiences",
                {
                    "organization_id": org_id,
                    "title": rows[0].get("title") or (field or "Experience"),
                    "start_date": starts[0] if starts else None,
                    "end_date": ends[0] if ends else None,
                    "is_current": 0 if ends else 1,
                    "field": field or None,
                    "source_id": source_id,
                    "verified": 1,
                },
            )
            evidence_type = "experience"

        for row in rows:
            achievement_id = insert_row(
                conn,
                "achievements",
                {
                    parent_column: parent_id,
                    "title": row.get("title") or "Achievement",
                    "description": row.get("description") or "",
                    "quantified_impact": row.get("quantified_impact"),
                    "start_date": row.get("start_date"),
                    "end_date": row.get("end_date"),
                    "source_id": source_id,
                    "verified": 1,
                },
            )
            raw_skills = row.get("skills")
            names: list[str] = []
            if raw_skills:
                try:
                    parsed = _json.loads(raw_skills)
                    names = [str(s) for s in parsed] if isinstance(parsed, list) else []
                except (ValueError, TypeError):
                    names = [s.strip() for s in str(raw_skills).split(",") if s.strip()]
            for name in names:
                skill_id = upsert_skill(conn, name, source_id=source_id, verified=1)
                if skill_id is not None:
                    add_skill_evidence(conn, skill_id, "achievement", achievement_id)
                    add_skill_evidence(conn, skill_id, evidence_type, parent_id)

    conn.commit()
    return True
