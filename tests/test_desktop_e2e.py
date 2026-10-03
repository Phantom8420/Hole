"""The desktop shell talking to the real server.

Starts the web app on a throwaway database, runs the Electron self-test against
it (an embedded page is driven, the extractors run over fixture pages, the
result is posted to /api/ingest), then checks what landed in the database.

Opens an off-screen Electron window, so it is opt-in:
    HOLE_DESKTOP_E2E=1 python -m unittest tests.test_desktop_e2e -v
Needs `npm install` in desktop/ first.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobsearch import db  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.web.server import App, _handler_class  # noqa: E402

DESKTOP = ROOT / "desktop"
ELECTRON = DESKTOP / "node_modules" / "electron" / "dist" / ("electron.exe" if os.name == "nt" else "electron")
TOKEN = "e2e-token-not-a-real-secret"


@unittest.skipUnless(os.environ.get("HOLE_DESKTOP_E2E") == "1", "set HOLE_DESKTOP_E2E=1 to run (opens an off-screen window)")
@unittest.skipUnless(ELECTRON.exists(), "run `npm install` in desktop/ first")
class DesktopToServerTests(unittest.TestCase):
    def test_captured_items_reach_the_database(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                mock.patch.dict(os.environ, {"JOBSEARCH_API_TOKEN": TOKEN}):
            db_path = Path(tmp) / "e2e.db"
            db.connect(db_path).close()
            app = App(db_path, Config(), "csrf")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(app))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)

            env = {
                **os.environ,
                "HOLE_E2E_URL": f"http://127.0.0.1:{httpd.server_address[1]}",
                "HOLE_E2E_TOKEN": TOKEN,
            }
            run = subprocess.run(
                [str(ELECTRON), ".", "--self-test"],
                cwd=DESKTOP, env=env, capture_output=True, text=True, timeout=180,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

            conn = db.connect(db_path)
            try:
                jobs = conn.execute("SELECT source, company, title FROM jobs ORDER BY title").fetchall()
                comps = conn.execute("SELECT name, discovery_source, status FROM competitions ORDER BY name").fetchall()
            finally:
                conn.close()
            self.assertEqual(len(jobs), 4)
            self.assertEqual({j["source"] for j in jobs}, {"app:selftest"})
            self.assertIn(("Acme Analytics", "Data Intern"), {(j["company"], j["title"]) for j in jobs})
            self.assertEqual(len(comps), 3)
            self.assertEqual({c["status"] for c in comps}, {"discovered"})
            self.assertIn("Spring Datathon", {c["name"] for c in comps})


if __name__ == "__main__":
    unittest.main()
