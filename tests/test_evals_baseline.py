"""Tests for oryxflow.evals.baseline -- git-ref baselines and anchored probe variants.

Everything runs against a scratch git repository built in ``tmp_path``; no network, and
no dependence on the oryxflow repo's own history or on a global git identity.
"""
import subprocess

import pytest

import oryxflow.evals as ev
from oryxflow.evals.baseline import (BaselineError, Variant, VariantError, git_sha,
                                     git_tree)


def git(repo, *args):
    proc = subprocess.run(['git'] + list(args), cwd=str(repo),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr.decode('utf-8', 'replace')
    return proc.stdout.decode('utf-8', 'replace')


class Repo(object):
    """A scratch repo path that can also carry the commit SHAs the tests need."""

    def __init__(self, path):
        self.path = path

    def __truediv__(self, other):
        return self.path / other

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-m', message)
    return git(repo, 'rev-parse', 'HEAD').strip()


@pytest.fixture
def repo(tmp_path):
    """A scratch repo with two commits: prompts/{system,deleted}.md, then a deletion.

    Commit 1 ('before') has both templates. Commit 2 ('after') deletes one of them and
    edits the other -- the shape the baseline exists for, where the change under test is
    a DELETION that no string replacement could put back.
    """
    root = Repo(tmp_path / 'scratch')
    (root / 'prompts').mkdir(parents=True)
    git(root, 'init')
    git(root, 'config', 'user.email', 'test@example.com')
    git(root, 'config', 'user.name', 'Test')
    (root / 'prompts' / 'system.md').write_text('You are helpful.\nBe brief.\n')
    (root / 'prompts' / 'deleted.md').write_text('ALWAYS cite a source.\n')
    (root / 'README.md').write_text('scratch\n')
    root.before = commit(root, 'before')

    (root / 'prompts' / 'deleted.md').unlink()
    (root / 'prompts' / 'system.md').write_text('You are helpful.\n')
    root.after = commit(root, 'after')
    return root


# 28. the property a string replacement cannot provide, and the reason git_tree exists

def test_git_tree_restores_a_file_deleted_in_the_working_tree(repo, tmp_path):
    assert not (repo / 'prompts' / 'deleted.md').exists()

    dest = git_tree(repo.before, 'prompts', dest=tmp_path / 'base', repo=repo)

    restored = dest / 'prompts' / 'deleted.md'
    assert restored.exists(), 'the deleted template was not restored'
    assert restored.read_bytes() == b'ALWAYS cite a source.\n'
    # and the file that merely changed is the OLD bytes, not the working-tree bytes
    assert (dest / 'prompts' / 'system.md').read_bytes() == b'You are helpful.\nBe brief.\n'
    # scoped to `paths`: nothing outside prompts/ came along
    assert not (dest / 'README.md').exists()


def test_git_tree_materializes_once_and_reuses(repo, tmp_path):
    dest = git_tree(repo.before, 'prompts', dest=tmp_path / 'base', repo=repo)
    stamp = (dest / 'prompts' / 'deleted.md').stat().st_mtime_ns
    again = git_tree(repo.before, 'prompts', dest=tmp_path / 'base', repo=repo)
    assert again == dest
    assert (dest / 'prompts' / 'deleted.md').stat().st_mtime_ns == stamp


def test_git_tree_default_dest_is_keyed_by_sha_and_gitignored(repo):
    dest = git_tree(repo.before, 'prompts', repo=repo)
    assert dest.parent == repo / '.oryxflow' / 'baseline'
    assert dest.name == repo.before[:12]
    assert (dest.parent / '.gitignore').read_text() == '*\n'
    # git agrees it is ignored, so a materialized baseline never shows up as a change
    out = git(repo, 'status', '--porcelain')
    assert '.oryxflow' not in out


# 29. an empty baseline reads as "the old version already scored well"

def test_git_tree_raises_when_the_ref_yields_no_files(repo, tmp_path):
    with pytest.raises(RuntimeError) as exc:
        git_tree(repo.before, 'templates', dest=tmp_path / 'base', repo=repo)
    assert 'no files' in str(exc.value)
    assert 'templates' in str(exc.value)
    assert isinstance(exc.value, BaselineError)


def test_git_tree_raises_on_an_unresolvable_ref(repo, tmp_path):
    with pytest.raises(RuntimeError):
        git_tree('no-such-ref', 'prompts', dest=tmp_path / 'base', repo=repo)


# 30. expect_absent: a present file means the ref is wrong and the arm measures nothing

def test_git_tree_expect_absent_raises_when_the_file_is_present(repo, tmp_path):
    with pytest.raises(RuntimeError) as exc:
        git_tree(repo.before, 'prompts', dest=tmp_path / 'base', repo=repo,
                 expect_absent=['prompts/deleted.md'])
    assert 'prompts/deleted.md' in str(exc.value)
    assert not (tmp_path / 'base').exists(), 'nothing should be written on a failed check'


def test_git_tree_expect_absent_passes_at_the_ref_where_it_is_gone(repo, tmp_path):
    dest = git_tree(repo.after, 'prompts', dest=tmp_path / 'base', repo=repo,
                    expect_absent=['prompts/deleted.md'])
    assert (dest / 'prompts' / 'system.md').read_bytes() == b'You are helpful.\n'
    assert not (dest / 'prompts' / 'deleted.md').exists()


# 31. a variant whose anchor is gone must not be a silent no-op

def test_variant_applies_when_the_anchor_is_present():
    v = Variant(old='Be brief.', new='Answer in one sentence.')
    assert v.apply('You are helpful.\nBe brief.\n') == \
        'You are helpful.\nAnswer in one sentence.\n'
    assert v('Be brief.') == 'Answer in one sentence.'


def test_variant_raises_when_the_anchor_is_absent():
    v = Variant(old='Be brief.', new='Answer in one sentence.')
    with pytest.raises(ValueError) as exc:
        v.apply('You are helpful.\n')
    assert isinstance(exc.value, VariantError)
    assert 'Be brief.' in str(exc.value)


def test_variant_code_version_changes_with_the_replacement():
    a = Variant('x', 'y')
    assert a.code_version() == Variant('x', 'y').code_version()
    assert a.code_version() != Variant('x', 'z').code_version()


# 32. an arm's code_version() is the resolved SHA, not the ref string

def test_code_version_from_git_tree_is_a_resolved_sha(repo, tmp_path):

    class Arm(object):
        """Stand-in for a TaskEval arm built from a git ref."""
        ref = 'HEAD'

        def code_version(self):
            git_tree(self.ref, 'prompts', dest=tmp_path / 'base', repo=repo)
            return git_sha(self.ref, repo=repo)

    arm = Arm()
    before = arm.code_version()
    assert before != 'HEAD'
    assert len(before) == 40 and all(c in '0123456789abcdef' for c in before)
    assert before == repo.after

    (repo / 'prompts' / 'system.md').write_text('You are helpful. Cite sources.\n')
    new_head = commit(repo, 'third')

    after = arm.code_version()
    assert after == new_head
    # the same ref expression now names different content, and the cache key moved with
    # it -- which is the whole reason the SHA and not 'HEAD' is the code_version
    assert after != before


def test_git_sha_resolves_an_annotated_tag_to_its_commit(repo):
    git(repo, 'tag', '-a', 'v1', '-m', 'v1')
    assert git_sha('v1', repo=repo) == repo.after
    assert git_sha('HEAD~1', repo=repo) == repo.before


# ------------------------------- PromptArm: the tree and the key cannot disagree

def test_working_tree_arm_reads_and_keys_the_same_files(tmp_path):
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts' / 'a.md').write_text('one')
    arm = ev.PromptArm(globs=['prompts/*.md'], root=tmp_path, subdir='prompts')
    assert arm.dir() == tmp_path / 'prompts'
    before = arm.code_version()
    (tmp_path / 'prompts' / 'a.md').write_text('two')
    assert arm.code_version() != before      # content, not mtime


def test_a_ref_arm_is_keyed_on_the_resolved_sha_not_the_ref_text(repo):
    """'HEAD~1' names different content after every commit; the sha does not."""
    arm = ev.PromptArm(ref='HEAD', paths=['prompts/'], root=repo)
    assert arm.code_version() == ev.git_sha('HEAD', repo=repo)
    assert len(arm.code_version()) == 40


def test_a_working_tree_arm_without_globs_is_refused(tmp_path):
    """Keyed on nothing means an edited prompt is served from cache forever."""
    with pytest.raises(ValueError, match='globs'):
        ev.PromptArm(root=tmp_path)


def test_a_ref_arm_without_paths_is_refused(tmp_path):
    with pytest.raises(ValueError, match='paths'):
        ev.PromptArm(ref='HEAD', root=tmp_path)


def test_both_arms_hand_back_the_same_layout(repo):
    """The loader must not have to know which arm it got."""
    live = ev.PromptArm(globs=['prompts/*.md'], root=repo, subdir='prompts')
    base = ev.PromptArm(ref='HEAD', paths=['prompts/'], root=repo, subdir='prompts')
    assert live.dir().name == base.dir().name == 'prompts'
    assert sorted(p.name for p in base.dir().glob('*.md'))
