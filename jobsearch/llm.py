"""Model access, behind one function.

Gemini is the default because its free tier costs nothing, which matters when a
pipeline run can tailor a dozen applications. The Anthropic path is kept because
it already worked and deleting working code buys nothing; set
`JOBSEARCH_LLM_PROVIDER=anthropic` or `[llm] provider = "anthropic"` to use it.

Everything that talks to a model goes through `call()`. It returns
`(text, usage)` where `usage["stop_reason"]` is normalised across providers, so
callers never branch on which one answered.

Gemini-specific hazards this handles, because each one otherwise produces a
silently empty resume rather than an error:

- A safety filter can block the response. `response.text` is then empty or
  raises, and an empty string would sail downstream as a blank document.
- Thinking models spend `max_output_tokens` on reasoning before writing a word.
  Hit the ceiling mid-thought and you get zero visible output with
  `finish_reason=MAX_TOKENS`.
- `finish_reason` is an enum, not a string, and its names differ from
  Anthropic's stop reasons.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .db import PROJECT_ROOT

GEMINI = "gemini"
ANTHROPIC = "anthropic"
PROVIDERS = (GEMINI, ANTHROPIC)

DEFAULT_PROVIDER = GEMINI
DEFAULT_MAX_TOKENS = 8000

# Flash is the right default for this workload: the free tier's request budget
# is the binding constraint, not model strength, and the generation prompt does
# the heavy lifting by handing over a closed fact set.
# Pinned, not an alias. `gemini-flash-latest` works but moves under you, and a
# resume generator changing behaviour without the config changing is worse than
# an occasional manual bump.
#
# Note gemini-2.5-flash is still returned by models.list() while refusing to
# generate for keys issued after its retirement -- "This model is no longer
# available to new users", HTTP 404. Presence in the listing is not proof a
# model is usable, so verify by calling it.
DEFAULT_MODELS = {
    GEMINI: "gemini-3.5-flash",
    ANTHROPIC: "claude-sonnet-5",
}

API_KEY_ENV = {
    GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ANTHROPIC: ("ANTHROPIC_API_KEY",),
}

# The only keys library code may pull from .env. Everything else in it (TURSO_*,
# JOBSEARCH_PASSWORD, ...) is for a real CLI invocation, which loads the lot in
# main(). Code that merely wants to know the model provider must not be able to
# repoint the process at a database, or arm a password check a test expects unset.
LLM_ENV_KEYS = frozenset({"JOBSEARCH_LLM_PROVIDER", *(n for names in API_KEY_ENV.values() for n in names)})

INSTALL_HINT = {
    GEMINI: "pip install google-genai",
    ANTHROPIC: "pip install anthropic",
}

KEY_HELP = {
    GEMINI: (
        "Get a free key at https://aistudio.google.com/apikey\n"
        "  PowerShell (this session): $env:GEMINI_API_KEY = '...'\n"
        "  Or put GEMINI_API_KEY=... in the .env file next to jobsearch.db"
    ),
    ANTHROPIC: (
        "Get a key at https://console.anthropic.com/settings/keys\n"
        "  PowerShell (this session): $env:ANTHROPIC_API_KEY = 'sk-ant-...'\n"
        "  Or put ANTHROPIC_API_KEY=sk-ant-... in the .env file next to jobsearch.db"
    ),
}

# Gemini finish reasons -> the vocabulary the rest of the codebase already uses.
_STOP_REASONS = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "safety",
    "PROHIBITED_CONTENT": "safety",
    "BLOCKLIST": "safety",
    "SPII": "safety",
    "RECITATION": "recitation",
}


class ModelError(RuntimeError):
    """Anything that stopped us getting usable text back."""


def load_dotenv(path: Path | None = None, *, only: frozenset[str] | None = None) -> None:
    """Minimal .env support so API keys don't have to live in the shell profile.

    The file is optional, so nothing here may raise. The existence check and the
    read are inherently a race, and on a OneDrive-backed path a file can be
    listed but not readable -- either way, "no .env" is a normal state, not an
    error worth propagating to a caller that only wanted to know the provider.

    `only` limits which keys are taken; library code passes LLM_ENV_KEYS.
    """
    env_path = path or (PROJECT_ROOT / ".env")
    try:
        contents = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if only is not None and key not in only:
            continue
        if key and key not in os.environ:
            os.environ[key] = value


def api_key_for(provider: str) -> str | None:
    for name in API_KEY_ENV.get(provider, ()):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return None


def resolve_provider(explicit: str | None = None) -> str:
    """Explicit argument, then env var, then whichever key exists, then Gemini."""
    load_dotenv(only=LLM_ENV_KEYS)
    candidate = (explicit or os.environ.get("JOBSEARCH_LLM_PROVIDER") or "").strip().lower()
    if candidate:
        if candidate not in PROVIDERS:
            raise ModelError(
                f"Unknown model provider {candidate!r}. Choose one of: {', '.join(PROVIDERS)}"
            )
        return candidate
    for provider in PROVIDERS:  # Gemini first
        if api_key_for(provider):
            return provider
    return DEFAULT_PROVIDER


def default_model(provider: str | None = None) -> str:
    return DEFAULT_MODELS[resolve_provider(provider)]


def describe() -> str:
    """One line for `config` output and error messages."""
    provider = resolve_provider()
    state = "key found" if api_key_for(provider) else "NO KEY"
    return f"{provider} ({DEFAULT_MODELS[provider]}) -- {state}"


def _require_key(provider: str) -> str:
    key = api_key_for(provider)
    if not key:
        names = " or ".join(API_KEY_ENV[provider])
        raise ModelError(f"{names} is not set.\n  {KEY_HELP[provider]}")
    return key


# --------------------------------------------------------------------------- gemini


def _call_gemini(
    system_prompt: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float | None,
) -> tuple[str, dict[str, Any]]:
    key = _require_key(GEMINI)
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise ModelError(f"The Gemini SDK is not installed. Run: {INSTALL_HINT[GEMINI]}") from exc

    client = genai.Client(api_key=key)
    config: dict[str, Any] = {
        "system_instruction": system_prompt,
        "max_output_tokens": max_tokens,
    }
    if temperature is not None:
        config["temperature"] = temperature

    try:
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(**config),
        )
    except Exception as exc:
        raise ModelError(f"Gemini call failed: {type(exc).__name__}: {exc}") from exc

    # A blocked *prompt* never produces candidates at all.
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    if block_reason:
        raise ModelError(
            f"Gemini refused the prompt (block_reason={getattr(block_reason, 'name', block_reason)}). "
            "This is a safety filter, not a bug in your profile data. Retry, or switch "
            "provider with JOBSEARCH_LLM_PROVIDER=anthropic."
        )

    candidates = getattr(response, "candidates", None) or []
    raw_finish = getattr(candidates[0], "finish_reason", None) if candidates else None
    finish_name = getattr(raw_finish, "name", str(raw_finish) if raw_finish else "")
    stop_reason = _STOP_REASONS.get(finish_name, "other" if finish_name else "end_turn")

    # `.text` raises on some blocked responses rather than returning empty.
    try:
        text = response.text or ""
    except Exception:
        text = ""

    usage_meta = getattr(response, "usage_metadata", None)
    usage = {
        "input_tokens": getattr(usage_meta, "prompt_token_count", None),
        "output_tokens": getattr(usage_meta, "candidates_token_count", None),
        "thinking_tokens": getattr(usage_meta, "thoughts_token_count", None),
        "stop_reason": stop_reason,
        "provider": GEMINI,
        "model": model,
    }

    if not text.strip():
        if stop_reason == "safety":
            raise ModelError(
                "Gemini blocked the response on safety grounds and returned nothing. "
                "Nothing was written. Retry, or set JOBSEARCH_LLM_PROVIDER=anthropic."
            )
        if stop_reason == "recitation":
            raise ModelError(
                "Gemini stopped for recitation (it judged the output too close to "
                "training data) and returned nothing."
            )
        if stop_reason == "max_tokens":
            thinking = usage.get("thinking_tokens") or 0
            spent = f" It spent {thinking} tokens thinking before writing." if thinking else ""
            raise ModelError(
                f"Gemini hit the {max_tokens}-token ceiling before producing any text.{spent} "
                "Raise --max-tokens, or use a non-thinking model such as gemini-2.0-flash."
            )
        raise ModelError(
            f"Gemini returned no text (finish_reason={finish_name or 'unknown'})."
        )
    return text, usage


# --------------------------------------------------------------------------- anthropic


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float | None,
) -> tuple[str, dict[str, Any]]:
    _require_key(ANTHROPIC)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - environment problem, not logic
        raise ModelError(f"The anthropic SDK is not installed. Run: {INSTALL_HINT[ANTHROPIC]}") from exc

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        response = client.messages.create(**kwargs)
    except Exception as exc:  # SDK raises a family of API errors; surface them plainly
        raise ModelError(f"Anthropic API call failed: {type(exc).__name__}: {exc}") from exc

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    )
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", None),
        "output_tokens": getattr(response.usage, "output_tokens", None),
        "stop_reason": getattr(response, "stop_reason", None),
        "provider": ANTHROPIC,
        "model": model,
    }
    if not text.strip():
        raise ModelError(
            f"Claude returned no text (stop_reason={usage['stop_reason']})."
        )
    return text, usage


# --------------------------------------------------------------------------- entry point


def call(
    system_prompt: str,
    user_message: str,
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    provider: str | None = None,
    temperature: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Send one prompt. Returns (text, usage). Raises ModelError on any failure.

    Never returns empty text -- an empty document is worse than an exception,
    because it looks like a successful run.
    """
    load_dotenv(only=LLM_ENV_KEYS)
    chosen = resolve_provider(provider)
    chosen_model = model or DEFAULT_MODELS[chosen]
    caller = _call_gemini if chosen == GEMINI else _call_anthropic
    return caller(
        system_prompt,
        user_message,
        model=chosen_model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
