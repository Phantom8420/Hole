"""Logins that survive a restart of the web server (jobsearch/web/sessions.py).

test_deploy.py drives the same thing through a real socket; these are the store on its own.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch.web.sessions import TTL_SECONDS, SessionStore  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "output" / "web-sessions.json"
        self.clock = Clock()

    def store(self, path: Path | None = None) -> SessionStore:
        """A store on the file, as a server starting up would make one."""
        return SessionStore(self.path if path is None else path, clock=self.clock)

    def test_a_login_is_valid_until_it_ends(self) -> None:
        store = self.store()
        token = store.create("pw")
        self.assertTrue(store.valid("pw", token))
        self.assertFalse(store.valid("pw", token + "x"))
        self.assertFalse(store.valid("pw", ""))
        self.clock.now += TTL_SECONDS - 1
        self.assertTrue(store.valid("pw", token))
        self.clock.now += 2
        self.assertFalse(store.valid("pw", token))

    def test_a_restart_keeps_the_login(self) -> None:
        token = self.store().create("pw")
        self.assertTrue(self.store().valid("pw", token))

    def test_logging_out_survives_a_restart_too(self) -> None:
        first = self.store()
        gone = first.create("pw")
        kept = first.create("pw")
        first.destroy("pw", gone)
        restarted = self.store()
        self.assertFalse(restarted.valid("pw", gone))
        self.assertTrue(restarted.valid("pw", kept))

    def test_changing_the_password_ends_every_login(self) -> None:
        token = self.store().create("old")
        self.assertFalse(self.store().valid("new", token))
        self.assertTrue(self.store().valid("old", token))

    def test_the_file_holds_neither_the_token_nor_the_password(self) -> None:
        token = self.store().create("a-long-password-nobody-guesses")
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn(token, text)
        self.assertNotIn("a-long-password-nobody-guesses", text)
        self.assertEqual(json.loads(text)["version"], 1)

    def test_a_login_that_ended_while_the_server_was_down_is_dropped(self) -> None:
        token = self.store().create("pw")
        self.clock.now += TTL_SECONDS + 1
        restarted = self.store()
        self.assertFalse(restarted.valid("pw", token))
        restarted.create("pw")  # the next write leaves out what ended
        self.assertEqual(len(json.loads(self.path.read_text(encoding="utf-8"))["logins"]), 1)

    def test_a_file_that_cannot_be_read_means_signing_in_again_not_a_crash(self) -> None:
        self.path.parent.mkdir(parents=True)
        for junk in ("not json", "[]", '{"logins": 5}', '{"logins": {"k": "soon"}}'):
            with self.subTest(junk=junk):
                self.path.write_text(junk, encoding="utf-8")
                err = io.StringIO()
                with redirect_stderr(err):
                    store = self.store()
                self.assertIn("could not read", err.getvalue())
                self.assertFalse(store.valid("pw", "anything"))
                self.assertTrue(store.valid("pw", store.create("pw")))  # and it works from here

    def test_a_missing_file_is_normal(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.store()
        self.assertEqual(err.getvalue(), "")

    def test_a_file_that_cannot_be_written_leaves_logins_in_memory_and_says_so_once(self) -> None:
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("a file where the folder should be", encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            store = self.store(blocker / "web-sessions.json")
            token = store.create("pw")
            store.create("pw")
        self.assertTrue(store.valid("pw", token))
        self.assertEqual(err.getvalue().count("web sessions:"), 1)

    def test_no_path_means_memory_only(self) -> None:
        store = SessionStore(None, clock=self.clock)
        token = store.create("pw")
        self.assertTrue(store.valid("pw", token))
        self.assertFalse(self.path.exists())

    def test_nothing_is_left_half_written(self) -> None:
        self.store().create("pw")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["web-sessions.json"])


if __name__ == "__main__":
    unittest.main()
