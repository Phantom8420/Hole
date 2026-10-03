"""Tests must never reach the production database.

The deployed app keeps its data in Turso, selected by TURSO_DATABASE_URL, and the
CLI reads that from .env. A test run that inherited it wrote its fixtures into
that database: a fake profile over the real one, fake applications, jobs
re-scored against the fake profile. These pin the three rules that stop it.
"""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import cli, db, llm  # noqa: E402
from jobsearch.config import Config  # noqa: E402

BOGUS_TURSO = "https://no-such-database.invalid"


class ExplicitPathBeatsTursoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "named.db"

    def test_a_named_file_is_used_even_when_turso_is_configured(self) -> None:
        with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": BOGUS_TURSO, "TURSO_AUTH_TOKEN": "x"}):
            with mock.patch("jobsearch.libsql_shim.connect", side_effect=AssertionError("reached Turso")):
                conn = db.connect(self.path)
        self.assertIsInstance(conn, sqlite3.Connection)
        conn.close()
        self.assertTrue(self.path.exists())

    def test_an_unqualified_call_still_goes_to_turso_when_configured(self) -> None:
        # The deployed app relies on this: no path, so Turso applies.
        fake = sqlite3.connect(":memory:")
        fake.row_factory = sqlite3.Row
        with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": BOGUS_TURSO, "TURSO_AUTH_TOKEN": "x"}):
            with mock.patch("jobsearch.libsql_shim.connect", return_value=fake) as shim:
                db.connect()
        shim.assert_called_once_with(BOGUS_TURSO, "x")


class DotenvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cli.db"

    def run_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(argv)

    def test_main_with_its_own_argv_does_not_read_dotenv(self) -> None:
        with mock.patch("jobsearch.llm.load_dotenv") as loader:
            self.run_main(["init", "--db", str(self.path)])
        loader.assert_not_called()

    def test_a_real_invocation_reads_dotenv(self) -> None:
        with mock.patch("jobsearch.llm.load_dotenv") as loader, \
                mock.patch.object(sys, "argv", ["jobsearch", "init", "--db", str(self.path)]):
            self.run_main(None)
        loader.assert_called_once()

    def test_the_cli_with_db_never_touches_turso(self) -> None:
        with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": BOGUS_TURSO}):
            with mock.patch("jobsearch.libsql_shim.connect", side_effect=AssertionError("reached Turso")):
                code = self.run_main(["init", "--db", str(self.path)])
        self.assertEqual(code, 0)
        self.assertTrue(self.path.exists())


class DotenvScopeTests(unittest.TestCase):
    """Library code that only wants an API key must not import the rest of .env:
    Config.problems() -> resolve_provider() used to take TURSO_* and the web
    password along with it, which re-opened the leak and made a test that expects
    "no password" start a real public server."""

    def test_only_limits_the_keys_taken(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "GEMINI_API_KEY=k\nTURSO_DATABASE_URL=https://x.invalid\nJOBSEARCH_PASSWORD=pw\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                llm.load_dotenv(env_file, only=llm.LLM_ENV_KEYS)
                self.assertEqual(os.environ.get("GEMINI_API_KEY"), "k")
                self.assertNotIn("TURSO_DATABASE_URL", os.environ)
                self.assertNotIn("JOBSEARCH_PASSWORD", os.environ)
                llm.load_dotenv(env_file)  # a real invocation still takes everything
                self.assertEqual(os.environ.get("TURSO_DATABASE_URL"), "https://x.invalid")

    def test_provider_lookup_uses_the_narrow_load(self) -> None:
        with mock.patch.object(llm, "load_dotenv") as loader, mock.patch.dict(os.environ, {}, clear=True):
            llm.resolve_provider()
        loader.assert_called_once_with(only=llm.LLM_ENV_KEYS)

    def test_config_problems_cannot_arm_the_environment(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text("TURSO_DATABASE_URL=https://x.invalid\nJOBSEARCH_PASSWORD=pw\n", encoding="utf-8")
            real = llm.load_dotenv
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch.object(llm, "load_dotenv", lambda path=None, **kw: real(env_file, **kw)):
                Config().problems()
                self.assertNotIn("TURSO_DATABASE_URL", os.environ)
                self.assertNotIn("JOBSEARCH_PASSWORD", os.environ)


class WebServesTursoByDefaultTests(unittest.TestCase):
    """`web` resolves a default path for its existence check, but must pass None on
    to the server unless --db was given, or Turso would silently stop applying."""

    def serve_args(self, argv):
        captured = {}

        def fake_serve(**kwargs):
            captured.update(kwargs)

        with mock.patch("jobsearch.web.serve", fake_serve), \
                mock.patch.object(cli, "_load_config", return_value=object()), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main(argv)
        return captured

    def test_no_db_flag_with_turso_configured_passes_no_path(self) -> None:
        with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": BOGUS_TURSO}):
            seen = self.serve_args(["web", "--no-browser"])
        self.assertIsNone(seen["db_path"])

    def test_an_explicit_db_flag_passes_that_path(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "x.db"
            db.connect(path).close()
            with mock.patch.dict(os.environ, {"TURSO_DATABASE_URL": BOGUS_TURSO}):
                seen = self.serve_args(["web", "--no-browser", "--db", str(path)])
        self.assertEqual(Path(seen["db_path"]), path.resolve())


if __name__ == "__main__":
    unittest.main()
