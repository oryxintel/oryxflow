import pathlib

import pytest

pytest.importorskip('nbconvert')
nbformat = pytest.importorskip('nbformat')

from oryxflow.targets.notebook import NotebookTarget


def _run_nb(tmp_path):
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell("print('HELLO')")
    cell.outputs = [nbformat.v4.new_output('stream', name='stdout', text='HELLO\n')]
    cell.execution_count = 1
    nb.cells.append(cell)
    run = tmp_path / 'run.ipynb'
    nbformat.write(nb, str(run))
    return run


def _target(tmp_path):
    return NotebookTarget(tmp_path / 'out' / 'report.ipynb')


def test_save_exports_html_without_code(tmp_path):
    run = _run_nb(tmp_path)
    t = _target(tmp_path)
    t.save(run)
    assert t.exists()
    assert not run.exists()
    html = t.html_path.read_text(encoding='utf-8')
    assert 'HELLO' in html
    assert "print('HELLO')" not in html
    assert 'In&nbsp;[' not in html


def test_include_code(tmp_path):
    t = _target(tmp_path)
    t.save(_run_nb(tmp_path), include_code=True)
    assert "print" in t.html_path.read_text(encoding='utf-8')


def test_exists_needs_both(tmp_path):
    t = _target(tmp_path)
    t.save(_run_nb(tmp_path))
    t.html_path.unlink()
    assert not t.exists()


def test_invalidate_removes_both(tmp_path):
    t = _target(tmp_path)
    t.save(_run_nb(tmp_path))
    assert t.invalidate()
    assert not t.path.exists() and not t.html_path.exists()


def test_load_returns_html_path(tmp_path):
    t = _target(tmp_path)
    with pytest.raises(RuntimeError):
        t.load()
    t.save(_run_nb(tmp_path))
    assert t.load() == t.html_path
    assert isinstance(t.load(), pathlib.Path)
    assert t.html_path.suffix == '.html'


def test_export_failure_leaves_nothing(tmp_path):
    t = _target(tmp_path)
    with pytest.raises(Exception):
        t.save(_run_nb(tmp_path), html_template='no-such-template')
    assert not t.path.exists() and not t.html_path.exists()
