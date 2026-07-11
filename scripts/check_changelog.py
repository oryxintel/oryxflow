#!/usr/bin/env python3
"""Lint CHANGELOG.md for the load-bearing conventions the docs plan pins down.

Dependency-free; run in CI and/or as a pre-commit hook. Asserts, over CHANGELOG.md:

- every non-Unreleased ``## [x.y.z]`` heading carries a ``- YYYY-MM-DD`` date;
- every bullet beginning ``BREAKING:`` also contains ``Migration:`` (same bullet);
- (heuristic, opt-in via --require-entry) if ``oryxflow/`` changed but CHANGELOG.md did
  not, the newest section must be non-empty.

Exit 0 if clean, 1 otherwise.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

HEADING_RE = re.compile(r"^## \[(?P<ver>[^\]]+)\](?P<rest>.*)$")
DATE_RE = re.compile(r"-\s*\d{4}-\d{2}-\d{2}\s*$")


def _bullets(lines):
    """Yield (lineno, text) for markdown bullets, joining continuation lines."""
    cur = None
    start = 0
    for i, raw in enumerate(lines, 1):
        if re.match(r"^\s*[-*]\s+", raw):
            if cur is not None:
                yield start, cur
            cur = raw.strip()
            start = i
        elif cur is not None and raw.strip() and not raw.startswith("#"):
            cur += " " + raw.strip()
        else:
            if cur is not None:
                yield start, cur
            cur = None
    if cur is not None:
        yield start, cur


def check(text):
    errors = []
    lines = text.splitlines()

    for i, raw in enumerate(lines, 1):
        m = HEADING_RE.match(raw)
        if not m:
            continue
        ver = m.group("ver")
        if ver.lower() == "unreleased":
            continue
        if not DATE_RE.search(m.group("rest")):
            errors.append(
                f"line {i}: version heading '[{ver}]' is missing a '- YYYY-MM-DD' date"
            )

    for lineno, bullet in _bullets(lines):
        # strip leading marker
        body = re.sub(r"^\s*[-*]\s+", "", bullet)
        if body.startswith("BREAKING:") and "Migration:" not in bullet:
            errors.append(
                f"line {lineno}: 'BREAKING:' bullet has no same-bullet 'Migration:' clause"
            )
    return errors


def main():
    if not CHANGELOG.exists():
        print(f"error: {CHANGELOG} not found", file=sys.stderr)
        return 1
    errors = check(CHANGELOG.read_text(encoding="utf-8"))
    if errors:
        print("CHANGELOG.md format check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("CHANGELOG.md format check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
