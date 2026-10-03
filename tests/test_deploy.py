"""HTTP-layer behaviour that only matters once the app sits behind proxies and a
desktop client: the Host allow-list, the session cookie behind TLS termination,
and /api/ingest. test_web.py drives `App` directly; these go through a real
socket because headers are the whole point.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch import db  # noqa: E402
from jobsearch.config import Config  # noqa: E402
from jobsearch.web.server import INGEST_MAX_ITEMS, App, _handler_class  # noqa: E402

TOKEN = "ingest-token-for-tests"


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in ("JOBSEARCH_HOST", "JOBSEARCH_API_TOKEN", "JOBSEARCH_PASSWORD"):
            os.environ.pop(name, None)

        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "test.db"
        db.connect(self.db_path).close()

        self.app = App(self.db_path, Config(), "csrf-token")
        self.app.password = "hunter2"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_class(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def rows(self, sql: str) -> list:
        conn = db.connect(self.db_path)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class HostAllowListTests(ServerCase):
    def test_loopback_is_allowed_without_configuration(self) -> None:
        response, _ = self.request("GET", "/login")
        self.assertEqual(response.status, 200)

    def test_a_foreign_host_is_refused_without_configuration(self) -> None:
        response, _ = self.request("GET", "/login", headers={"Host": "front.example"})
        self.assertEqual(response.status, 403)

    def test_every_configured_host_is_allowed_and_nothing_else(self) -> None:
        os.environ["JOBSEARCH_HOST"] = "origin.example, Front.Example"
        for host, expected in (("origin.example", 200), ("front.example", 200), ("evil.example", 403)):
            with self.subTest(host=host):
                response, _ = self.request("GET", "/login", headers={"Host": host})
                self.assertEqual(response.status, expected)


class SessionCookieTests(ServerCase):
    def login(self, extra: dict | None = None):
        headers = {"Content-Type": "application/x-www-form-urlencoded", **(extra or {})}
        response, _ = self.request("POST", "/login", body=b"password=hunter2", headers=headers)
        self.assertEqual(response.status, 303)
        return response.getheader("Set-Cookie") or ""

    def test_cookie_is_secure_when_a_proxy_says_the_browser_used_https(self) -> None:
        self.assertIn("Secure", self.login({"X-Forwarded-Proto": "https"}))

    def test_cookie_is_not_secure_on_plain_loopback(self) -> None:
        cookie = self.login()
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)


class IngestTests(ServerCase):
    AUTH = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

    def post(self, payload, headers: dict | None = None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        response, data = self.request(
            "POST", "/api/ingest", body=body, headers=self.AUTH if headers is None else headers
        )
        return response.status, json.loads(data)

    def setUp(self) -> None:
        super().setUp()
        os.environ["JOBSEARCH_API_TOKEN"] = TOKEN

    def test_disabled_without_a_token_configured(self) -> None:
        del os.environ["JOBSEARCH_API_TOKEN"]
        status, _ = self.post({"items": []})
        self.assertEqual(status, 404)

    def test_wrong_or_missing_token_is_refused(self) -> None:
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}):
            with self.subTest(headers=headers):
                status, _ = self.post({"items": [{"kind": "job"}]}, headers=headers)
                self.assertEqual(status, 401)

    def test_stores_jobs_and_competitions_and_dedupes_on_repeat(self) -> None:
        payload = {
            "source": "linkedin",
            "items": [
                {"kind": "job", "title": "Data Intern", "company": "Acme", "location": "Remote",
                 "url": "https://example.com/jobs/1", "description": "Python and SQL"},
                {"kind": "competition", "title": "Spring Hack", "category": "hackathon",
                 "url": "https://example.com/hack", "deadline": "2026-11-01", "tracks": ["AI", "Fintech"]},
            ],
        }
        status, result = self.post(payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["jobs"], {"new": 1, "duplicate": 0})
        self.assertEqual(result["competitions"], {"new": 1, "duplicate": 0})

        job = self.rows("SELECT source, company, title, remote FROM jobs")[0]
        self.assertEqual((job["source"], job["company"], job["title"], job["remote"]), ("app:linkedin", "Acme", "Data Intern", 1))
        comp = self.rows("SELECT name, tracks, discovery_source, status FROM competitions")[0]
        self.assertEqual((comp["tracks"], comp["discovery_source"], comp["status"]), ("AI, Fintech", "app:linkedin", "discovered"))

        _, again = self.post(payload)
        self.assertEqual(again["jobs"], {"new": 0, "duplicate": 1})
        self.assertEqual(again["competitions"], {"new": 0, "duplicate": 1})
        self.assertEqual(len(self.rows("SELECT id FROM jobs")), 1)

    def test_non_web_urls_are_dropped_and_malformed_items_counted(self) -> None:
        status, result = self.post({"items": [
            {"kind": "job", "title": "Intern", "company": "Acme", "url": "javascript:alert(1)"},
            {"kind": "job", "title": "No company"},
            {"kind": "nonsense", "title": "x"},
            "not an object",
        ]})
        self.assertEqual(status, 200)
        self.assertEqual(result["rejected"], 3)
        self.assertEqual(self.rows("SELECT url FROM jobs")[0]["url"], "")

    def test_oversized_and_malformed_bodies_are_refused(self) -> None:
        status, _ = self.post({"items": [{"kind": "job", "title": "t", "company": "c"}] * (INGEST_MAX_ITEMS + 1)})
        self.assertEqual(status, 413)
        status, _ = self.post(b"{not json")
        self.assertEqual(status, 400)
        status, _ = self.post({"items": []})
        self.assertEqual(status, 400)

    def test_new_jobs_are_scored_once_there_is_a_profile(self) -> None:
        conn = db.connect(self.db_path)
        org = db.upsert_organization(conn, "Northwind", kind="company")
        experience = db.insert_row(conn, "experiences", {
            "organization_id": org, "title": "Engineer", "start_date": "2021-01",
            "is_current": 1, "verified": 1,
        })
        achievement = db.insert_row(conn, "achievements", {
            "experience_id": experience, "title": "Built ETL", "description": "Python ETL on PostgreSQL",
            "verified": 1,
        })
        db.link_skills_to(conn, ["Python"], "achievement", achievement, verified=1)
        conn.commit()
        conn.close()

        self.post({"items": [{"kind": "job", "title": "Python Developer", "company": "Acme",
                              "description": "We use Python daily."}]})
        job = self.rows("SELECT status, fit_score FROM jobs")[0]
        self.assertEqual(job["status"], "scored")
        self.assertIsNotNone(job["fit_score"])


if __name__ == "__main__":
    unittest.main()
