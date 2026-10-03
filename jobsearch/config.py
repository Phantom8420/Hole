"""Pipeline configuration.

Read from `config.toml` next to the database. The file is never rewritten by the
tool, so comments and hand-edits survive.

One setting matters more than the rest: `autonomous`. It ships **false**. Sending
an application is outward-facing and irreversible -- it reaches a real employer
under your name -- so the pipeline will source, score, tailor, and verify without
it, and stop at the send step. Flip it to true when you have watched a few runs
and trust the fit scores.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import PROJECT_ROOT

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"


class ConfigError(RuntimeError):
    pass


def _get(mapping: dict[str, Any], path: str, default: Any = None) -> Any:
    node: Any = mapping
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


@dataclass
class SearchConfig:
    titles: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    # False: a remote posting passes whatever place it names. True: one that names a
    # place ("Remote in Canada", "LATAM") has to name one in `locations` as well, while
    # one that names none ("Remote", "Worldwide") still passes.
    locations_apply_to_remote: bool = False
    remote_only: bool = False
    # Places never wanted, remote or not. Whole words, so "India" leaves
    # "Indianapolis" alone. `locations` above only judges on-site postings; this
    # also catches "Remote - India".
    exclude_locations: list[str] = field(default_factory=list)
    exclude_companies: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    # Whole words, checked against the title only. For seniority: "senior" in a
    # description is usually "work with senior engineers", in a title it is the job.
    exclude_title_keywords: list[str] = field(default_factory=list)
    # Whole words, title only, and the title must have at least one: how a student
    # keeps to internships ("intern", "co-op", ...). Empty means any level.
    require_title_keywords: list[str] = field(default_factory=list)
    # Most years of experience a posting may ask for; None means do not look.
    max_experience_years: int | None = None
    # True: a freelance or contract posting skips the title and level rules above, which
    # are written for jobs, and is judged on the rest. False: screened like any posting.
    include_freelance: bool = False
    # Calibrated against real board data: across 544 live Stripe postings a
    # backend profile scored 0 on sales roles, ~7 median, and 35-43 on the
    # genuinely matching engineering roles. 30 sits just under that top band.
    min_fit: float = 30.0
    max_age_days: int = 30


@dataclass
class SourceConfig:
    name: str
    enabled: bool = False
    settings: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def list_of(self, key: str) -> list[str]:
        return _str_list(self.settings.get(key))


@dataclass
class LimitsConfig:
    max_applications_per_day: int = 10
    max_applications_per_run: int = 5
    max_per_company_per_week: int = 2
    max_tailor_per_run: int = 8


@dataclass
class EmailConfig:
    enabled: bool = False
    credentials_file: str = "gmail_credentials.json"
    token_file: str = "gmail_token.json"
    from_name: str = ""
    reply_to: str = ""
    daily_cap: int = 20


@dataclass
class AtsConfig:
    enabled: bool = False
    headless: bool = True
    timeout_seconds: int = 45
    screenshot_dir: str = "output/_ats"


@dataclass
class DispatchConfig:
    channel_order: list[str] = field(default_factory=lambda: ["ats_form", "email"])
    require_clean_grounding: bool = True
    require_verified_records: bool = False
    email: EmailConfig = field(default_factory=EmailConfig)
    ats: AtsConfig = field(default_factory=AtsConfig)


@dataclass
class LlmConfig:
    """Which model writes the documents. Empty means let `llm.py` decide."""

    provider: str = ""          # "gemini" | "anthropic" | "" for auto
    model: str = ""             # "" means the provider's own default
    max_tokens: int = 8000

    def apply_to_env(self) -> None:
        """Publish the provider choice so `llm.resolve_provider` sees it.

        The config file is the least surprising place to set this, but the
        resolver reads the environment so that a one-off override on the command
        line still wins. Setting it here rather than passing it through six call
        signatures keeps the seam narrow.
        """
        import os

        if self.provider and not os.environ.get("JOBSEARCH_LLM_PROVIDER"):
            os.environ["JOBSEARCH_LLM_PROVIDER"] = self.provider


@dataclass
class Config:
    autonomous: bool = False
    search: SearchConfig = field(default_factory=SearchConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    path: Path | None = None

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        config_path = Path(path) if path else PROJECT_ROOT / CONFIG_NAME
        if not config_path.is_file():
            # config.toml is gitignored -- it can carry Adzuna/USAJobs keys, and
            # the tool never rewrites it, so hand-edits survive. A host with no
            # persistent disk (e.g. Render's free tier) never gets a copy from
            # git, so it never gets one at all unless it's handed one this way:
            # JOBSEARCH_CONFIG_TOML holding the file's contents verbatim, same
            # place every other secret on that host lives (env vars, not files).
            inline = os.environ.get("JOBSEARCH_CONFIG_TOML")
            if inline:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(inline, encoding="utf-8")
            else:
                return cls(path=config_path)
        try:
            raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc
        return cls.from_dict(raw, path=config_path)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, path: Path | None = None) -> "Config":
        search = SearchConfig(
            titles=_str_list(_get(raw, "search.titles")),
            keywords=_str_list(_get(raw, "search.keywords")),
            locations=_str_list(_get(raw, "search.locations")),
            locations_apply_to_remote=bool(_get(raw, "search.locations_apply_to_remote", False)),
            remote_only=bool(_get(raw, "search.remote_only", False)),
            exclude_locations=_str_list(_get(raw, "search.exclude_locations")),
            exclude_companies=[c.lower() for c in _str_list(_get(raw, "search.exclude_companies"))],
            exclude_keywords=[k.lower() for k in _str_list(_get(raw, "search.exclude_keywords"))],
            exclude_title_keywords=[
                k.lower() for k in _str_list(_get(raw, "search.exclude_title_keywords"))
            ],
            require_title_keywords=[
                k.lower() for k in _str_list(_get(raw, "search.require_title_keywords"))
            ],
            max_experience_years=(
                None
                if _get(raw, "search.max_experience_years") is None
                else max(0, int(_get(raw, "search.max_experience_years")))
            ),
            include_freelance=bool(_get(raw, "search.include_freelance", False)),
            min_fit=float(_get(raw, "search.min_fit", 45.0)),
            max_age_days=int(_get(raw, "search.max_age_days", 30)),
        )
        limits = LimitsConfig(
            max_applications_per_day=int(_get(raw, "limits.max_applications_per_day", 10)),
            max_applications_per_run=int(_get(raw, "limits.max_applications_per_run", 5)),
            max_per_company_per_week=int(_get(raw, "limits.max_per_company_per_week", 2)),
            max_tailor_per_run=int(_get(raw, "limits.max_tailor_per_run", 8)),
        )
        dispatch = DispatchConfig(
            channel_order=_str_list(_get(raw, "dispatch.channel_order")) or ["ats_form", "email"],
            require_clean_grounding=bool(_get(raw, "dispatch.require_clean_grounding", True)),
            require_verified_records=bool(_get(raw, "dispatch.require_verified_records", False)),
            email=EmailConfig(
                enabled=bool(_get(raw, "dispatch.email.enabled", False)),
                credentials_file=str(_get(raw, "dispatch.email.credentials_file", "gmail_credentials.json")),
                token_file=str(_get(raw, "dispatch.email.token_file", "gmail_token.json")),
                from_name=str(_get(raw, "dispatch.email.from_name", "")),
                reply_to=str(_get(raw, "dispatch.email.reply_to", "")),
                daily_cap=int(_get(raw, "dispatch.email.daily_cap", 20)),
            ),
            ats=AtsConfig(
                enabled=bool(_get(raw, "dispatch.ats.enabled", False)),
                headless=bool(_get(raw, "dispatch.ats.headless", True)),
                timeout_seconds=int(_get(raw, "dispatch.ats.timeout_seconds", 45)),
                screenshot_dir=str(_get(raw, "dispatch.ats.screenshot_dir", "output/_ats")),
            ),
        )
        sources: dict[str, SourceConfig] = {}
        for name, settings in (raw.get("sources") or {}).items():
            if not isinstance(settings, dict):
                continue
            sources[name] = SourceConfig(
                name=name,
                enabled=bool(settings.get("enabled", False)),
                settings={k: v for k, v in settings.items() if k != "enabled"},
            )
        llm_config = LlmConfig(
            provider=str(_get(raw, "llm.provider", "")).strip().lower(),
            model=str(_get(raw, "llm.model", "")).strip(),
            max_tokens=int(_get(raw, "llm.max_tokens", 8000)),
        )
        return cls(
            autonomous=bool(raw.get("autonomous", False)),
            search=search,
            limits=limits,
            dispatch=dispatch,
            llm=llm_config,
            sources=sources,
            path=path,
        )

    # ------------------------------------------------------------------ checks

    def exists(self) -> bool:
        return bool(self.path and self.path.is_file())

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources.values() if s.enabled]

    def problems(self) -> list[str]:
        """Everything that would stop a run from doing useful work."""
        from . import llm

        issues: list[str] = []

        # Checked before the config-file test: without a key, tailoring fails
        # whether or not the rest of the configuration is sound.
        self.llm.apply_to_env()
        try:
            provider = llm.resolve_provider()
        except llm.ModelError as exc:
            issues.append(str(exc))
        else:
            if not llm.api_key_for(provider):
                names = " or ".join(llm.API_KEY_ENV[provider])
                issues.append(
                    f"No {names} set, so nothing can be tailored. "
                    f"{llm.KEY_HELP[provider].splitlines()[0]}"
                )

        if not self.exists():
            issues.append(
                f"No {CONFIG_NAME}. Copy {EXAMPLE_NAME} to {CONFIG_NAME} and edit it."
            )
            return issues

        enabled = self.enabled_sources()
        if not enabled:
            issues.append("No job sources enabled -- nothing to search. See [sources] in the config.")

        for source in enabled:
            if source.name == "greenhouse" and not source.list_of("boards"):
                issues.append("sources.greenhouse is enabled but lists no boards.")
            if source.name == "lever" and not source.list_of("companies"):
                issues.append("sources.lever is enabled but lists no companies.")
            if source.name == "ashby" and not source.list_of("boards"):
                issues.append("sources.ashby is enabled but lists no boards.")
            if source.name == "adzuna" and not (source.get("app_id") and source.get("app_key")):
                issues.append("sources.adzuna needs app_id and app_key (free at developer.adzuna.com).")
            if source.name == "usajobs" and not (source.get("email") and source.get("api_key")):
                issues.append("sources.usajobs needs email and api_key (free at developer.usajobs.gov).")

        if self.autonomous:
            channels = set(self.dispatch.channel_order)
            if "email" in channels and not self.dispatch.email.enabled:
                issues.append("dispatch.channel_order includes email but dispatch.email.enabled is false.")
            if "ats_form" in channels and not self.dispatch.ats.enabled:
                issues.append("dispatch.channel_order includes ats_form but dispatch.ats.enabled is false.")
            if not channels & {"email", "ats_form"}:
                issues.append("autonomous is on but no sending channel is configured.")
        return issues

    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.exists() and not self.autonomous:
            out.append(
                "autonomous = false -- the pipeline will prepare everything and stop before "
                "sending. Set it true in the config when you are ready."
            )
        if self.search.min_fit < 25:
            out.append(
                f"search.min_fit = {self.search.min_fit} is very low; expect weak matches "
                "to reach the tailoring step."
            )
        return out
