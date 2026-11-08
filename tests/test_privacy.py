"""Privacy guard: the repository is public, so tracked text must not leak personal data."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Kept as fragments so this file never matches itself.
PATTERNS = [
    ("windows-user-path", re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+")),
    ("posix-home-path", re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/")),
    ("game-uid", re.compile(r"\bUID\b[^\n]{0,12}?\d{6,}")),
    ("github-token", re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai-token", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".exe", ".dll", ".png"}


def tracked_text_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    paths = []
    for rel in result.stdout.splitlines():
        if Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        paths.append(ROOT / rel)
    return paths


class PrivacyGuardTests(unittest.TestCase):
    def test_no_personal_data_in_tracked_files(self) -> None:
        findings = []
        for path in tracked_text_files():
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                for name, pattern in PATTERNS:
                    if pattern.search(line):
                        findings.append(f"{path.relative_to(ROOT)}:{line_no}: {name}: {line.strip()[:120]}")
        self.assertEqual([], findings, "privacy guard findings:\n" + "\n".join(findings))


if __name__ == "__main__":
    unittest.main(verbosity=2)
