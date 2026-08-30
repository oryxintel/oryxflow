"""The two ways to build a comparison arm, deliberately distinct.

A baseline is *something that existed*, so it is checked out of git: byte-exact by
construction, immune to the live files moving under it, and -- the reason this module
does not simply ship a string-replacement helper -- able to restore a section that was
DELETED. A probe is a rewrite that has not shipped anywhere, so there is no ref to check
out and an anchored ``(old -> new)`` replacement is all there is.

Both fail loudly. A comparison arm that silently becomes a no-op reports the old version
as having scored well, which is indistinguishable from a real result and therefore the
worst failure a comparison can have.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class BaselineError(RuntimeError):
    """A baseline arm could not be built, or was built empty.

    A ``RuntimeError``, because an empty or wrong baseline is not a recoverable
    condition: whatever the arm went on to measure would not be the old version.
    """


class VariantError(ValueError):
    """A variant's anchor text was not found in the rendered text.

    A ``ValueError``, because the arguments no longer describe the text they are
    applied to -- the anchor has been edited away, and applying the variant anyway
    would report the unchanged prompt's numbers as the variant's.
    """


def _git(args, repo=None, binary=False):
    """Run a git command, returning stdout (bytes when ``binary``). Raise on failure."""
    cwd = str(repo) if repo is not None else os.getcwd()
    proc = subprocess.run(['git'] + list(args), cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise BaselineError("git {} failed in {}: {}".format(
            ' '.join(args), cwd, proc.stderr.decode('utf-8', 'replace').strip()))
    return proc.stdout if binary else proc.stdout.decode('utf-8', 'replace')


def git_sha(ref, repo=None):
    """Resolve ``ref`` to its full commit SHA.

    An arm built from a git ref must report *this* as its ``code_version()``, never the
    ref string: ``HEAD~1`` names different content every time something is committed, and
    a cache key that moves under a stable-looking name reads stale numbers as fresh ones.
    A resolved SHA is immutable, so the arm's cache key is stable forever.
    """
    try:
        out = _git(['rev-parse', '--verify', '{}^{{commit}}'.format(ref)], repo=repo)
    except BaselineError:
        out = _git(['rev-parse', '--verify', str(ref)], repo=repo)
    sha = out.strip()
    if not sha:
        raise BaselineError("ref '{}' did not resolve to a commit".format(ref))
    return sha


def _as_list(paths):
    if paths is None:
        return []
    if isinstance(paths, (str, bytes, os.PathLike)):
        return [paths]
    return list(paths)


def _ls_tree(ref, paths, repo=None):
    """Filenames under ``paths`` at ``ref``, recursively, git-style forward slashes."""
    args = ['ls-tree', '-r', '-z', '--name-only', str(ref), '--']
    args += [str(p).replace('\\', '/') for p in paths]
    out = _git(args, repo=repo, binary=True).decode('utf-8', 'surrogateescape')
    return [n for n in out.split('\0') if n]


def _default_dest(sha, repo=None):
    base = Path(repo) if repo is not None else Path.cwd()
    root = base / '.oryxflow' / 'baseline'
    root.mkdir(parents=True, exist_ok=True)
    # The materialized tree is generated, never source: ignore it wherever it lands,
    # so a user whose repo predates this never has to notice it.
    ignore = root / '.gitignore'
    if not ignore.exists():
        ignore.write_text('*\n')
    return root / sha[:12]


def git_tree(ref, paths, dest=None, expect_absent=None, repo=None):
    """Materialize files from a git ref into a directory, for a BASELINE arm.

    Use this for a prompt/template tree as it was before a change: it is
    byte-exact by construction, cannot decay as the live files move, and -- the
    reason it exists -- it restores DELETED files, which a string-replacement
    variant cannot do at all.

    ``ref`` is anything git resolves ('abc123^', 'v2.1', 'main~3'). Because a
    resolved ref is immutable, an arm built from one has a cache key that is
    stable forever.

    Raises RuntimeError when the ref yields no files under ``paths`` -- a
    silently empty baseline reads as "the old version already scored well",
    which is the worst possible failure for a comparison.

    ``expect_absent=`` names files that must NOT exist at this ref; their
    presence means the ref is wrong and the arm is measuring nothing.

    Returns the directory the files were written to. Point your own loader at it --
    how your templates are loaded is yours to know, so nothing here is patched or
    installed on your behalf. The default location is keyed by the resolved SHA,
    beside the eval and gitignored, so a ref is materialized once and reused.
    Pass ``repo=`` to run git somewhere other than the working directory, and use
    ``git_sha(ref)`` for the arm's ``code_version()``.
    """
    paths = _as_list(paths)
    if not paths:
        raise BaselineError(
            "git_tree(ref={!r}) needs at least one path; materializing nothing would "
            "give an empty baseline".format(ref))
    sha = git_sha(ref, repo=repo)

    for name in _as_list(expect_absent):
        found = _ls_tree(sha, [name], repo=repo)
        if found:
            raise BaselineError(
                "ref '{}' ({}) still has {} -- expect_absent said it should not exist "
                "there, so this is not the ref you meant and the arm would measure "
                "nothing.".format(ref, sha[:10], ', '.join(sorted(found))))

    names = _ls_tree(sha, paths, repo=repo)
    if not names:
        raise BaselineError(
            "ref '{}' ({}) has no files under {} -- an empty baseline reads as 'the old "
            "version already scored well', so it is refused rather than measured.".format(
                ref, sha[:10], ', '.join(repr(str(p)) for p in paths)))

    dest = Path(dest) if dest is not None else _default_dest(sha, repo=repo)
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        target = dest.joinpath(*name.split('/'))
        blob = _git(['show', '{}:{}'.format(sha, name)], repo=repo, binary=True)
        if target.exists() and target.read_bytes() == blob:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    manifest = dest / '_oryxflow_baseline.json'
    manifest.write_text(json.dumps(
        {'ref': str(ref), 'sha': sha, 'paths': [str(p) for p in paths], 'files': names},
        indent=2, sort_keys=True))
    return dest


@dataclass
class Variant:
    """An exact (old -> new) replacement applied to already-rendered text, for a
    PROBE arm -- a rewrite that has not shipped, so there is no ref to check out.

    Raises when ``old`` is not present: a variant whose anchor text has been
    edited away silently becomes a no-op, and then reports the unchanged prompt's
    numbers as the variant's.

    NOT for baselines. Use ``git_tree`` there: a replacement cannot restore a
    deleted section, and cannot put one back in its original position.
    """
    old: str
    new: str

    def apply(self, text):
        """Return ``text`` with ``old`` replaced by ``new``. Raise ``VariantError``
        (a ``ValueError``) when ``old`` is absent, rather than returning the text
        unchanged."""
        if self.old not in text:
            raise VariantError(
                "variant anchor not found in the rendered text (anchor {!r}...): the "
                "text it was written against has changed, so this variant would be a "
                "no-op and its arm would report the unchanged prompt's numbers.".format(
                    self.old[:80]))
        return text.replace(self.old, self.new)

    def __call__(self, text):
        return self.apply(text)

    def code_version(self):
        """A token that changes when this replacement changes, for an arm's cache key."""
        h = hashlib.md5()
        h.update(self.old.encode('utf-8'))
        h.update(b'\0')
        h.update(self.new.encode('utf-8'))
        return 'variant-' + h.hexdigest()[:10]


@dataclass
class PromptArm:
    """Where one arm reads its prompt files, AND the cache key that matches them.

    The two have to agree, and nothing else makes them. Written by hand it is two
    places -- a ``prompt_root(arm)`` that returns a directory and a
    ``code_version()`` that returns a token -- and the failure when they drift is
    silent and bad: a baseline arm reading a checked-out tree but keyed on a hash
    of the LIVE files re-runs when a live prompt is edited (merely wasteful) and
    does NOT re-run when you point it at a different ref (stale, and reported as
    fresh). One object owns both, so it cannot happen.

        ARMS = {
            'live':    ev.PromptArm(globs=['prompts/*.jinja2'], root=ROOT),
            'baseline': ev.PromptArm(ref='v2.1', paths=['prompts/'], root=ROOT),
        }

        # in the agent:  ARMS[arm].dir()
        # in the task:   def code_version(self): return ARMS[self.arm].code_version()

    ``ref=None`` means the working tree, keyed on the CONTENT of ``globs``. A
    ``ref`` means that commit, materialized with :py:func:`git_tree` and keyed on
    the resolved sha -- immutable, so a baseline arm's cell stays valid for good.

    ``subdir`` is applied to both, so the two arms hand the loader the same
    layout and it never has to know which one it got.
    """
    ref: str = None
    paths: tuple = ()
    globs: tuple = ()
    root: str = None
    subdir: str = ''
    expect_absent: tuple = ()
    dest: str = None

    def __post_init__(self):
        if self.ref is None and not self.globs:
            raise ValueError(
                'PromptArm: the working-tree arm needs globs= (the files whose CONTENT '
                'is its cache key) -- without them the arm is keyed on nothing and an '
                'edited prompt is served from cache')
        if self.ref is not None and not self.paths:
            raise ValueError(
                'PromptArm: a ref arm needs paths= (what to materialize from {!r})'.format(
                    self.ref))

    def dir(self):
        """The directory this arm reads its prompts from."""
        base = Path(self.root) if self.root is not None else Path.cwd()
        if self.ref is not None:
            base = git_tree(self.ref, self.paths, dest=self.dest,
                            expect_absent=self.expect_absent, repo=self.root)
        return base.joinpath(*self.subdir.split('/')) if self.subdir else base

    def code_version(self):
        """The cache token for the files :py:meth:`dir` will hand out."""
        if self.ref is not None:
            return git_sha(self.ref, repo=self.root)
        from oryxflow.utils import hash_files
        return hash_files(*self.globs, root=self.root)
