import importlib.util, pathlib, re


def test_oryxflow_evals_gone():
    assert importlib.util.find_spec('oryxflow.evals') is None


def test_evals_extra_is_oryxeval():
    src = (pathlib.Path(__file__).parents[1] / 'setup.py').read_text()
    m = re.search(r"'evals':\s*\[([^\]]*)\]", src)
    assert m and 'oryxeval>=26.11.0' in m.group(1) and 'python_version' in m.group(1)
    assert 'pydantic' not in m.group(1)
