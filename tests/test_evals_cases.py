"""Tests for oryxflow.evals.cases.load_cases -- all offline, no network."""

import json
import textwrap

import pytest

pydantic = pytest.importorskip('pydantic')
pytest.importorskip('pydantic_evals')

from pydantic import BaseModel, ValidationError
from pydantic_evals import Case

from oryxflow.evals.cases import load_cases


class Turn(BaseModel):
    question: str
    state: str = 'empty'
    turn_index: int = 0


def write(path, text):
    path.write_text(textwrap.dedent(text).lstrip('\n'), encoding='utf-8')
    return path


# ---- 1. CSV round-trip: columns route to inputs / expected / metadata ---------------

def test_csv_routes_inputs_expected_metadata(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,state,turn_index,expected,phrasing,synthetic
        c1,where is it?,empty,0,north,vague,false
        c2,how many?,filled,2,twelve,direct,true
    """)
    cases = load_cases(csv_path, inputs=Turn)

    assert [c.name for c in cases] == ['c1', 'c2']
    assert cases[0].inputs == Turn(question='where is it?', state='empty', turn_index=0)
    assert cases[1].inputs.turn_index == 2                      # pydantic coerced the CSV string
    assert cases[0].expected_output == 'north'
    assert cases[0].metadata == {'phrasing': 'vague', 'synthetic': False}
    assert cases[1].metadata == {'phrasing': 'direct', 'synthetic': True}
    assert cases.excluded == 0


def test_expected_dotted_columns_become_a_mapping(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,expected.answer,expected.unit
        c1,how far?,12,km
    """)
    cases = load_cases(csv_path, inputs=Turn)
    assert cases[0].expected_output == {'answer': '12', 'unit': 'km'}


def test_expected_model_is_built_from_dotted_columns(tmp_path):
    class Answer(BaseModel):
        answer: int
        unit: str

    csv_path = write(tmp_path / 'cases.csv', """
        name,question,expected.answer,expected.unit
        c1,how far?,12,km
    """)
    cases = load_cases(csv_path, inputs=Turn, expected=Answer)
    assert cases[0].expected_output == Answer(answer=12, unit='km')


def test_defaults_apply_when_the_cell_is_empty(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,state
        c1,where is it?,
    """)
    cases = load_cases(csv_path, inputs=Turn)
    assert cases[0].inputs.state == 'empty'


def test_missing_name_column_falls_back_to_a_positional_name(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        question
        where is it?
    """)
    cases = load_cases(csv_path, inputs=Turn)
    assert cases[0].name == 'case_1'


# ---- 2. an unrecognized column lands in metadata, not dropped -----------------------

def test_unknown_column_goes_to_metadata(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,difficulty,source_team
        c1,where is it?,hard,alpha
    """)
    cases = load_cases(csv_path, inputs=Turn)
    # Metadata becomes a DataFrame column, so dropping it would silently remove a slice.
    assert cases[0].metadata == {'difficulty': 'hard', 'source_team': 'alpha'}
    assert cases[0].inputs == Turn(question='where is it?')


def test_without_an_inputs_model_columns_become_inputs(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,difficulty,holdout
        c1,where is it?,hard,false
    """)
    cases = load_cases(csv_path)
    assert cases[0].inputs == {'question': 'where is it?', 'difficulty': 'hard'}
    assert cases[0].metadata == {'holdout': False}


# ---- 3. a typo'd input column raises ValidationError naming the field ---------------

def test_typoed_input_column_raises_naming_the_field(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,quesiton,state
        c1,where is it?,empty
    """)
    with pytest.raises(ValidationError) as excinfo:
        load_cases(csv_path, inputs=Turn)
    message = str(excinfo.value)
    assert 'question' in message
    assert [e['loc'] for e in excinfo.value.errors()] == [('question',)]


def test_bad_input_value_raises_naming_the_field(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,turn_index
        c1,where is it?,not-a-number
    """)
    with pytest.raises(ValidationError) as excinfo:
        load_cases(csv_path, inputs=Turn)
    assert 'turn_index' in str(excinfo.value)


# ---- 4. @path values -----------------------------------------------------------------

def test_at_path_loads_the_file_text(tmp_path):
    (tmp_path / 'fixtures').mkdir()
    doc = write(tmp_path / 'fixtures' / 'doc.md', """
        # A long fixture document

        Repeated down a CSV column this would be unreadable.
    """)
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,notes
        c1,@fixtures/doc.md,@fixtures/doc.md
    """)
    cases = load_cases(csv_path, inputs=Turn)
    assert cases[0].inputs.question == doc.read_text(encoding='utf-8')
    assert cases[0].metadata['notes'] == doc.read_text(encoding='utf-8')


def test_missing_at_path_names_the_case_and_the_column(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question
        c1,@fixtures/gone.md
    """)
    with pytest.raises(FileNotFoundError) as excinfo:
        load_cases(csv_path, inputs=Turn)
    message = str(excinfo.value)
    assert "c1" in message
    assert "question" in message
    assert "gone.md" in message


def test_at_path_resolves_relative_to_the_case_file_not_the_cwd(tmp_path, monkeypatch):
    sub = tmp_path / 'evals'
    sub.mkdir()
    write(sub / 'doc.md', 'fixture text')
    csv_path = write(sub / 'cases.csv', """
        name,question
        c1,@doc.md
    """)
    monkeypatch.chdir(tmp_path)
    cases = load_cases(csv_path, inputs=Turn)
    assert cases[0].inputs.question == 'fixture text'


# ---- 5. holdout excludes and counts --------------------------------------------------

@pytest.mark.parametrize('flag', ['true', 'TRUE', 'yes', '1', 'Yes'])
def test_holdout_truthy_excludes_and_counts(tmp_path, flag):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,holdout
        c1,a?,{}
        c2,b?,false
        c3,c?,
    """.format(flag))
    cases = load_cases(csv_path, inputs=Turn)
    assert [c.name for c in cases] == ['c2', 'c3']
    assert cases.excluded == 1
    assert len(cases) == 2


def test_no_holdout_column_excludes_nothing(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question
        c1,a?
    """)
    assert load_cases(csv_path, inputs=Turn).excluded == 0


def test_reserved_boolean_columns_survive_as_booleans(tmp_path):
    csv_path = write(tmp_path / 'cases.csv', """
        name,question,synthetic,expect_write,count
        c1,a?,1,0,1
    """)
    meta = load_cases(csv_path, inputs=Turn)[0].metadata
    # expect_* / synthetic are boolean by convention, so 1/0 are safe to read as bools;
    # an ordinary column keeps its string, because 1 there is as likely to be a count.
    assert meta == {'synthetic': True, 'expect_write': False, 'count': '1'}


# ---- 6. YAML and JSONL produce the same list as the CSV -----------------------------

ROWS = [
    {'name': 'c1', 'question': 'where is it?', 'state': 'empty', 'turn_index': 0,
     'expected': 'north', 'phrasing': 'vague', 'synthetic': False, 'holdout': False},
    {'name': 'c2', 'question': 'how many?', 'state': 'filled', 'turn_index': 2,
     'expected': 'twelve', 'phrasing': 'direct', 'synthetic': True, 'holdout': False},
    {'name': 'c3', 'question': 'secret?', 'state': 'empty', 'turn_index': 1,
     'expected': 'nope', 'phrasing': 'vague', 'synthetic': False, 'holdout': True},
]
COLUMNS = list(ROWS[0])


def test_yaml_and_jsonl_match_the_csv(tmp_path):
    csv_path = tmp_path / 'cases.csv'
    lines = [','.join(COLUMNS)]
    for row in ROWS:
        lines.append(','.join(str(row[c]).lower() if isinstance(row[c], bool) else str(row[c])
                              for c in COLUMNS))
    csv_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    yaml_path = tmp_path / 'cases.yaml'
    yaml_path.write_text(
        '\n'.join('- ' + '\n  '.join('{}: {}'.format(k, json.dumps(v)) for k, v in row.items())
                  for row in ROWS) + '\n', encoding='utf-8')

    jsonl_path = tmp_path / 'cases.jsonl'
    jsonl_path.write_text('\n'.join(json.dumps(row) for row in ROWS) + '\n', encoding='utf-8')

    from_csv = load_cases(csv_path, inputs=Turn)
    from_yaml = load_cases(yaml_path, inputs=Turn)
    from_jsonl = load_cases(jsonl_path, inputs=Turn)

    assert list(from_csv) == list(from_yaml) == list(from_jsonl)
    assert from_csv.excluded == from_yaml.excluded == from_jsonl.excluded == 1
    assert isinstance(from_csv[0], Case)
    assert from_csv[0].inputs == Turn(question='where is it?', state='empty', turn_index=0)


def test_yaml_accepts_a_top_level_cases_key(tmp_path):
    yaml_path = tmp_path / 'cases.yaml'
    yaml_path.write_text('cases:\n  - name: c1\n    question: a?\n', encoding='utf-8')
    cases = load_cases(yaml_path, inputs=Turn)
    assert [c.name for c in cases] == ['c1']


def test_jsonl_ignores_blank_lines(tmp_path):
    jsonl_path = tmp_path / 'cases.jsonl'
    jsonl_path.write_text('{"name": "c1", "question": "a?"}\n\n', encoding='utf-8')
    assert len(load_cases(jsonl_path, inputs=Turn)) == 1


def test_unsupported_suffix_is_refused(tmp_path):
    other = write(tmp_path / 'cases.txt', 'name,question\nc1,a?\n')
    with pytest.raises(ValueError, match=r'\.csv, \.yaml or \.jsonl'):
        load_cases(other, inputs=Turn)


def test_inline_rows_route_like_a_file():
    from pydantic import BaseModel as _BM

    class _In(_BM):
        request: str

    cases = load_cases([{'name': 'a', 'request': 'hi', 'control': True},
                        {'name': 'b', 'request': 'yo', 'holdout': 1}], inputs=_In)
    assert [c.name for c in cases] == ['a']
    assert cases[0].inputs.request == 'hi'
    assert cases[0].metadata['control'] is True
    assert cases.excluded == 1
