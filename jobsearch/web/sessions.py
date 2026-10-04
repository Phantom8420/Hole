"""Logins that outlast a restart of the web server.

A login is a random token in the browser's cookie. The server keeps only HMAC(password, token) for
each live login, in memory and in a small file, so:

- restarting the server (a deploy, a reboot) does not sign anyone out;
- a copy of the file cannot be turned back into cookies, and tells nothing about the password;
- changing the password ends every login, because no stored value matches under the new one.

With no path it is memory only, which is what the tests and a throwaway server want.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days, the same as the cookie's Max-Age


class SessionStore:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        ttl: float = TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path) if path else None
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._live: dict[str, float] = {}  # HMAC(password, token) -> when the login ends
        self._warned = False
        self._load()

    @staticmethod
    def _key(password: str, token: str) -> str:
        return hmac.new(password.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()

    def create(self, password: str) -> str:
        """A fresh token for a login that has just been checked."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._live = {key: end for key, end in self._live.items() if end >= now}
            self._live[self._key(password, token)] = now + self.ttl
            self._save()
        return token

    def valid(self, password: str, token: str) -> bool:
        if not token:
            return False
        key = self._key(password, token)
        with self._lock:
            end = self._live.get(key)
            if end is None:
                return False
            if end < self._clock():
                del self._live[key]  # the file catches up with the next change
                return False
            return True

    def destroy(self, password: str, token: str) -> None:
        """Log out. A restart must not bring the login back, so the file is written too."""
        if not token:
            return
        with self._lock:
            if self._live.pop(self._key(password, token), None) is not None:
                self._save()

    # ------------------------------------------------------------------ the file

    def _warn(self, message: str) -> None:
        if not self._warned:
            self._warned = True
            print(f"web sessions: {message}", file=sys.stderr)

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            logins = json.loads(self.path.read_text(encoding="utf-8"))["logins"]
            now = self._clock()
            self._live = {key: float(end) for key, end in logins.items() if isinstance(key, str) and float(end) >= now}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            # Nothing here is worth stopping the server for: everyone signs in again.
            self._live = {}
            self._warn(f"could not read {self.path} ({err}); starting with no logins")

    def _save(self) -> None:
        """Write the file whole and swap it in, so a crash never leaves half of one. Called with the lock held."""
        if self.path is None:
            return
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp makes the file readable by its owner alone
            handle, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f"{self.path.name}.", suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump({"version": 1, "logins": self._live}, out)
            os.replace(tmp, self.path)
            tmp = None
        except OSError as err:
            # Logins still work, in memory; they just will not survive a restart.
            self._warn(f"could not save {self.path} ({err}); logins will not survive a restart")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
