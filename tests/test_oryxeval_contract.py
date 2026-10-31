"""Symbols the oryxeval package imports from oryxflow.

A failure here breaks the oryxeval package; coordinate a release there.
"""
import ast, hashlib, sys
import pytest
import oryxflow, oryxflow.codehash as ch, oryxflow.utils, oryxflow.tasks, oryxflow.state, oryxflow.core, oryxflow.settings
import oryxflow.parameter

PARAMS = ['Parameter', 'IntParameter', 'FloatParameter', 'BoolParameter', 'ChoiceParameter',
          'ListParameter', 'DictParameter', 'DateParameter', 'EnumParameter']


def test_strip_docstrings_public():
    assert ch.strip_docstrings is ch._strip_docstrings


@pytest.mark.parametrize('obj,name', [
    (ch, 'callable_code_hash'), (ch, 'task_code_hash'), (ch, '_strip_docstrings'),
    (oryxflow, 'hash_files'), (oryxflow.utils, 'hash_files'),
    (oryxflow.tasks, 'TaskJson'), (oryxflow.tasks, 'TaskPqPandas'),
    (oryxflow, 'WorkflowMulti'), (oryxflow, 'runIterConcat'),
    *[(oryxflow, p) for p in PARAMS],
    *[(oryxflow.parameter, p) for p in PARAMS[1:]],   # oryxeval.cli imports from here
    (oryxflow.state, 'clear_cache'), (oryxflow.core, '_code_warned'),
    (oryxflow.settings, 'eventspath'), (oryxflow.settings, 'dir'), (oryxflow.settings, 'dirpath'),
])
def test_contract_symbol(obj, name):
    assert hasattr(obj, name)


# oryxeval's _source_hash is md5(ast.dump(strip_docstrings(ast.parse(src)))): changing this
# output moves EVERY oryxeval cache key, so every user re-pays for every model call. Don't
# update the value to make the test pass; keep the old output (or coordinate an oryxeval
# release that says so). ast.dump's format is per Python minor, hence one value per version.
GOLDEN_SRC = 'def f(x):\n    """Doc."""\n    return x + 1\n'
GOLDEN_MD5 = {(3, 12): '93f50de2c4e484e7e8e3a2093bb8280b'}


@pytest.mark.skipif(sys.version_info[:2] not in GOLDEN_MD5, reason='no golden for this Python')
def test_strip_docstrings_golden():
    blob = ast.dump(ch.strip_docstrings(ast.parse(GOLDEN_SRC)))
    assert hashlib.md5(blob.encode('utf-8')).hexdigest() == GOLDEN_MD5[sys.version_info[:2]]
