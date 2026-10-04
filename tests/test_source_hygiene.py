"""Source files carry no stray control characters.

A backspace once got into ats_form.py where a JavaScript regex meant `\\bcv\\b`: the
file was written through something that turned the escape into the character, the
pattern matched nothing, and nothing looked wrong. Python accepts such a character
inside a string, so only a scan finds it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = (
    "jobsearch", "tests",
    "desktop/src", "desktop/shell", "desktop/test", "desktop/tools",
    "android/tools", "android/app/src",
)
SUFFIXES = {".py", ".js", ".html", ".css", ".toml", ".json", ".kt", ".kts", ".xml"}
ALLOWED = {"\t", "\n", "\r", "\f"}


class SourceHygieneTests(unittest.TestCase):
    def test_no_stray_control_characters(self) -> None:
        offenders = []
        for folder in FOLDERS:
            for path in (ROOT / folder).rglob("*"):
                if not path.is_file() or path.suffix not in SUFFIXES or "node_modules" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                bad = sorted({hex(ord(c)) for c in text if ord(c) < 32 and c not in ALLOWED})
                if bad:
                    offenders.append(f"{path.relative_to(ROOT)}: {', '.join(bad)}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
