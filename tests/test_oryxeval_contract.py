"""Symbols the oryxeval package imports from oryxflow.

A failure here breaks the oryxeval package; coordinate a release there.
"""
import pytest
import oryxflow, oryxflow.codehash as ch, oryxflow.utils, oryxflow.tasks, oryxflow.state, oryxflow.core, oryxflow.settings


def test_strip_docstrings_public():
    assert ch.strip_docstrings is ch._strip_docstrings


@pytest.mark.parametrize('obj,name', [
    (ch, 'callable_code_hash'), (ch, 'task_code_hash'), (ch, '_strip_docstrings'),
    (oryxflow, 'hash_files'), (oryxflow.utils, 'hash_files'),
    (oryxflow.tasks, 'TaskJson'), (oryxflow.tasks, 'TaskPqPandas'),
    (oryxflow, 'WorkflowMulti'), (oryxflow, 'runIterConcat'),
    *[(oryxflow, p) for p in ['Parameter', 'IntParameter', 'FloatParameter', 'BoolParameter',
       'ChoiceParameter', 'ListParameter', 'DictParameter', 'DateParameter', 'EnumParameter']],
    (oryxflow.state, 'clear_cache'), (oryxflow.core, '_code_warned'), (oryxflow.settings, 'eventspath'),
])
def test_contract_symbol(obj, name):
    assert hasattr(obj, name)
