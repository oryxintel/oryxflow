"""Advisory warning when a run would build a second output directory below an existing one.

The records are loguru records, so they are collected with a sink appending to a list --
pytest's caplog only sees stdlib logging and would report nothing here.
"""
import os

import pytest
from loguru import logger

import oryxflow
import oryxflow.settings
import oryxflow.utils


@pytest.fixture
def warnings_seen():
    messages = []
    orig_cwd = os.getcwd()
    orig_dir, orig_dirpath = oryxflow.settings.dir, oryxflow.settings.dirpath
    orig_warn = oryxflow.settings.warn_nested_dir
    oryxflow.utils._warned_nested_dirs.clear()
    logger.enable('oryxflow')
    handler = logger.add(messages.append, level='WARNING', filter='oryxflow', format='{message}')
    try:
        yield messages
    finally:
        logger.remove(handler)
        logger.disable('oryxflow')
        os.chdir(orig_cwd)
        oryxflow.settings.dir, oryxflow.settings.dirpath = orig_dir, orig_dirpath
        oryxflow.settings.warn_nested_dir = orig_warn
        oryxflow.utils._warned_nested_dirs.clear()


def make_dir(path, populated=True):
    path.mkdir(parents=True, exist_ok=True)
    if populated:
        (path / 'TaskGetData.pq').write_text('output')
    return path


def test_warns_on_nested_empty_dir(tmp_path, warnings_seen):
    # tmp_path is a repo boundary so the walk cannot reach a real data/ above the tmp dir
    (tmp_path / '.git').mkdir()
    parent_data = make_dir(tmp_path / 'data')
    sub = tmp_path / 'sub'
    sub.mkdir()
    os.chdir(sub)

    oryxflow.set_dir('data')

    assert len(warnings_seen) == 1
    message = warnings_seen[0]
    assert str(parent_data) in message
    assert str(sub / 'data') in message


def test_silent_without_ancestor(tmp_path, warnings_seen):
    (tmp_path / '.git').mkdir()
    sub = tmp_path / 'sub'
    sub.mkdir()
    os.chdir(sub)

    oryxflow.set_dir('data')

    assert warnings_seen == []


def test_silent_when_local_dir_populated(tmp_path, warnings_seen):
    (tmp_path / '.git').mkdir()
    make_dir(tmp_path / 'data')
    sub = tmp_path / 'sub'
    make_dir(sub / 'data')
    os.chdir(sub)

    oryxflow.set_dir('data')

    assert warnings_seen == []


def test_silent_when_suppressed(tmp_path, warnings_seen):
    (tmp_path / '.git').mkdir()
    make_dir(tmp_path / 'data')
    sub = tmp_path / 'sub'
    sub.mkdir()
    os.chdir(sub)
    oryxflow.settings.warn_nested_dir = False

    oryxflow.set_dir('data')

    assert warnings_seen == []


def test_warns_once_per_process(tmp_path, warnings_seen):
    (tmp_path / '.git').mkdir()
    make_dir(tmp_path / 'data')
    sub = tmp_path / 'sub'
    sub.mkdir()
    os.chdir(sub)

    oryxflow.set_dir('data')
    oryxflow.set_dir('data')

    assert len(warnings_seen) == 1


def test_stops_at_repo_boundary(tmp_path, warnings_seen):
    (tmp_path / '.git').mkdir()
    make_dir(tmp_path / 'data')
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    sub = repo / 'sub'
    sub.mkdir()
    os.chdir(sub)

    oryxflow.set_dir('data')

    assert warnings_seen == []
