#!/usr/bin/env python
"""Regenerate the doc-example tests, run them, and build the MkDocs site.

One portable entry point used by both the local deploy scripts and CI, so "it built
on my machine" and "it built in the Action" run the exact same steps.

Steps:
  1. Compile the runnable Markdown pages into tests/test_docs_*.py with phmdoctest.
  2. Run those doc tests — they fail if a documented example has rotted (an API was
     renamed, a snippet raises, sklearn dropped a dataset, ...).
  3. Build the static site into ./site with `mkdocs build`.

Usage:
    python scripts/build_docs.py                # regenerate tests, run them, build
    python scripts/build_docs.py --check        # additionally fail if committed test
                                                #   files are stale (use in CI)
    python scripts/build_docs.py --skip-tests   # just build (fast local preview)
    python scripts/build_docs.py --strict       # mkdocs build --strict
"""
import argparse
import filecmp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Markdown pages that run top-to-bottom and are compiled into pytest files by phmdoctest.
# Add a page here (and mark its runnable code blocks with <!--phmdoctest-share-names-->)
# to have its examples verified on every build. See docs/CLAUDE.md.
TESTED_PAGES = {
    "docs/docs/quickstart.md": "tests/test_docs_quickstart.py",
    "docs/index.md": "tests/test_docs_index.py",
}


def run(cmd):
    print(">>", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)


def generate(check=False):
    for page, out in TESTED_PAGES.items():
        outp = ROOT / out
        if check:
            fd, tmp = tempfile.mkstemp(suffix=".py")
            os.close(fd)
            run([sys.executable, "-m", "phmdoctest", page, "--outfile", tmp])
            stale = not outp.exists() or not filecmp.cmp(tmp, outp, shallow=False)
            os.unlink(tmp)
            if stale:
                sys.exit(
                    f"ERROR: {out} is out of date with {page}.\n"
                    f"       Run `python scripts/build_docs.py` and commit the result."
                )
        else:
            run([sys.executable, "-m", "phmdoctest", page, "--outfile", str(outp)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if generated test files are stale (CI)")
    ap.add_argument("--skip-tests", action="store_true", help="skip regenerating/running doc tests")
    ap.add_argument("--strict", action="store_true", help="pass --strict to mkdocs build")
    args = ap.parse_args()

    if not args.skip_tests:
        generate(check=args.check)
        run([sys.executable, "-m", "pytest", *TESTED_PAGES.values(), "-q"])

    build = ["mkdocs", "build"]
    if args.strict:
        build.append("--strict")
    run(build)
    print("\nOK: site built to ./site", flush=True)


if __name__ == "__main__":
    main()
