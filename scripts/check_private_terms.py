"""Commit-time guard: no client names and no local machine paths in a public repo.

The client names are private, so this file does not list them. It reads them, one per line
(``#`` starts a comment), from any of:

- ``.private-terms`` at the repo root (gitignored, machine-local);
- ``~/.oryx-private-terms`` (one file for every repo and worktree on the machine);
- the file named by the ``ORYX_PRIVATE_TERMS_FILE`` environment variable;
- the ``ORYX_PRIVATE_TERMS`` environment variable (terms separated by ``;``).

A term matches case-insensitively as a whole word. Local paths (a Windows drive path into a
user or OneDrive folder, ``/home/<user>``, ``/Users/<user>``) are checked with built-in
patterns; a ``<placeholder>`` after the folder (``/home/<name>/``) is allowed. A line that
must keep a match says ``private-terms: allow``.

Usage:
    python scripts/check_private_terms.py          # lines added in the staged diff
    python scripts/check_private_terms.py --all    # every tracked file
Exit 1 lists each hit as ``file:line: what``. With no terms configured the name check
is skipped with a warning, and the path check still runs.
"""
import os
import re
import subprocess
import sys

ALLOW = 'private-terms: allow'
PATHS = [
    ('local path', re.compile(r'\b[A-Za-z]:[\\/]+(?:OneDrive|Users[\\/]+(?!<)\w)', re.I)),
    ('local path', re.compile(r'(?<![\w.])/home/(?!<)\w')),
    ('local path', re.compile(r'(?<![\w.])/Users/(?!<)\w')),
]


def _git(root, *args):
    return subprocess.run(['git', '-C', root] + list(args), capture_output=True,
                          text=True, encoding='utf-8', errors='replace', check=True).stdout


def load_terms(root, environ=None):
    environ = os.environ if environ is None else environ
    lines = []
    home = environ.get('HOME') or environ.get('USERPROFILE')
    for path in (os.path.join(root, '.private-terms'),
                 home and os.path.join(home, '.oryx-private-terms'),
                 environ.get('ORYX_PRIVATE_TERMS_FILE')):
        if path and os.path.isfile(path):
            with open(path, encoding='utf-8') as f:
                lines += f.read().splitlines()
    lines += (environ.get('ORYX_PRIVATE_TERMS') or '').split(';')
    terms = {line.split('#', 1)[0].strip() for line in lines}
    return sorted(t for t in terms if t)


def patterns(terms):
    out = [('private term', re.compile(r'(?<![A-Za-z0-9]){}(?![A-Za-z0-9])'.format(
        re.escape(t)), re.I)) for t in terms]
    return out + PATHS


def scan_lines(lines, pats):
    """``[(line_no, what)]`` for ``(line_no, text)`` pairs; the term itself is not echoed."""
    hits = []
    for no, text in lines:
        if ALLOW in text:
            continue
        for what, pat in pats:
            if pat.search(text):
                hits.append((no, what))
                break
    return hits


def staged_lines(root):
    """``{path: [(line_no, text)]}`` for the lines the staged diff adds."""
    out, path, no = {}, None, 0
    for line in _git(root, 'diff', '--cached', '-U0', '--no-color').splitlines():
        if line.startswith('+++ '):
            path = line[6:] if line.startswith('+++ b/') else None
        elif line.startswith('@@'):
            no = int(re.match(r'@@ -\S+ \+(\d+)', line).group(1))
        elif path and line.startswith('+'):
            out.setdefault(path, []).append((no, line[1:]))
            no += 1
    return out


def tracked_lines(root):
    out = {}
    for path in _git(root, 'ls-files').splitlines():
        try:
            with open(os.path.join(root, path), encoding='utf-8') as f:
                out[path] = list(enumerate(f.read().splitlines(), 1))
        except (UnicodeDecodeError, OSError):
            continue      # binary or unreadable
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = _git(os.getcwd(), 'rev-parse', '--show-toplevel').strip()
    terms = load_terms(root)
    if not terms:
        print('check_private_terms: no private terms configured (.private-terms, '
              '~/.oryx-private-terms, ORYX_PRIVATE_TERMS_FILE or ORYX_PRIVATE_TERMS); '
              'checking local paths only', file=sys.stderr)
    pats = patterns(terms)
    files = tracked_lines(root) if '--all' in argv else staged_lines(root)
    hits = [(path, no, what) for path, lines in sorted(files.items())
            for no, what in scan_lines(lines, pats)]
    for path, no, what in hits:
        print('{}:{}: {}'.format(path, no, what), file=sys.stderr)
    if hits:
        print('check_private_terms: {} hit(s). Public repo: no client names or local paths '
              '(use the org alias, a repo-relative path or /path/to/<repo>). Mark a line '
              "that must keep its match with '{}'.".format(len(hits), ALLOW), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
