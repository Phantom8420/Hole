"""The profile graph.

Modelled on the shape of a LinkedIn profile rather than a flat list of bullets:
a person holds positions at organizations, accomplishments hang off a specific
position or project, and a skill is only real if some record proves it.

Two properties matter more than anything else here:

1. Accomplishments are *attached*. A bullet belongs to the job where it happened,
   so a generated resume can put it under the right employer and date range
   without the model having to guess.
2. Skills carry evidence. `skill_evidence` links every claimed skill back to the
   experience, project, achievement, or certification that demonstrates it. A
   skill with no evidence row never reaches a resume, which is what makes
   "never claim a skill just because the posting asks for it" enforceable in
   code instead of hopefully in a prompt.

Every row also carries provenance: which import it came from, and whether a
human has confirmed it. Facts extracted by a model from a PDF are not treated
as equal to facts a person typed, and autonomous sending can be restricted to
confirmed rows.
"""

from __future__ import annotations

SCHEMA_VERSION = 4

# --------------------------------------------------------------------------- provenance

SOURCE_KINDS = (
    "manual",
    "linkedin_export",
    "resume",
    "document",
    "interview",
    "github",
    "phase1_migration",
)

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Where a fact came from. Every entity points at one of these.
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,               -- manual / linkedin_export / resume / document / ...
    location TEXT,                    -- file path or URL it was read from
    label TEXT,
    imported_at TEXT NOT NULL,
    notes TEXT
);

-- ------------------------------------------------------------------ the person

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    full_name TEXT,
    headline TEXT,
    summary TEXT,
    email TEXT,
    phone TEXT,
    location TEXT,
    website TEXT,
    github TEXT,
    linkedin_url TEXT,
    work_authorization TEXT,
    updated_at TEXT
);

-- Anything else worth keeping that has no column of its own (pronouns, salary
-- floor, portfolio links, clearance...). Passed through to generation as-is.
CREATE TABLE IF NOT EXISTS profile_attributes (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ------------------------------------------------------------------ entities

-- Companies and schools, deduplicated by normalized name so "Google" and
-- "Google LLC" collapse into one node.
CREATE TABLE IF NOT EXISTS organizations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    kind TEXT,                        -- company / school / nonprofit / client / self
    industry TEXT,
    url TEXT
);

CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    employment_type TEXT,             -- full-time / contract / internship / freelance
    location TEXT,
    location_type TEXT,               -- remote / hybrid / onsite
    start_date TEXT,                  -- YYYY-MM or YYYY-MM-DD
    end_date TEXT,                    -- NULL means current
    is_current INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    field TEXT,                       -- "software engineering", "marketing", ...
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS education (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    degree TEXT,
    field_of_study TEXT,
    start_date TEXT,
    end_date TEXT,
    grade TEXT,
    activities TEXT,
    description TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    role TEXT,
    url TEXT,
    start_date TEXT,
    end_date TEXT,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    field TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS certifications (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    issuer TEXT,
    issue_date TEXT,
    expiry_date TEXT,
    credential_id TEXT,
    url TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

-- The bullets. Attached to the position, project, or degree where they happened.
-- Exactly one parent, enforced by the CHECK; a standalone achievement (all three
-- NULL) is not allowed, because an accomplishment with no context cannot be
-- placed on a resume honestly.
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY,
    experience_id INTEGER REFERENCES experiences(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    education_id INTEGER REFERENCES education(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    quantified_impact TEXT,           -- free text, e.g. "cut load time 40%"
    start_date TEXT,
    end_date TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    CHECK (
        (experience_id IS NOT NULL)
      + (project_id IS NOT NULL)
      + (education_id IS NOT NULL) = 1
    )
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    category TEXT,                    -- language / framework / tool / domain / soft
    proficiency TEXT,                 -- familiar / working / advanced / expert
    years_experience REAL,
    last_used TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

-- Proof that a claimed skill was actually used somewhere. Exactly one target.
CREATE TABLE IF NOT EXISTS skill_evidence (
    id INTEGER PRIMARY KEY,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    experience_id INTEGER REFERENCES experiences(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    achievement_id INTEGER REFERENCES achievements(id) ON DELETE CASCADE,
    education_id INTEGER REFERENCES education(id) ON DELETE CASCADE,
    certification_id INTEGER REFERENCES certifications(id) ON DELETE CASCADE,
    note TEXT,
    CHECK (
        (experience_id IS NOT NULL)
      + (project_id IS NOT NULL)
      + (achievement_id IS NOT NULL)
      + (education_id IS NOT NULL)
      + (certification_id IS NOT NULL) = 1
    )
);

-- ------------------------------------------------------------------ the long tail

-- Hackathons, case competitions, finance competitions. Kept separate from
-- `awards` because a competition entry does not always mean a win --
-- `result` is free text ("Winner", "Finalist", "Participant", or blank while
-- pending) rather than an implicit "this was an award."
CREATE TABLE IF NOT EXISTS competitions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,           -- hackathon / case_competition / finance_competition / other
    result TEXT,
    period TEXT,
    description TEXT,
    tech TEXT,                        -- comma-separated, free text
    url TEXT,
    -- Everything below is for opportunities DISCOVERED but not yet entered.
    -- An entry the candidate typed in after the fact leaves these blank; one
    -- found by `competitions discover` fills them so the decision to enter can
    -- be made from the row itself.
    deadline TEXT,                    -- ISO date where known, else free text
    team_size TEXT,                   -- free text: "2-4", "solo or team", ...
    tracks TEXT,                      -- comma-separated themes announced by the organizer
    apply_url TEXT,                   -- where registration actually happens
    discovered_at TEXT,               -- set only by the scraper; NULL if hand-entered
    discovery_source TEXT,            -- which connector found it
    status TEXT NOT NULL DEFAULT 'entered',  -- discovered / shortlisted / entered / dismissed
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS awards (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    issuer TEXT,
    date TEXT,
    description TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    publisher TEXT,
    date TEXT,
    url TEXT,
    description TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS languages (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    proficiency TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS volunteering (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
    role TEXT,
    cause TEXT,
    start_date TEXT,
    end_date TEXT,
    description TEXT,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

-- Third-party words about the candidate. Never paraphrased into resume bullets
-- as if they were the candidate's own claims; available for cover letter tone
-- and for interview prep.
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY,
    author_name TEXT,
    author_title TEXT,
    author_organization TEXT,
    relationship TEXT,
    date TEXT,
    text TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    verified INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ applications

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    company TEXT,
    role TEXT,
    source TEXT,                      -- greenhouse / lever / ashby / adzuna / usajobs / manual
    job_url TEXT,
    resume_version TEXT,              -- output folder holding the documents
    status TEXT,                      -- drafted / approved / sent / responded / rejected
    channel TEXT,                     -- email / ats_form / manual
    fit_score REAL,
    grounding_status TEXT,            -- clean / flagged
    decision_reasons TEXT,            -- JSON array: why the policy engine chose this
    approved_at TEXT,
    sent_date TEXT,
    dispatch_error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    response TEXT
);

-- Standing answers to the questions application forms ask that are not on a
-- resume: work authorization, sponsorship, notice period, phone, why-this-
-- company. Filling one of these from here is retrieval, not invention -- the
-- answer is whatever the candidate wrote, verbatim. Nothing is ever generated
-- into this table, which is what keeps auto-submit inside the grounding rule.
--
-- `pattern` is matched case-insensitively against the form's question label
-- after punctuation is stripped. `company` scopes an answer to one employer so
-- "why do you want to work here" can differ per application.
CREATE TABLE IF NOT EXISTS application_answers (
    id INTEGER PRIMARY KEY,
    pattern TEXT NOT NULL,
    answer TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',   -- text / choice / boolean
    company TEXT,                        -- NULL = applies to every employer
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS publication_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('publication', 'resource', 'idea')),
    title TEXT NOT NULL,
    url TEXT,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'completed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A lightweight personal workspace alongside the job-search data.  Ideas are
-- deliberately separate from actionable to-dos so a rough thought is never
-- presented as work that has fallen behind.
CREATE TABLE IF NOT EXISTS todo_items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('idea', 'todo')),
    text TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_todo_items_kind_created
    ON todo_items(kind, completed, id DESC);

CREATE INDEX IF NOT EXISTS idx_answers_pattern ON application_answers(pattern);

-- Questions a form asked that nothing in application_answers covered. Recorded
-- rather than discarded so `answers gaps` can show what to write next, drawn
-- from real postings instead of guesses about what forms ask.
CREATE TABLE IF NOT EXISTS unanswered_questions (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    -- Empty string rather than NULL for "any employer": SQLite treats NULLs as
    -- distinct in a UNIQUE constraint, so a nullable column here would insert a
    -- duplicate row on every repeat instead of counting it.
    company TEXT NOT NULL DEFAULT '',
    job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT NOT NULL,
    UNIQUE(question, company)
);

-- ------------------------------------------------------------------ pipeline

-- Postings pulled from job boards. `fingerprint` collapses the same role
-- arriving from two different sources.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    company TEXT,
    title TEXT,
    location TEXT,
    remote INTEGER NOT NULL DEFAULT 0,
    url TEXT,
    apply_url TEXT,
    description TEXT,
    compensation TEXT,
    posted_at TEXT,
    employment_type TEXT,                 -- internship / full_time / part_time / contract / freelance, where the source says
    deadline TEXT,                        -- last day to apply or enter, for gigs that have one
    discovered_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    fit_score REAL,
    status TEXT NOT NULL DEFAULT 'new',   -- new / scored / skipped / tailored / applied / failed / closed
    skip_reason TEXT
);

-- One row per `run`, so an unattended pipeline can be audited after the fact.
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT,                        -- autonomous / review-only / dry-run
    sourced INTEGER NOT NULL DEFAULT 0,
    scored INTEGER NOT NULL DEFAULT 0,
    tailored INTEGER NOT NULL DEFAULT 0,
    queued INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

-- Outbound drafts that are not ATS applications: cold emails, and LinkedIn
-- messages the human sends by hand.
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,            -- email / linkedin_dm / linkedin_note
    recipient TEXT,
    recipient_name TEXT,
    subject TEXT,
    body TEXT NOT NULL,
    deep_link TEXT,                   -- for linkedin_*: where the human sends it
    status TEXT NOT NULL DEFAULT 'drafted',  -- drafted / approved / sent / copied
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ach_experience ON achievements(experience_id);
CREATE INDEX IF NOT EXISTS idx_ach_project ON achievements(project_id);
CREATE INDEX IF NOT EXISTS idx_evidence_skill ON skill_evidence(skill_id);
CREATE INDEX IF NOT EXISTS idx_exp_org ON experiences(organization_id);
CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_messages_app ON messages(application_id);
"""

APPLICATION_STATUSES = ("drafted", "approved", "sent", "responded", "rejected")
JOB_STATUSES = ("new", "scored", "skipped", "tailored", "applied", "failed", "closed")
MESSAGE_CHANNELS = ("email", "linkedin_dm", "linkedin_note")
DISPATCH_CHANNELS = ("email", "ats_form", "manual")
PROFICIENCY_LEVELS = ("familiar", "working", "advanced", "expert")

# Entity tables that carry provenance + a verified flag, in the order a resume
# would present them.
ENTITY_TABLES = (
    "experiences",
    "achievements",
    "projects",
    "education",
    "skills",
    "certifications",
    "awards",
    "publications",
    "volunteering",
    "languages",
    "recommendations",
)
