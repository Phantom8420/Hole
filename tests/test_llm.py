"""Model-provider tests.

No network. The Gemini SDK is replaced with a fake so the failure modes that
matter -- safety blocks, thinking eating the token budget, blocked prompts --
can be exercised deterministically. Each of those otherwise yields empty text,
and empty text that reaches the caller looks exactly like a successful run.
"""

from __future__ import annotations

import sys
import types as pytypes
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import config as config_module  # noqa: E402
from jobsearch import generate, llm  # noqa: E402


class FakeResponse:
    """Mimics the parts of GenerateContentResponse that `_call_gemini` touches."""

    def __init__(
        self,
        text: str = "hello",
        *,
        finish: str | None = "STOP",
        block_reason: str | None = None,
        text_raises: bool = False,
        prompt_tokens: int = 11,
        output_tokens: int = 22,
        thinking_tokens: int | None = None,
    ) -> None:
        self._text = text
        self._text_raises = text_raises
        self.prompt_feedback = SimpleNamespace(block_reason=block_reason)
        self.candidates = (
            [SimpleNamespace(finish_reason=SimpleNamespace(name=finish))] if finish else []
        )
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thinking_tokens,
        )

    @property
    def text(self) -> str:
        if self._text_raises:
            raise ValueError("no valid Part")
        return self._text


@contextmanager
def fake_gemini(response=None, *, raises: Exception | None = None, sequence: list | None = None):
    """Install a stand-in `google.genai` for the duration of the block. `sequence` is
    what successive calls do: an exception to raise or a response to return."""
    captured: dict[str, object] = {"calls": 0}

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            captured["calls"] = int(captured["calls"]) + 1
            if sequence is not None:
                item = sequence.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            if raises is not None:
                raise raises
            return response if response is not None else FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.models = FakeModels()

    fake_types = pytypes.ModuleType("google.genai.types")
    fake_types.GenerateContentConfig = lambda **kwargs: SimpleNamespace(**kwargs)

    fake_genai = pytypes.ModuleType("google.genai")
    fake_genai.Client = FakeClient
    fake_genai.types = fake_types

    fake_google = pytypes.ModuleType("google")
    fake_google.genai = fake_genai

    saved = {name: sys.modules.get(name) for name in ("google", "google.genai", "google.genai.types")}
    sys.modules["google"] = fake_google
    sys.modules["google.genai"] = fake_genai
    sys.modules["google.genai.types"] = fake_types
    try:
        yield captured
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class ProviderResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        # A real .env would otherwise leak keys into these assertions.
        patcher = mock.patch.object(llm, "load_dotenv", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_to_gemini_with_no_keys(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(llm.resolve_provider(), "gemini")
            self.assertEqual(llm.default_model(), llm.DEFAULT_MODELS[llm.GEMINI])

    def test_explicit_argument_wins(self) -> None:
        with mock.patch.dict("os.environ", {"JOBSEARCH_LLM_PROVIDER": "gemini"}, clear=True):
            self.assertEqual(llm.resolve_provider("anthropic"), "anthropic")

    def test_env_var_beats_key_presence(self) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-x", "JOBSEARCH_LLM_PROVIDER": "gemini"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(llm.resolve_provider(), "gemini")

    def test_falls_back_to_whichever_key_exists(self) -> None:
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}, clear=True):
            self.assertEqual(llm.resolve_provider(), "anthropic")
            self.assertEqual(llm.default_model(), "claude-sonnet-5")

    def test_gemini_wins_when_both_keys_are_present(self) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-x", "GEMINI_API_KEY": "g-x"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(llm.resolve_provider(), "gemini")

    def test_google_api_key_is_accepted_too(self) -> None:
        with mock.patch.dict("os.environ", {"GOOGLE_API_KEY": "g-x"}, clear=True):
            self.assertEqual(llm.api_key_for("gemini"), "g-x")

    def test_unknown_provider_is_rejected(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(llm.ModelError):
                llm.resolve_provider("llama")

    def test_describe_reports_missing_key(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIn("NO KEY", llm.describe())
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "g-x"}, clear=True):
            self.assertIn("key found", llm.describe())


class DotenvTests(unittest.TestCase):
    def test_values_are_loaded_without_clobbering_the_environment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "# a comment\n\nGEMINI_API_KEY='from-file'\nALREADY_SET=file-value\n",
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"ALREADY_SET": "shell-value"}, clear=True):
                import os

                llm.load_dotenv(env_file)
                self.assertEqual(os.environ["GEMINI_API_KEY"], "from-file")
                self.assertEqual(os.environ["ALREADY_SET"], "shell-value")  # shell wins

    def test_a_missing_file_is_not_an_error(self) -> None:
        llm.load_dotenv(Path("no-such-directory") / ".env")  # must not raise

    def test_an_unreadable_file_is_not_an_error(self) -> None:
        # `is_file()` can report True for something `read_text()` cannot open --
        # a stale cloud placeholder, a race, or a test that patches Path.
        with mock.patch.object(Path, "is_file", lambda self: True):
            llm.load_dotenv(Path("definitely") / "not" / "here" / ".env")


class GeminiCallTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(llm, "load_dotenv", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {"GEMINI_API_KEY": "g-key"}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_happy_path_returns_text_and_normalised_usage(self) -> None:
        with fake_gemini(FakeResponse("the resume")) as captured:
            text, usage = llm.call("SYSTEM", "USER", max_tokens=1234)
        self.assertEqual(text, "the resume")
        self.assertEqual(usage["stop_reason"], "end_turn")
        self.assertEqual(usage["provider"], "gemini")
        self.assertEqual(usage["model"], llm.DEFAULT_MODELS[llm.GEMINI])
        self.assertEqual(usage["input_tokens"], 11)
        self.assertEqual(usage["output_tokens"], 22)
        self.assertEqual(captured["api_key"], "g-key")
        self.assertEqual(captured["contents"], "USER")
        self.assertEqual(captured["config"].system_instruction, "SYSTEM")
        self.assertEqual(captured["config"].max_output_tokens, 1234)

    def test_model_override_is_passed_through(self) -> None:
        with fake_gemini(FakeResponse()) as captured:
            llm.call("S", "U", model="gemini-2.0-flash")
        self.assertEqual(captured["model"], "gemini-2.0-flash")

    def test_max_tokens_is_normalised_to_the_shared_vocabulary(self) -> None:
        with fake_gemini(FakeResponse("partial", finish="MAX_TOKENS")):
            _text, usage = llm.call("S", "U")
        self.assertEqual(usage["stop_reason"], "max_tokens")

    def test_safety_block_raises_instead_of_returning_empty(self) -> None:
        with fake_gemini(FakeResponse("", finish="SAFETY")):
            with self.assertRaises(llm.ModelError) as caught:
                llm.call("S", "U")
        self.assertIn("safety", str(caught.exception).lower())

    def test_prohibited_content_counts_as_safety(self) -> None:
        with fake_gemini(FakeResponse("", finish="PROHIBITED_CONTENT")):
            with self.assertRaises(llm.ModelError):
                llm.call("S", "U")

    def test_blocked_prompt_is_reported_clearly(self) -> None:
        blocked = FakeResponse("", finish=None, block_reason=SimpleNamespace(name="SAFETY"))
        with fake_gemini(blocked):
            with self.assertRaises(llm.ModelError) as caught:
                llm.call("S", "U")
        self.assertIn("refused the prompt", str(caught.exception))

    def test_thinking_exhausting_the_budget_explains_itself(self) -> None:
        starved = FakeResponse("", finish="MAX_TOKENS", thinking_tokens=4000)
        with fake_gemini(starved):
            with self.assertRaises(llm.ModelError) as caught:
                llm.call("S", "U", max_tokens=4000)
        message = str(caught.exception)
        self.assertIn("4000 tokens thinking", message)
        self.assertIn("gemini-2.0-flash", message)  # points at the fix

    def test_text_property_raising_is_survived(self) -> None:
        with fake_gemini(FakeResponse(text_raises=True, finish="SAFETY")):
            with self.assertRaises(llm.ModelError):
                llm.call("S", "U")

    def test_recitation_is_named(self) -> None:
        with fake_gemini(FakeResponse("", finish="RECITATION")):
            with self.assertRaises(llm.ModelError) as caught:
                llm.call("S", "U")
        self.assertIn("recitation", str(caught.exception).lower())

    def test_sdk_exception_is_wrapped(self) -> None:
        with fake_gemini(raises=ValueError("API key not valid")):
            with mock.patch.object(llm.time, "sleep") as sleep:
                with self.assertRaises(llm.ModelError) as caught:
                    llm.call("S", "U")
        self.assertIn("API key not valid", str(caught.exception))
        self.assertFalse(caught.exception.transient)  # a bad key is not worth trying again
        sleep.assert_not_called()

    def test_a_rate_limit_is_waited_out_and_the_call_goes_through(self) -> None:
        class RateLimited(Exception):
            code = 429

        with fake_gemini(sequence=[RateLimited("slow down"), RuntimeError("503 UNAVAILABLE"), FakeResponse("ok")]) as seen:
            with mock.patch.object(llm.time, "sleep") as sleep:
                text, _ = llm.call("S", "U")
        self.assertEqual(text, "ok")
        self.assertEqual(seen["calls"], 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [20, 60])

    def test_a_quota_that_never_clears_ends_as_a_transient_error(self) -> None:
        errors = [RuntimeError("429 RESOURCE_EXHAUSTED")] * 4
        with fake_gemini(sequence=list(errors)) as seen:
            with mock.patch.object(llm.time, "sleep") as sleep:
                with self.assertRaises(llm.ModelError) as caught:
                    llm.call("S", "U")
        self.assertEqual(seen["calls"], 4)  # the first try and three more
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(caught.exception.transient)

    def test_generate_passes_the_transient_flag_on(self) -> None:
        with mock.patch.object(generate.llm, "call", side_effect=llm.ModelError("429", transient=True)):
            with self.assertRaises(generate.GenerationError) as caught:
                generate.call_model("S", "U")
        self.assertTrue(caught.exception.transient)
        with mock.patch.object(generate.llm, "call", side_effect=llm.ModelError("refused")):
            with self.assertRaises(generate.GenerationError) as caught:
                generate.call_model("S", "U")
        self.assertFalse(caught.exception.transient)

    def test_missing_key_names_both_accepted_variables(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(llm.ModelError) as caught:
                llm.call("S", "U", provider="gemini")
        message = str(caught.exception)
        self.assertIn("GEMINI_API_KEY", message)
        self.assertIn("aistudio.google.com", message)


class CallModelBridgeTests(unittest.TestCase):
    """`generate.call_model` is what the rest of the codebase actually calls."""

    def setUp(self) -> None:
        patcher = mock.patch.object(llm, "load_dotenv", lambda *a, **k: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_model_errors_surface_as_generation_errors(self) -> None:
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "g"}, clear=True):
            with fake_gemini(FakeResponse("", finish="SAFETY")):
                with self.assertRaises(generate.GenerationError):
                    generate.call_model("S", "U")

    def test_default_model_is_none_so_the_provider_chooses(self) -> None:
        self.assertIsNone(generate.DEFAULT_MODEL)

    def test_call_model_reaches_gemini_by_default(self) -> None:
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "g"}, clear=True):
            with fake_gemini(FakeResponse("drafted")) as captured:
                text, usage = generate.call_model("S", "U")
        self.assertEqual(text, "drafted")
        self.assertEqual(usage["provider"], "gemini")
        self.assertEqual(captured["model"], llm.DEFAULT_MODELS[llm.GEMINI])


class LlmConfigTests(unittest.TestCase):
    def test_provider_is_read_from_the_config_file(self) -> None:
        config = config_module.Config.from_dict(
            {"llm": {"provider": "Anthropic", "model": "claude-sonnet-5", "max_tokens": 5000}}
        )
        self.assertEqual(config.llm.provider, "anthropic")  # normalised
        self.assertEqual(config.llm.max_tokens, 5000)

    def test_defaults_when_the_section_is_absent(self) -> None:
        config = config_module.Config.from_dict({})
        self.assertEqual(config.llm.provider, "")
        self.assertEqual(config.llm.max_tokens, 8000)

    def test_apply_to_env_publishes_the_choice(self) -> None:
        config = config_module.Config.from_dict({"llm": {"provider": "anthropic"}})
        with mock.patch.dict("os.environ", {}, clear=True):
            config.llm.apply_to_env()
            import os

            self.assertEqual(os.environ["JOBSEARCH_LLM_PROVIDER"], "anthropic")

    def test_apply_to_env_does_not_override_an_explicit_env_var(self) -> None:
        config = config_module.Config.from_dict({"llm": {"provider": "anthropic"}})
        with mock.patch.dict("os.environ", {"JOBSEARCH_LLM_PROVIDER": "gemini"}, clear=True):
            config.llm.apply_to_env()
            import os

            self.assertEqual(os.environ["JOBSEARCH_LLM_PROVIDER"], "gemini")

    def test_missing_key_is_reported_as_a_config_problem(self) -> None:
        config = config_module.Config.from_dict({"llm": {"provider": "gemini"}})
        with mock.patch.object(llm, "load_dotenv", lambda *a, **k: None):
            with mock.patch.dict("os.environ", {}, clear=True):
                problems = config.problems()
        self.assertTrue(any("GEMINI_API_KEY" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
