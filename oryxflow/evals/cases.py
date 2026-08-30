"""Eval cases as a file a domain expert can edit, not constructors in Python.

A case set buried in ``Case(...)`` calls stops being reviewable as a diff, cannot be
edited by the person who actually has the examples, and turns "add fifty more" into code
generation. This module reads the same cases from a spreadsheet, a YAML file or a JSONL
file, and routes the columns by name with nothing to configure.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from pydantic_evals import Case


# Reserved metadata column names. Mostly documented rather than enforced -- a user may
# have their own meaning for them, and the cost of being wrong is a slice column with a
# surprising name, not a wrong number. Two exceptions: ``holdout`` is acted on, and
# ``arm`` is REFUSED by ``sweep`` (it is the label sweep gives each cell, so a metadata
# column of that name would be shadowed and any slice on it would silently report the
# arm label instead).
RESERVED_METADATA = ('synthetic', 'holdout', 'arm', 'expect_*')

_BOOL_TRUE = ('true', 'yes')
_BOOL_FALSE = ('false', 'no')
_TRUTHY = ('true', 'yes', '1')

_EXPECTED = 'expected'
_EXPECTED_PREFIX = 'expected.'


class CaseList(list):
    """The list of cases, plus how many rows were withheld.

    ``excluded`` counts the ``holdout`` rows that were dropped, so a report can state
    the number. A case whose text is embedded in the prompt under test is scoring
    against its own answer key, and a silent exclusion is as misleading as none at all.
    """

    excluded = 0


def load_cases(path, inputs=None, expected=None, name_col='name'):
    """Load eval cases from a .csv / .yaml / .jsonl file into pydantic-evals ``Case`` objects.

    Column routing, by name, with no configuration:
      * ``name``                      -> the case name
      * a field of the ``inputs`` model -> that input field (validated by pydantic)
      * ``expected`` / ``expected.*``  -> the expected output
      * everything else                -> case metadata, which becomes a DataFrame
                                          column and is therefore available as a report slice

    A value of the form ``@some/path.md`` is replaced by that file's text, resolved
    relative to the case file. This is what keeps a flat CSV usable with realistic
    inputs -- a long fixture document lives in one file instead of being repeated
    down a column.

    ``inputs`` is a pydantic model class. Pass it: a column your model has no field for
    lands in metadata, so a misspelled one leaves its real field unset and pydantic
    raises a ``ValidationError`` naming that field. That check is the whole reason
    moving cases out of Python is safe. Without a model there is nothing to tell an
    input column from a metadata one, so every column except ``name``, ``expected*``
    and the reserved names below becomes an input.

    ``expected`` is an optional model class for the expected output, built from the
    ``expected.*`` columns the same way.

    Reserved metadata names: ``synthetic`` (kept apart from real cases, because a
    synthetic set written by the mind that wrote the prompt flatters it), ``holdout``,
    ``arm``, and ``expect_*`` for per-case expectations. A truthy ``holdout``
    **excludes** the case from the returned list; the number dropped is on the returned
    list's ``.excluded`` attribute. ``arm`` is the one name a sweep will not accept --
    it labels each cell of the matrix, so a column of that name is refused up front
    rather than shadowed (name yours ``surface``, ``write_arm``, ``variant``...).

    Returns a ``CaseList`` -- a list of ``Case`` carrying that count.
    """
    path = Path(path)
    rows = _read(path)
    field_names = _model_field_names(inputs) if inputs is not None else None

    cases = CaseList()
    excluded = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(
                "{}: case {} is a {}, expected one mapping per case".format(
                    path, i + 1, type(row).__name__))
        row = {(k.strip() if isinstance(k, str) else k): v for k, v in row.items()}
        name = row.pop(name_col, None)
        name = str(name) if name not in (None, '') else 'case_{}'.format(i + 1)
        row = {k: _resolve_at(v, path, name, k) for k, v in row.items()}

        kwargs, expected_map, expected_scalar, metadata = {}, {}, None, {}
        has_expected_scalar = False
        for col, value in row.items():
            if col == _EXPECTED:
                expected_scalar, has_expected_scalar = value, True
            elif col.startswith(_EXPECTED_PREFIX):
                expected_map[col[len(_EXPECTED_PREFIX):]] = value
            elif field_names is None:
                if _is_reserved(col):
                    metadata[col] = _coerce_metadata(col, value)
                else:
                    kwargs[col] = value
            elif col in field_names:
                kwargs[col] = value
            else:
                metadata[col] = _coerce_metadata(col, value)

        if has_expected_scalar and expected_map:
            raise ValueError(
                "case '{}' in {}: has both an 'expected' column and expected.* columns "
                "-- use one or the other".format(name, path))
        if has_expected_scalar and isinstance(expected_scalar, dict):
            expected_map, has_expected_scalar = dict(expected_scalar), False

        case_inputs = _drop_empty(kwargs)
        if inputs is not None:
            case_inputs = _build(inputs, case_inputs, name, path)

        if expected is not None:
            if has_expected_scalar:
                raise ValueError(
                    "case '{}' in {}: expected= is a model, so the file needs "
                    "expected.<field> columns, not a bare 'expected' column".format(name, path))
            case_expected = _build(expected, _drop_empty(expected_map), name, path)
        elif expected_map:
            case_expected = _drop_empty(expected_map)
        else:
            case_expected = expected_scalar if expected_scalar != '' else None

        if _truthy(metadata.get('holdout')):
            excluded += 1
            continue
        cases.append(Case(name=name, inputs=case_inputs, metadata=metadata or None,
                          expected_output=case_expected))

    cases.excluded = excluded
    return cases


# ---- readers ---------------------------------------------------------------------

def _read(path):
    suffix = path.suffix.lower()
    if suffix == '.csv':
        return _read_csv(path)
    if suffix in ('.yaml', '.yml'):
        return _read_yaml(path)
    if suffix == '.jsonl':
        return _read_jsonl(path)
    raise ValueError(
        "{}: unsupported case file '{}' -- use .csv, .yaml or .jsonl".format(path, suffix))


def _read_csv(path):
    # utf-8-sig: a CSV saved by a spreadsheet carries a BOM, which would otherwise
    # become part of the first column's name and route that column to metadata.
    with open(path, newline='', encoding='utf-8-sig') as f:
        return [dict(row) for row in csv.DictReader(f)]


def _read_yaml(path):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f)
    if isinstance(raw, dict) and 'cases' in raw:
        raw = raw['cases']
    if not isinstance(raw, list):
        raise ValueError(
            "{}: expected a list of cases, or a 'cases:' key holding one".format(path))
    return raw


def _read_jsonl(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                raise ValueError("{} line {}: not valid JSON -- {}".format(path, i, e))
    return rows


# ---- helpers ---------------------------------------------------------------------

def _model_field_names(model):
    fields = getattr(model, 'model_fields', None) or getattr(model, '__fields__', None)
    if not fields:
        raise TypeError(
            "{!r} is not a pydantic model class -- load_cases needs one so a misspelled "
            "column is a ValidationError rather than a silent default".format(model))
    return list(fields)


def _build(model, kwargs, name, path):
    try:
        return model(**kwargs)
    except Exception as e:
        # Keep the exception type -- a ValidationError already names the offending
        # field -- and add the case, which the file knows and the model does not.
        if hasattr(e, 'add_note'):
            e.add_note("while building case '{}' from {}".format(name, path))
        raise


def _resolve_at(value, path, name, col):
    """``@some/path.md`` -> that file's text, resolved relative to the case file."""
    if not isinstance(value, str) or not value.startswith('@'):
        return value
    ref = value[1:].strip()
    target = (path.parent / ref).resolve()
    if not target.is_file():
        raise FileNotFoundError(
            "case '{}', column '{}': '{}' points at {}, which does not exist".format(
                name, col, value, target))
    return target.read_text(encoding='utf-8')


def _drop_empty(kwargs):
    """An empty CSV cell means 'not provided', so the model's own default applies."""
    return {k: v for k, v in kwargs.items() if v not in (None, '')}


def _is_reserved(col):
    return col in ('synthetic', 'holdout') or col.startswith('expect_')


def _coerce_metadata(col, value):
    """CSV gives every cell as a string; recover booleans where the target is unambiguous.

    ``true/false/yes/no`` are booleans in any column. ``1/0`` are booleans only in a
    column that is boolean by convention (``holdout``, ``synthetic``, ``expect_*``) --
    elsewhere they are as likely to be a count, and guessing wrong would put a bool in
    a column someone means to average.
    """
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    if _is_reserved(col) and text in ('1', '0'):
        return text == '1'
    return None if value == '' else value


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)
