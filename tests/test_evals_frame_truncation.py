"""A display cap must never reach a scorer.

The regression test for a real failure: a harness capped its output model's ``message``
field at 2000 characters so a CSV row would not be a wall of text, then computed its
metric off the last line of that same field. Long replies were sliced mid-sentence, the
harness read the last line of its own truncation, and the judge reported failures the
model had not committed. Every arm scored 0% and the sweep was measuring itself.

So ``_to_frame()`` never shortens a value in place. A long text column is emitted twice:
``<field>`` in full -- byte-identical to what the model returned, which is what any later
re-analysis reads -- and ``<field>_preview`` capped for display only.
"""
import pytest

pytest.importorskip('pydantic_evals')

from pydantic import BaseModel                                   # noqa: E402
from pydantic_evals import Case, Dataset                          # noqa: E402

import oryxflow                                                   # noqa: E402
import oryxflow.state                                             # noqa: E402
from oryxflow.evals.task import TaskEval                           # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


class Turn(BaseModel):
    q: int


class Reply(BaseModel):
    message: str


# A long reply whose LAST LINE is the part a metric would read -- exactly the shape the
# observed bug destroyed by cutting the string short.
LONG = ('paragraph one. ' * 400) + '\nFINAL LINE: the answer is 42'
SHORT = 'brief'


def make_task(family='TaskEvalTrunc', **attrs):
    cases = [Case(name='long', inputs=Turn(q=0)), Case(name='short', inputs=Turn(q=1))]

    async def case(self, inputs):
        return Reply(message=LONG if inputs.q == 0 else SHORT)

    body = dict(dataset=Dataset(name='ds', cases=cases), retry_task=None,
                preflight=None, code_version='test', case=case)
    body.update(attrs)
    return type(family, (TaskEval,), body)


def test_long_field_is_emitted_twice_never_shortened(env):
    task = make_task()()
    task.run()
    df = task.outputLoad(keys='cases').set_index('case_name')
    cap = task.preview_chars

    assert len(LONG) > cap
    assert 'message' in df.columns
    assert 'message_preview' in df.columns

    # the full column is byte-identical to what the model returned
    assert df.loc['long', 'message'] == LONG
    assert df.loc['short', 'message'] == SHORT
    # ...including the last line, the part the observed bug sliced off
    assert df.loc['long', 'message'].splitlines()[-1] == 'FINAL LINE: the answer is 42'

    # the preview is capped, and is the ONLY thing that is
    assert len(df.loc['long', 'message_preview']) <= cap
    assert df.loc['long', 'message_preview'].startswith('paragraph one.')
    assert df.loc['long', 'message_preview'].endswith('...')
    # a value already under the cap is passed through unchanged
    assert df.loc['short', 'message_preview'] == SHORT


def test_no_preview_column_when_nothing_exceeds_the_cap(env):
    task = make_task(family='TaskEvalTruncHighCap', preview_chars=len(LONG) + 1)()
    task.run()
    df = task.outputLoad(keys='cases')
    assert 'message' in df.columns
    assert 'message_preview' not in df.columns
    assert df.set_index('case_name').loc['long', 'message'] == LONG


def test_preview_chars_none_emits_no_preview_columns(env):
    task = make_task(family='TaskEvalTruncOff', preview_chars=None)()
    task.run()
    df = task.outputLoad(keys='cases')
    assert 'message_preview' not in df.columns
    assert df.set_index('case_name').loc['long', 'message'] == LONG


def test_preview_cap_is_configurable_per_task(env):
    task = make_task(family='TaskEvalTruncTight', preview_chars=50)()
    task.run()
    df = task.outputLoad(keys='cases').set_index('case_name')
    assert len(df.loc['long', 'message_preview']) == 50
    assert df.loc['long', 'message'] == LONG        # still full length


def test_long_metadata_is_previewed_too(env):
    """The rule is frame-wide, not output-only: a long fixture in metadata gets it too."""
    cases = [Case(name='a', inputs=Turn(q=0), metadata={'prompt': LONG}),
             Case(name='b', inputs=Turn(q=1), metadata={'prompt': SHORT})]

    async def case(self, inputs):
        return Reply(message=SHORT)

    cls = type('TaskEvalTruncMeta', (TaskEval,),
               dict(dataset=Dataset(name='ds2', cases=cases), retry_task=None,
                    preflight=None, code_version='test', case=case))
    task = cls()
    task.run()

    df = task.outputLoad(keys='cases').set_index('case_name')
    assert df.loc['a', 'prompt'] == LONG
    assert len(df.loc['a', 'prompt_preview']) <= task.preview_chars
