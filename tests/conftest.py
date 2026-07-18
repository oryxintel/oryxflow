"""pytest fixtures shared by the test suite.

The only thing here is isolation for the *documentation* tests. Files named
``test_docs_*.py`` are generated from the Markdown docs by phmdoctest (see
``docs/CLAUDE.md`` / the ``docs-test`` make-style target). The doc examples call
``oryxflow.set_dir('data/')`` with a relative path, so without isolation they would
scribble a ``data/`` directory into wherever pytest runs. This fixture runs each such
module in a throwaway working directory.

Scope is **module**, not function, on purpose: the code blocks from one page share a
namespace and a cache dir (a later block loads what an earlier block saved), so they
must all see the same working directory. The hand-written suite is untouched — the
fixture no-ops for any module whose name isn't ``test_docs_*``.
"""
import os
import shutil
import tempfile

import pytest


@pytest.fixture(scope="module", autouse=True)
def _isolate_doc_tests(request):
    if not request.module.__name__.rpartition(".")[2].startswith("test_docs_"):
        yield
        return
    orig = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="oryxflow-doctest-")
    os.chdir(tmp)
    try:
        yield
    finally:
        os.chdir(orig)
        shutil.rmtree(tmp, ignore_errors=True)
