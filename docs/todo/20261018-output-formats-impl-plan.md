# Output Formats (Formatted Excel + Notebook Reports) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one shared table of display formats (`oryxflow.report.FORMATS`) and a formatted Excel writer (`oryxflow.utils.excel_write` plus `formatted=True` on the Excel targets) as **Part A**, its own release. Then ship parameterized report notebooks as tasks (`TaskNotebook` executed with papermill) as **Part B**, a later release.

**Architecture:** `oryxflow/report.py` owns the format presets and the column-to-format resolution. It imports only pandas at module level. The Excel writer in `oryxflow/utils.py` imports `report` lazily and reads each preset's `.excel` code. The notebook formatter in `report.py` (Part B) reads `.py`. `TaskNotebook` (`oryxflow/tasks/notebook.py`) and `NotebookTarget` (`oryxflow/targets/notebook.py`) are optional-extra submodules that the package root never imports.

**Tech Stack:** pandas and openpyxl (already core) for Part A. Part B adds jinja2 (pandas Styler) and papermill + nbconvert + nbformat + ipykernel, all in a new `notebook` extra. Tests use pytest.

**Spec:** [`docs/todo/20261018-utils-excel-writer.md`](20261018-utils-excel-writer.md) (Excel writer) and [`docs/todo/20261018-output-notebook-reports.md`](20261018-output-notebook-reports.md) (notebook reports). Executors read both. Where this plan differs from a spec, the plan wins. The differences are listed under "Spec reconciliation", and spec features this plan drops are under "Deferred". Skip those in the spec.

## Global Constraints

- Commit only when the maintainer has authorized commits. When committing, the spec files and this plan go in the same commit as the code they describe (repo convention).
- Part A (Tasks 1–5) is a complete release on its own. Part B (Tasks 6–10) starts only after Part A has shipped, and it must not change any Part A interface.
- Public repo: no client names, private project names, data-vendor names, absolute local paths or credentials in any file, test, docstring or commit message.
- Never create a venv, conda env or other install dir inside a cloud-synced folder (OneDrive etc.). Run tests from an environment located elsewhere, and prefer `PYTHONPATH=<repo root>` over `pip install -e` from a synced tree, because `-e` writes `*.egg-info` into it. Don't install packages without the maintainer's go-ahead.
- Optional dependencies: openpyxl is already in `install_requires`. xlsxwriter is **not** used (rejected in the Excel spec). papermill, nbconvert, nbformat, ipykernel and jinja2 go in a new extra, `notebook` (Part B). Nothing in `import oryxflow` or `import oryxflow.report` may import openpyxl, jinja2, matplotlib, papermill or nbconvert at module level.
- `python_requires='>=3.9'`: no `match`, no `X | Y` type syntax. House style is plain functions with docstrings and no type annotations.
- Test command form: `python -m pytest <file> -q` from the repo root.
- **Error assertions match the real call path.** `oryxflow.run()` (`oryxflow/__init__.py`, `run()`, `abort=True`) catches whatever a task's `run()` raises and re-raises `RuntimeError('Exception found running flow: ...') from result.first_exception`. So an error raised inside a task's `run()`/`save()` and reached through `oryxflow.run` is asserted as `with pytest.raises(RuntimeError) as e:` plus `assert isinstance(e.value.__cause__, <Type>)` and a match on `str(e.value.__cause__)`. Errors raised by a direct call (a target's `save`, `excel_write`, `report.*`) or at class definition or instantiation are asserted directly with `pytest.raises(<Type>, match=...)`.
- Formats are display-only. No option may change stored values or dtypes. `pd.read_excel` of a formatted file must equal the unformatted round-trip.
- The unformatted Excel save path (`formatted=False`, the default) stays byte-for-byte unchanged.
- Never hand-edit `tests/test_docs_*.py`. Regenerate them with `python scripts/build_docs.py`.

## Spec reconciliation (decided here, executors don't relitigate)

1. **One preset table, in `oryxflow/report.py`.** The two specs drafted conflicting tables: an `(excel, py)` tuple in `utils` versus a `Format(py, excel)` namedtuple in `report`, with different alias sets. This plan uses `report.Format(py, excel)` with the union of both alias sets, minus `bp_signed` (table in Task 1). `utils.excel_write` reads `FORMATS[name].excel`. `utils` does not re-export `FORMATS`.
2. **`resolve_formats` lives in `report.py`**, not `utils.py`. It gains `defaults=True`. The Excel writer uses the dtype defaults, and `report.table`/`format_frame` pass `defaults=False`. So glob keys (`'*_pct'`) work in both, and one `formats=` dict styles both outputs.
3. **One rule for a format value, used on both sides.** A value is one of:
   - a preset name;
   - a `str.format` spec (any string containing `{`);
   - a callable;
   - otherwise, a raw Excel number-format code.

   **A string that looks like a preset name but isn't one raises `KeyError` on both sides**, listing the valid names. "Looks like a preset name" means it matches `^[a-z][a-z_0-9]*$`, has no `{`, and is not in `FORMATS`. So the typo `'pctt'` raises in `fmt()` and in `excel_code()` alike, instead of writing an invalid Excel format that triggers Excel's "repaired content" prompt. All-digit raw codes like `'0'` or `'00000'`, and codes like `'General'`, `'@'`, `'0.000'` and `'#,##0'`, pass through as raw Excel codes.

   `report.excel_code(spec)` maps a value to its Excel code: preset → `.excel`, raw code → itself, py spec or callable → `None` (no Excel format). `report._py(spec)` maps it to its Python form: preset → `.py`, py spec or callable → itself, raw Excel code → `None`. Where the Python form is `None`, the value is shown as `str(value)`.
4. **Bad values never raise while formatting.** `fmt()` and the callables `table()` and `format_frame()` build fall back to `str(value)` on `ValueError`/`TypeError`. An example is a `'n/a'` string in a `pct` column. (Rule 3's `KeyError` is about a bad *spec*, not a bad value, and is raised before any value is formatted.)
5. **Every preset renders the same on both sides.** `thousands` has a Python form, a callable giving `'1,234k'`, matching its Excel code `#,##0,"k"`.

## Review Focus

1. **A title or notes shift the header row.** Freeze panes, the autofilter range, widths and every conditional-format range must follow the shifted header, and a reader must be able to get the data back with `pd.read_excel(..., skiprows=n)`. An off-by-one here puts the filter on a note row or the banding on the header. Pinned by `test_title_and_notes_shift_header` and `test_deliverable_end_to_end` (Task 3).
2. **Non-string column labels** (years `2025`, `2026` as headers, as pivot tables produce): glob matching, widths and header styling must use `str(col)` and not crash. Pinned by `test_non_string_columns` (Task 2).
3. **Nulls**: NaN in float columns, an all-NaT datetime column, `None` in object columns. Widths and dtype resolution must not raise, and nulls write as empty cells. Pinned by `test_nulls_and_all_nat_column` (Task 2).
4. **Sheet names that differ only by case** (`'Data'`, `'data'`): Excel treats them as one sheet, and openpyxl renames the second one silently. That breaks `ExcelPandasSheetsTarget.load(keys=...)`, so it must raise `ValueError`. Pinned by `test_sheet_names_case_insensitive_duplicate` (Task 2).
5. **Mixed-type columns** in a report table (a `pct` column holding `'n/a'`): the table should render that cell as is, not raise inside Styler rendering. Pinned by `test_fmt_bad_value_falls_back` (Task 1) and `test_table_mixed_types` (Task 6).
6. **Non-JSON parameter values** (`DateParameter`, `ListParameter`, `DictParameter`) must reach the notebook as plain JSON values (ISO date string, list, dict). Pinned by `test_parameter_types_reach_notebook` (Task 9).

---

## Part A: Formatted Excel (own release)

### Task 1: Shared format presets (`oryxflow/report.py`, part 1)

**Files:**
- Create: `oryxflow/report.py`
- Test: `tests/test_report_format.py`

**Interfaces:**
- Produces:
  - `Format = namedtuple('Format', ['py', 'excel'])`
  - `FORMATS: dict[str, Format]`
  - `NA_REP = '-'`
  - `excel_code(spec) -> str | None`
  - `_py(spec) -> str | callable | None`
  - `fmt(value, spec='num1', na_rep=NA_REP) -> str`
  - `resolve_formats(df, formats=None, defaults=True) -> dict` mapping `{column: spec}`

- [ ] **Step 0: Record the baseline**

Run: `python -m pytest tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py tests/test_workflowMulti2.py -q`
Expected: all pass. Write the count down (CLAUDE.md says 86); Tasks 4 and 9 compare against it.

- [ ] **Step 1: Write the failing tests** in `tests/test_report_format.py`

```python
import pandas as pd, pytest
import oryxflow.report as rpt

def test_presets_table():
    assert rpt.FORMATS['pct'] == rpt.Format('{:.1%}', '0.0%')
    assert rpt.FORMATS['float'].excel == '#,##0.00'
    assert set(rpt.FORMATS) == {'int', 'int_plain', 'int_signed', 'float', 'float4', 'num1', 'num2',
        'pct', 'pct0', 'pct2', 'pct_signed', 'multiple', 'thousands', 'date', 'datetime', 'text'}

def test_int_variants():
    assert rpt.fmt(39580, 'int_plain') == '39580' and rpt.excel_code('int_plain') == '0'   # ids: no separator
    assert (rpt.fmt(3, 'int_signed'), rpt.fmt(-2, 'int_signed'), rpt.fmt(0, 'int_signed')) == ('+3', '-2', '0')
    assert rpt.excel_code('int_signed') == '+0;-0;0'

def test_fmt_values():
    assert rpt.fmt(1234.5) == '1,234.5'                 # default spec num1
    assert rpt.fmt(0.0532, 'pct') == '5.3%'
    assert rpt.fmt(0.0532, 'pct_signed') == '+5.3%'
    assert rpt.fmt(1.5, 'multiple') == '1.50x'
    assert rpt.fmt(pd.Timestamp('2026-01-02'), 'date') == '2026-01-02'
    assert rpt.fmt(1234567, '{:,.0f}') == '1,234,567'   # raw py spec
    assert rpt.fmt(5, lambda v: f'<{v}>') == '<5>'      # callable
    assert rpt.fmt(1234, '0.000') == '1234'             # raw Excel code: no py form -> str
    assert rpt.fmt(float('nan'), 'pct') == '-' and rpt.fmt(None) == '-'
    with pytest.raises(KeyError, match='pct'):          # message lists valid names
        rpt.fmt(1, 'nope_preset')

def test_fmt_bad_value_falls_back():
    assert rpt.fmt('n/a', 'pct') == 'n/a'

def test_thousands_same_both_sides():
    assert rpt.fmt(1234000, 'thousands') == '1,234k'
    assert rpt.excel_code('thousands') == '#,##0,"k"'

def test_excel_code():
    assert rpt.excel_code('pct') == '0.0%'
    assert rpt.excel_code('0.000') == '0.000'           # raw Excel code passthrough
    assert rpt.excel_code('0') == '0' and rpt.excel_code('00000') == '00000'
    assert rpt.excel_code('General') == 'General' and rpt.excel_code('@') == '@'
    assert rpt.excel_code('{:.1f}') is None and rpt.excel_code(str) is None

def test_excel_code_rejects_unknown_preset():
    with pytest.raises(KeyError, match='pct'):          # lists valid names
        rpt.excel_code('pctt')

def test_resolve_formats():
    df = pd.DataFrame({'name': ['a'], 'year': [2026], 'value': [1.5], 'ret_pct': [0.1],
                       'when': pd.to_datetime(['2026-01-01']), 'stamp': pd.to_datetime(['2026-01-01 09:30'])})
    assert rpt.resolve_formats(df) == {'value': 'float', 'ret_pct': 'float', 'when': 'date', 'stamp': 'datetime'}
    r = rpt.resolve_formats(df, {'ret_*': 'pct', 'value': '0.000', 'year': 'int'})
    assert (r['ret_pct'], r['value'], r['year']) == ('pct', '0.000', 'int')
    r = rpt.resolve_formats(df, {'*': 'text', 'value': 'pct'})   # exact beats glob
    assert r['value'] == 'pct' and r['name'] == 'text'
    assert rpt.resolve_formats(df, False) == {}
    assert rpt.resolve_formats(df, {'ret_*': 'pct'}, defaults=False) == {'ret_pct': 'pct'}

def test_import_is_light():
    import subprocess, sys
    code = 'import oryxflow.report, sys; print(any(m in sys.modules for m in ("openpyxl","jinja2","matplotlib","papermill")))'
    assert subprocess.run([sys.executable, '-c', code], capture_output=True, text=True).stdout.strip() == 'False'
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_report_format.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'oryxflow.report'`.

- [ ] **Step 3: Implement `oryxflow/report.py` (presets section)**

The module docstring follows the notebook spec, §2, trimmed to what this part ships. Module-level imports: `fnmatch`, `re`, `namedtuple`, `pandas as pd`. The table is exact copy:

| name | py | excel |
| --- | --- | --- |
| `int` | `{:,.0f}` | `#,##0` |
| `int_plain` | `{:.0f}` | `0` (IDs and codes: no thousands separator) |
| `int_signed` | a function: `'{:+.0f}'.format(v)`, but `0` shows bare as `'0'` | `+0;-0;0` (rank moves) |
| `float` | `{:,.2f}` | `#,##0.00` |
| `float4` | `{:.4f}` | `0.0000` |
| `num1` / `num2` | `{:,.1f}` / `{:,.2f}` | `#,##0.0` / `#,##0.00` |
| `pct` / `pct0` / `pct2` | `{:.1%}` / `{:.0%}` / `{:.2%}` | `0.0%` / `0%` / `0.00%` |
| `pct_signed` | `{:+.1%}` | `+0.0%;-0.0%;0.0%` |
| `multiple` | `{:.2f}x` | `0.00"x"` |
| `thousands` | a function: `'{:,.0f}k'.format(v / 1000)` | `#,##0,"k"` |
| `date` | `{:%Y-%m-%d}` | `yyyy-mm-dd` |
| `datetime` | `{:%Y-%m-%d %H:%M}` | `yyyy-mm-dd hh:mm` |
| `text` | `{}` | `@` |

- `excel_code`, `_py` and `fmt` follow reconciliation rule 3. They share one private check, `_check_spec(spec)`, using the regex `^[a-z][a-z_0-9]*$`. `fmt` then applies rule 4.
- `resolve_formats` copies the algorithm in the Excel spec (Implementation step 1), with these changes: glob matching uses `fnmatch.fnmatchcase(str(col), key)` on string keys containing `*?[`; the dtype branch runs only when `defaults`; and an empty datetime column resolves to `'date'`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_report_format.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/report.py tests/test_report_format.py docs/todo/20261018-output-formats-impl-plan.md
git commit -m "feat(report): shared display-format presets for notebooks and Excel"
```

---

### Task 2: Formatted Excel writer (`oryxflow.utils.excel_write`)

**Files:**
- Modify: `oryxflow/utils.py`, adding an "Excel output" section at the end of the file
- Test: `tests/test_utils_excel.py` (new)

**Interfaces:**
- Consumes: `oryxflow.report.resolve_formats`, `excel_code`, `fmt`, `_py` (Task 1).
- Produces:
  - `excel_write(path, data, **options) -> path`. `data` is a DataFrame (sheet `'Sheet1'`) or `{sheet_name: df_or_spec}`, where a spec is a dict `{'df': df, **per_sheet_options}`. Options passed to `excel_write` apply to every sheet; a spec's own keys override them for that sheet only. Unknown option names, in either place, raise `TypeError`. This replaces the spec's `sheet_options`.
  - `_excel_format_sheet(ws, df, row_header, index=False, formats=None, filters=True, freeze=True, widths='auto', width_min=8, width_max=60, header_style=True, **styling) -> None`. `row_header` is the 1-based row of the header (1 in this task; Task 3 shifts it). `**styling` is empty in this task; Task 3 fills it.
  - `freeze`: `True` freezes the header row, plus the index column(s) when `index=True`. An int `n` freezes the header row and the first `n` columns. `False` freezes nothing.
  - Private: `_EXCEL_DEFAULTS` (the option names and defaults, the single list Task 3 extends), `_EXCEL_WIDTH_SAMPLE = 1000`, `_EXCEL_FILTER_ARROW = 3`, `_excel_check_sheet_names(names)`, `_excel_flatten_columns(df)`, `_excel_widths(...)`, `_excel_sheet_specs(data, options) -> [(name, df, opts)]`

- [ ] **Step 1: Write the failing tests** in `tests/test_utils_excel.py`

The fixture frame comes from the Excel spec (Implementation step 5): `name`, `year`, `value`, `ret_pct`, `when`, `stamp`. Read results back with `openpyxl.load_workbook` and use `tmp_path` throughout. Tests and their assertions:

- `test_defaults`: `ws.auto_filter.ref == 'A1:F3'`, `ws.freeze_panes == 'A2'`, `ws['A1'].font.b`, `ws['A1'].fill.fgColor.rgb.endswith('D3D9E3')`, `ws['A1'].alignment.wrap_text`.
- `test_number_formats_dtype_defaults`: in row 2, `value` is `'#,##0.00'`, `when` is `'yyyy-mm-dd'`, `stamp` is `'yyyy-mm-dd hh:mm'`, and `year` and `name` are `'General'`.
- `test_formats_precedence`: with `{'ret_*': 'pct', 'value': '0.000', 'year': 'int'}`, the cells get `'0.0%'`, `'0.000'` and `'#,##0'`. With `{'value': '{:.1f}'}` (a py spec), `value` gets `'General'`.
- `test_formats_typo_raises`: `excel_write(p, df, formats={'ret_*': 'pctt'})` raises `KeyError`, and no file is left at `p`.
- `test_formats_false`: the `value` and `ret_pct` cells are `'General'`.
- `test_round_trip`: `pd.read_excel(formatted)` passes `assert_frame_equal` against `pd.read_excel` of a plain `df.to_excel(index=False)` file, and `1234567.891` is intact.
- `test_widths`: a `'x' * 200` column gets 60; a one-character column with a one-character header gets 8; `widths=15` gives 15 everywhere; `widths={'name': 30}` overrides only `name`.
- `test_numeric_width_from_format`: a float column of `0.047196123456` with `formats={'r': 'pct'}` and header `'r'` gets a width of at most 12, sized from the rendered `'4.7%'` and not from the float's repr.
- `test_filter_arrow_width`: a text column with a 10-character header and 1-character values is 3 wider with `filters=True` than with `filters=False`.
- `test_freeze_int`: `freeze=2` gives `freeze_panes == 'C2'`; `freeze=False` gives `None`.
- `test_sheet_spec_overrides`: `excel_write(p, {'a': df, 'b': {'df': df, 'filters': False, 'freeze': 1}}, formats={'ret_*': 'pct'})`. Sheet `a` has a filter and `'A2'`; sheet `b` has no filter, `'B2'`, and still `'0.0%'` on `ret_pct` (the global option carries through). `{'b': {'df': df, 'colour': 1}}` raises `TypeError`, and so does a spec dict without `'df'`.
- `test_width_sample_capped`: column `'c'` holds 1000 values `'a'` followed by one `'x' * 200`, so the long value is row 1001. The column's width is 8, because only the first 1000 rows are sampled.
- `test_index_true`: `df.set_index('name')` with `index=True` gives `freeze_panes == 'B2'` and `auto_filter.ref == 'A1:F3'`.
- `test_multi_sheet`: with `{'a': df, 'b': df}`, both sheets have a filter and `freeze_panes == 'A2'`. With `filters=False`, neither does.
- `test_bad_sheet_name`: a 32-character name and `'a/b'` raise `ValueError`; `excel_write(p, df, colour=1)` raises `TypeError`.
- `test_sheet_names_case_insensitive_duplicate`: `{'Data': df, 'data': df}` raises `ValueError` matching `'data'`.
- `test_multiindex_columns_flattened`: two-level columns give header cells `'a / x'` and `'a / y'`.
- `test_empty_frame`: `pd.DataFrame({'a': []})` gives `auto_filter.ref == 'A1:A1'`.
- `test_non_string_columns`: `pd.DataFrame({2025: [1.5], 2026: [2.5], 'x': ['a']})` writes without error; `formats={'202*': 'pct'}` gives `'0.0%'` on both year columns, and their widths are at least 8.
- `test_nulls_and_all_nat_column`: a frame with `[1.5, nan]` floats, an all-NaT datetime column and `[None, 'b']` writes; the NaN cell's value is `None`, the NaT column's format is `'yyyy-mm-dd'`, and widths are within [8, 60].
- `test_does_not_mutate_input`: after a write with MultiIndex columns, the caller's `df.columns` is still a MultiIndex.
- `test_import_cost`: a subprocess running `import oryxflow` does not have `openpyxl` in `sys.modules`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_utils_excel.py -q`
Expected: FAIL with `AttributeError: module 'oryxflow.utils' has no attribute 'excel_write'`.

- [ ] **Step 3: Implement the Excel section in `oryxflow/utils.py`**

Follow the Excel spec (Implementation step 1) for the defaults, header style, freeze, filter, width formula and `UPath` handling, with these changes:

- Skip the spec's conditional formatting here (Task 3 does it), and skip hyperlinks and `sheet_options` (replaced by sheet specs; see Deferred).
- One pass: build the workbook with openpyxl, style every sheet, then save once. Never reopen a saved file to add formatting.
- Import pandas, openpyxl and `oryxflow.report` inside the functions.
- Widths: a column with a format that has a Python form is sized from `report.fmt(v, spec)` of the sampled values, so numbers size to what is displayed. The header's contribution is its length plus `_EXCEL_FILTER_ARROW` when filters are on, because the dropdown arrow covers about three characters of the header cell. The result is then clamped to `[width_min, width_max]`.
- Resolve all formats, mapping them through `report.excel_code(spec)` and skipping `None`, **before** creating the workbook. That way a typo `KeyError` leaves no file behind.
- Width samples come from the header plus the first `_EXCEL_WIDTH_SAMPLE` (1000) rows only. Render them with `report.fmt(v, spec)` when `report._py(spec)` is not None, else `str(v)`.
- `_excel_check_sheet_names(names)` checks every name (non-empty, at most 31 characters, none of `[]:*?/\`) and also rejects duplicates under `str(name).lower()`.
- `_excel_flatten_columns` returns a copy and never mutates.
- Unknown option names raise `TypeError` (checked against `_EXCEL_DEFAULTS`).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_utils_excel.py tests/test_report_format.py -q`
Expected: all pass (22 + 9).

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/utils.py tests/test_utils_excel.py
git commit -m "feat(utils): excel_write formatted Excel output"
```

---

### Task 3: Deliverable styling (title, notes, highlights, layout)

Client deliverable workbooks need more than headers and filters: a title block, reading notes, a red/yellow/green rating column, a signed-change colour scale, banded rows, wrapped prose columns, visual column groups, and ID codes that must not turn into numbers. This task adds those as `excel_write` options. Each one is also valid in a sheet spec.

**Files:**
- Modify: `oryxflow/utils.py` (the Excel section from Task 2)
- Test: `tests/test_utils_excel.py` (append)

**Interfaces:**
- Consumes: `excel_write`, `_excel_format_sheet`, `_EXCEL_DEFAULTS` (Task 2); `report.FORMATS['int_plain']` (Task 1).
- Produces new options, added to `_EXCEL_DEFAULTS`:
  - `title=None` (str) and `notes=()` (list of str). The title goes in row 1, bold at 14pt. Each note gets its own row below it, italic at 9pt and wrapped. Title and note cells are merged across `min(n_cols, 6)` and `min(n_cols, 12)` columns, and not merged when the sheet has one column. One blank spacer row follows. The header row is therefore `1 + len(notes) + 2` when a title or notes are given, else 1. Freeze, filter, widths and every rule below use that shifted row.
  - `highlight={}`: `{col: {value: fill}}`. Each pair is an openpyxl `CellIsRule(operator='equal', formula=['"<value>"'])` over the column's data cells, with a fill and font colour. `fill` is a name from `_EXCEL_FILLS`, or a dict `{'bg': 'RRGGBB', 'font': 'RRGGBB'}`. Any other name raises `KeyError` listing the valid ones, before the workbook is created.
  - `_EXCEL_FILLS = {'green': ('C6EFCE', '006100'), 'yellow': ('FFEB9C', '9C6500'), 'red': ('FFC7CE', '9C0006'), 'grey': ('E7E6E6', '3F3F3F')}`, as `(bg, font)` pairs.
  - `colorscale=()`: columns given a 3-colour scale (`ColorScaleRule`): min `F8696B`, mid `num` 0 `FFFFFF`, max `63BE7B`.
  - `banded=False`: shading `F2F5FA` on alternate data rows via a `FormulaRule(formula=['MOD(ROW(),2)=0'])` over the whole data range. It is added **after** the highlight and colour-scale rules so that they win where they overlap.
  - `wrap_columns=()`: the cells wrap, the column width is `width_max`, and **every** data cell on the sheet gets `vertical='top'` when at least one wrap column exists. Otherwise the short cells sit at the bottom of a tall row and read as belonging to the row below.
  - `group_starts=()`: a medium left border on that column's header and data cells.
  - `id_columns=()`: values are written as strings (`str(v)`, with NaN left empty), right-aligned, with number format `'@'`. Excel's "number stored as text" warning is **not** suppressed: openpyxl has an `IgnoredError` class but its worksheet writer never serializes it (checked against openpyxl 3.1.5), so there is nothing to call. Note this in a code comment.
- Every column name in these options must exist in the frame (after `_excel_flatten_columns`, compared via `str(col)`), else `KeyError` naming the sheet and column, raised before the workbook is created.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_utils_excel.py`)

Each test reads the file back with `openpyxl.load_workbook`. The test frame is Task 2's fixture plus `rating` (`['Green', 'Red']`), `rank_chg` (`[3, -2]`), `fips` (`[39580, 6037]`) and `note` (long text).

- `test_title_and_notes_shift_header`: `title='Report - 2026Q2', notes=['a', 'b']`. `ws['A1'].value == 'Report - 2026Q2'` and it is bold; `ws['A2'].value == 'a'`, `ws['A3'].value == 'b'`; the header is in row 5 (`ws['A5'].value == 'name'`); the first data value is in row 6; `ws.freeze_panes == 'A6'`; `ws.auto_filter.ref` starts at `A5`; `'A1:F1'` is in `ws.merged_cells`. With `freeze=2` the freeze is `'C6'`. `pd.read_excel(p, skiprows=4)` equals the plain round-trip.
- `test_title_one_column_not_merged`: a single-column frame with a title has no merged cells.
- `test_highlight_named_and_raw`: `highlight={'rating': {'Green': 'green', 'Red': {'bg': '112233', 'font': 'FFFFFF'}}}`. `ws.conditional_formatting` has two `cellIs` rules over the `rating` data range; their formulas are `['"Green"']` and `['"Red"']`; the first rule's `dxf.fill.bgColor.rgb` ends with `C6EFCE` and its `dxf.font.color.rgb` ends with `006100`.
- `test_highlight_bad_fill_raises`: `{'rating': {'Green': 'grean'}}` raises `KeyError` matching `'green'`, and no file exists.
- `test_unknown_column_raises`: `colorscale=['nope']` raises `KeyError` matching `'nope'`.
- `test_colorscale`: `colorscale=['rank_chg']` gives a `colorScale` rule on that column with a mid value of 0.
- `test_banded_after_value_rules`: with both `highlight` and `banded=True`, the banding `expression` rule has a larger `priority` number (lower precedence) than the highlight rules. `banded=False` adds no expression rule.
- `test_wrap_columns_top_align`: `wrap_columns=['note']` gives `note` cells `wrap_text` and the column width `width_max`, and a non-wrap cell in the same row has `alignment.vertical == 'top'`. Without wrap columns, that cell's vertical alignment is not `'top'`.
- `test_group_starts_border`: `group_starts=['value']` gives the `value` header and data cells `border.left.style == 'medium'`, and the `year` cells no left border.
- `test_id_columns_text`: `id_columns=['fips']` gives `data_type == 's'`, value `'39580'`, `alignment.horizontal == 'right'`, number format `'@'`.
- `test_deliverable_end_to_end`: two sheets in the shape of a real deliverable workbook:

```python
sheets = {
    'Retail': {'df': df, 'title': 'Market Rankings - Retail - 2026Q2',
               'notes': ['Green = top quintile, Red = bottom quintile'],
               'formats': {'ret_*': 'pct', 'rank_chg': 'int_signed'},
               'highlight': {'rating': {'Green': 'green', 'Red': 'red'}},
               'colorscale': ['rank_chg'], 'id_columns': ['fips'],
               'group_starts': ['value'], 'freeze': 2, 'banded': True},
    'Notes': {'df': pd.DataFrame({'item': ['Generated'], 'detail': ['2026-10-18']}),
              'title': 'Notes', 'filters': False, 'freeze': 1, 'wrap_columns': ['detail']},
}
utils.excel_write(p, sheets)
```

  Asserts: the tab order is `['Retail', 'Notes']`; Retail's header is in row 4 and its `freeze_panes == 'C5'`; `ret_pct` is `'0.0%'` and `rank_chg` is `'+0;-0;0'`; `fips` is text; there are 2 `cellIs` rules, a `colorScale` and an `expression` rule; Notes has no autofilter and `freeze_panes == 'B4'`. `pd.read_excel(p, sheet_name='Retail', skiprows=3)` keeps the values of `ret_pct` and `rank_chg` (the `fips` column comes back as strings, as intended).

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_utils_excel.py -q -k "title or highlight or unknown_column or colorscale or banded or wrap or group or id_columns or deliverable"`
Expected: FAIL with `TypeError` for the unknown option `title` (and the others), from Task 2's option check.

- [ ] **Step 3: Implement the options**

Add the names to `_EXCEL_DEFAULTS` and the rules to `_excel_format_sheet`, using `openpyxl.formatting.rule` (`CellIsRule`, `ColorScaleRule`, `FormulaRule`) and `openpyxl.styles` (`PatternFill`, `Font`, `Border`, `Side`, `Alignment`).
- The DataFrame is written starting at the shifted header row, so `excel_write`'s cell-writing loop takes `row_header` from `1 + len(notes) + 2 if (title or notes) else 1`.
- Validate fills and column names together with the format resolution, before the workbook is created (same rule as Task 2's typo check).
- Add the rules in this order: highlight, colour scale, banding.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_utils_excel.py tests/test_report_format.py -q`
Expected: all pass (33 + 9).

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/utils.py tests/test_utils_excel.py
git commit -m "feat(utils): excel_write titles, notes, highlights and layout options"
```

---

### Task 4: `formatted=True` on the Excel targets

**Files:**
- Modify: `oryxflow/targets/__init__.py`, specifically `ExcelPandasTarget.save` (around line 206) and `ExcelPandasSheetsTarget.save` (around line 254)
- Test: `tests/test_utils_excel.py` (append)

**Interfaces:**
- Consumes: `oryxflow.utils.excel_write` (Task 2).
- Produces:
  - `ExcelPandasTarget.save(df, formatted=False, **kwargs)`
  - `ExcelPandasSheetsTarget.save(data, formatted=False, **kwargs)`
  - With `formatted=True` the kwargs are `excel_write` options (Tasks 2 and 3), and a `{sheet: spec}` dict is passed through as is, so per-sheet titles and highlights work from a task. `TaskExcelPandas.save` and `TaskData.save` already forward kwargs; `tasks/__init__.py` does not change.

- [ ] **Step 1: Write the failing tests**

- `test_task_formatted_save`: set the data dir with `oryxflow.set_dir(tmp_path / 'data')` and restore `settings.dir`/`dirpath` in a `finally`. A `TaskExcelPandas` with `persists = ['summary', 'detail']` calls `self.save({'summary': df, 'detail': df}, formatted=True, formats={'ret_*': 'pct'})`. After `oryxflow.run`, `outputLoad(as_dict=True)['summary']` equals the plain round-trip, and the file's `summary` sheet has `auto_filter.ref` set and `'0.0%'` on `ret_pct`.
- `test_task_single_formatted_save`: `TaskExcelPandasSingle` with `self.save(df, formatted=True)` writes sheet `'Sheet1'` with a filter.
- `test_task_formatted_typo_raises`: a task whose `run()` calls `self.save(df, formatted=True, formats={'value': 'pctt'})`. `oryxflow.run(...)` raises `RuntimeError` whose `__cause__` is a `KeyError` (see Global Constraints, error assertions).
- `test_unformatted_path_unchanged`: `ExcelPandasSheetsTarget(tmp_path/'x.xlsx').save({'a': df})` has `auto_filter.ref is None` and `freeze_panes is None`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_utils_excel.py -q -k "formatted or unchanged"`
Expected: the three `formatted` tests FAIL. Through `oryxflow.run`, the failure surfaces as `RuntimeError: Exception found running flow: ...` chained from `TypeError: ... unexpected keyword argument 'formatted'`. In `test_task_formatted_typo_raises` the cause is that `TypeError`, not the expected `KeyError`.

- [ ] **Step 3: Add the `formatted` switch to both `save` methods**

Each `save` delegates to `oryxflow.utils.excel_write(self.path, data_or_{'Sheet1': df}, **kwargs)` and returns `self.path`. The `else` branch keeps the existing body. While there, fix the docstrings that say `pd.read_csv`/`df.to_csv` in both Excel targets.

- [ ] **Step 4: Run the tests and the baseline, and confirm they pass**

Run: `python -m pytest tests/test_utils_excel.py tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py tests/test_workflowMulti2.py -q`
Expected: all pass, and the baseline count matches Task 1 Step 0.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/targets/__init__.py tests/test_utils_excel.py
git commit -m "feat(targets): formatted=True on Excel saves"
```

---

### Task 5: Part A docs, changelog, CLAUDE.md

**Files:**
- Modify:
  - `docs/docs/targets.md`: add the "Excel files people will open" section
  - `CHANGELOG.md`: add bullets under `## [Unreleased]` → `### Added`
  - `CLAUDE.md`: Layout lines for `report.py` and the `utils.py` Excel section
  - `docs/todo/20261018-utils-excel-writer.md`: append `## Implementation notes (divergences from the plan as built)`. Record reconciliation rules 1–5, the cuts from Deferred that touch this spec, and anything else that changed (repo CLAUDE.md convention).
- Regenerate: `tests/test_docs_targets.py`

**Interfaces:**
- Consumes: the public names from Tasks 1–4, exactly as defined there.

- [ ] **Step 1: Write the section**

Write for data scientists: what they get and what to type, with no internals. Cover:
- the alias table from Task 1;
- `self.save({...}, formatted=True, formats={'*_pct': 'pct'})` and `oryxflow.utils.excel_write('report.xlsx', {'summary': df})`;
- that a misspelled format name raises instead of writing a broken file;
- a deliverable example: a `{sheet: spec}` dict with a title, a note, a rating `highlight`, a `colorscale` on a change column, an `id_columns` code and `freeze=2` (the shape of Task 3's end-to-end test), plus one line saying ID columns stay text so leading zeros survive;
- this one sentence: "Formatting styles each cell, so very large frames (hundreds of thousands of rows) write noticeably slower than an unformatted save; column widths are sized from the first 1,000 rows."

The snippet must be runnable: define its own small `df`, and use a task class name that is unique on the page.

- [ ] **Step 2: Regenerate and check**

Run: `python scripts/build_docs.py`
Expected: it regenerates `tests/test_docs_targets.py` and those tests pass.

Run: `python scripts/build_docs.py --check`
Expected: exit 0.

- [ ] **Step 3: Sanitization grep**

Run: `git diff --cached --name-only | xargs grep -nE '[A-Za-z]:\\\\|/Users/|OneDrive'` (also run it over untracked new files).
Expected: no output. Then manually scan the new prose for client, private-project and data-vendor names.

- [ ] **Step 4: Commit** (only if authorized)

```bash
git add docs/docs/targets.md CHANGELOG.md CLAUDE.md tests/test_docs_targets.py docs/todo/20261018-utils-excel-writer.md
git commit -m "docs: formatted Excel output"
```

- [ ] **Step 5: Hand-check (by the executor, not delegated)**

Excel spec, Verification 5: open a formatted workbook in Excel or LibreOffice and confirm there is no "repaired content" prompt. Part A is then ready to release.

---

## Part B: Notebook reports (later release)

### Task 6: Report tables (`oryxflow/report.py`, part 2)

**Files:**
- Modify: `oryxflow/report.py`
- Test: `tests/test_report_format.py` (append)

**Interfaces:**
- Consumes: `resolve_formats(..., defaults=False)`, `_py`, `fmt` (Task 1).
- Produces:
  - `format_frame(df, formats=None, na_rep=NA_REP) -> DataFrame` (strings, a copy)
  - `table(df, formats=None, index='auto', rename=None, columns=None, caption=None, na_rep=NA_REP) -> pandas.io.formats.style.Styler`

- [ ] **Step 1: Write the failing tests**

Styler tests start with `pytest.importorskip('jinja2')`.

- `test_format_frame`: given `{'r': [0.05, nan], 'n': [1234, 5]}` and `{'r': 'pct', 'n': 'int'}`, it returns `[['5.0%', '1,234'], ['-', '5']]` and leaves the input frame unchanged.
- `test_table_hides_range_index`: `'<th id="T_' ` row headers are absent from `table(df).to_html()`. They stay present for `df.rename_axis('sector')` with a string index, and for `index=True`.
- `test_table_rename_then_format`: with `rename={'r': 'Return'}` and `formats={'Return': 'pct'}`, `'5.0%'` appears in the html.
- `test_table_glob_formats`: `formats={'*_pct': 'pct'}` formats `ret_pct`.
- `test_table_same_formats_as_excel`: `formats={'n': 'thousands'}` on `{'n': [1234000]}` puts `'1,234k'` in the html.
- `test_table_mixed_types`: `{'r': [0.05, 'n/a']}` with `{'r': 'pct'}` renders `'5.0%'` and `'n/a'` without raising.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_report_format.py -q`
Expected: the new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement the functions**

Follow the notebook spec, decision 8 and Implementation step 2 (`table` only; skip `setup`, `PALETTE`, `percent_axis` and `links`, see Deferred), with these changes:

- Resolve formats with `resolve_formats(df_renamed, formats, defaults=False)`.
- Each Styler formatter is `lambda v, s=spec: fmt(v, s, na_rep)`, which gives reconciliation rule 4.
- `ImportError` without jinja2 points to `format_frame` and to `oryxflow[notebook]`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_report_format.py -q`
Expected: all pass. Tests needing jinja2 are skipped, not failed, when it is missing.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/report.py tests/test_report_format.py
git commit -m "feat(report): styled report tables and format_frame"
```

---

### Task 7: Input bridge (`report.inputs_of` / `report.inputLoad`)

**Files:**
- Modify: `oryxflow/report.py`
- Test: `tests/test_report_inputs.py` (new; core dependencies only, no papermill)

**Interfaces:**
- Produces:
  - `inputs_of(task) -> dict`, a JSON-safe dict shaped `{name: {'target': 'mod.Qual', 'path': str} | {'persists': {k: {...}}}}`. A single unnamed dep is keyed `'0'`, and list deps are keyed `'0'`, `'1'`, …
  - `inputLoad(spec=None, task=None, keys=None)`. When `spec` is None it uses the `oryxflow_inputs` variable that Task 9 injects into the notebook, so a template only writes `rpt.inputLoad(task='a')`. Lookup order: `IPython.get_ipython().user_ns` when IPython is importable and `get_ipython()` is not None, else `sys.modules['__main__']`.

- [ ] **Step 1: Write the failing tests**

Use `oryxflow.set_dir(tmp_path/'data')` and restore it afterwards. The upstream `A(TaskPqPandas)` saves `{'a': [1, 2]}`; `B(TaskPqPandas)` has `persists = ['train', 'test']`; `C(TaskCache)` is the in-memory case.

- `test_inputs_of_named`: for `requires({'a': A(), 'b': B()})`, `json.loads(json.dumps(spec)) == spec`; `spec['a']['target'] == 'oryxflow.targets.PqPandasTarget'`; `set(spec['b']['persists']) == {'train', 'test'}`.
- `test_inputs_of_list_keys_are_strings`: `requires(A(), B())` gives `set(spec) == {'0', '1'}`.
- `test_inputLoad_selectors`: `inputLoad(spec, task='a')` equals A's frame; `task=1` on a list spec works; `inputLoad(spec, task='b', keys='train')` returns a frame; `keys=['test', 'train']` returns a list in that order; `inputLoad(spec)` with several deps returns a dict of all of them.
- `test_inputLoad_uses_injected_inputs`: `monkeypatch.setattr(sys.modules['__main__'], 'oryxflow_inputs', spec, raising=False)`, then `rpt.inputLoad(task='a')` equals A's frame.
- `test_inputLoad_none_message`: with no `oryxflow_inputs` anywhere (`monkeypatch.delattr(sys.modules['__main__'], 'oryxflow_inputs', raising=False)`), `inputLoad()` raises `ValueError` matching `inputs_of`.
- `test_inputs_of_cache_dep_raises`: a `TaskCache` dep raises `TypeError` matching both the dep key and `TaskPqPandas`.
- `test_inputs_of_cloud_raises`: with `monkeypatch.setattr(oryxflow.settings, 'cloud_fs_enabled', True)`, it raises `NotImplementedError`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_report_inputs.py -q`
Expected: FAIL with `AttributeError: ... 'inputs_of'`.

- [ ] **Step 3: Implement the functions**

Follow the notebook spec, Implementation step 2, "inputs_of" and "inputLoad" notes, plus the `spec=None` lookup above. Import `oryxflow.settings`, `oryxflow.targets.CacheTarget` and IPython lazily inside the functions.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_report_inputs.py tests/test_report_format.py -q`
Expected: all pass.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/report.py tests/test_report_inputs.py
git commit -m "feat(report): inputs_of/inputLoad bridge for report notebooks"
```

---

### Task 8: `notebook` extra and `NotebookTarget`

**Files:**
- Modify: `setup.py` (`extras_require`)
- Create: `oryxflow/targets/notebook.py`
- Test: `tests/test_notebook_target.py` (new)

**Interfaces:**
- Produces:
  - extra `'notebook': ['papermill', 'nbconvert', 'nbformat', 'ipykernel', 'jinja2']`
  - `NotebookTarget(_LocalPathTarget)` with:
    - `.html_path` (the `.ipynb` path with suffix `.html`)
    - `exists() -> bool` (both files)
    - `load(cached=False, **kwargs) -> pathlib.Path` (the html; `RuntimeError` if incomplete)
    - `save(executed_path, html_template='classic', include_code=False) -> path` (export first, then `replace` into place)
    - `invalidate() -> bool` (removes both)

- [ ] **Step 1: Write the failing tests**

They start with `pytest.importorskip('nbconvert')` and `pytest.importorskip('nbformat')`. Build a notebook with `nbformat.v4.new_notebook()`, holding one code cell (`print('HELLO')`) with a stream output `'HELLO\n'`, and write it to `tmp_path/'run.ipynb'`.

- `test_save_exports_html_without_code`: after `t.save(run)`, `t.exists()` holds and the run file is gone. The html contains `'HELLO'` but not `"print('HELLO')"`, and has no `In&nbsp;[` prompt.
- `test_include_code`: `include_code=True` puts the source in the html.
- `test_exists_needs_both`: deleting the html makes `exists()` False.
- `test_invalidate_removes_both`.
- `test_load_returns_html_path`, plus `load()` on an incomplete target raises `RuntimeError`.
- `test_export_failure_leaves_nothing`: with `html_template='no-such-template'`, the save raises, and neither final file exists.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_notebook_target.py -q`
Expected: FAIL with `ModuleNotFoundError: oryxflow.targets.notebook`, or SKIPPED if nbconvert is missing. If they skip, stop and ask the maintainer to provide an environment with the extra (see Global Constraints).

- [ ] **Step 3: Implement**

Follow the notebook spec, Implementation step 3. Import nbconvert and nbformat inside `save()` only. Add the extra to `setup.py`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_notebook_target.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add setup.py oryxflow/targets/notebook.py tests/test_notebook_target.py
git commit -m "feat(targets): NotebookTarget (.ipynb + code-free .html) and notebook extra"
```

---

### Task 9: `TaskNotebook`

**Files:**
- Create: `oryxflow/tasks/notebook.py`
- Test: `tests/test_notebook_report.py` (new)

**Interfaces:**
- Consumes:
  - `NotebookTarget` (Task 8)
  - `oryxflow.report.inputs_of` (Task 7)
  - `codehash.task_code_hash(task)`
  - `settings.code_version_auto`
  - `core._resolve_code_version`, which calls a method-form `code_version` and hashes its JSON
- Produces:
  - `TaskNotebook(TaskData)`, with class attributes:
    - `notebook` (required)
    - `kernel_name = 'python3'`
    - `html_template = 'classic'`
    - `html_include_code = False`
    - `target_class = NotebookTarget`
    - `target_ext = 'ipynb'`
  - Hook: `notebook_parameters(self) -> dict`
  - `code_version(self) -> dict` with keys `template` (a digest of the template's cell sources, computed on each call) and `python`
  - Injected reserved names: `oryxflow_inputs`, `oryxflow_task_id`, `oryxflow_task_family`

- [ ] **Step 1: Write the failing tests**

The module starts with `pytest.importorskip('papermill')` and `pytest.importorskip('nbconvert')`. The setup comes from the notebook spec, Implementation step 6: the template is built programmatically in `tmp_path`, using `monkeypatch.chdir(tmp_path)` and `oryxflow.set_dir(tmp_path/'data')`, with the upstream `TaskPqPandas` `{'a': [1, 2]}`. The template's data cell uses `rpt.inputLoad(task=...)` with no spec argument. These are the spec's cases, minus `test_published_copy` (see Deferred):

1. `test_runs_and_exports`
2. `test_params_injected`
3. `test_cached_second_run`
4. `test_template_edit_reruns`
5. `test_output_only_edit_does_not_rerun`
6. `test_upstream_change_reruns`
7. `test_failure_is_atomic`
8. `test_guards`
9. `test_check_inputs_quiet`

Their assertions are as listed in the spec, using `RunResult.did_run(Report)` for the rerun checks. Any expected error raised inside the task's `run()` follows the Global Constraints rule: `RuntimeError` from `oryxflow.run`, with the spec's error type and message on `__cause__`. Plus:

10. `test_parameter_types_reach_notebook`: `d = oryxflow.DateParameter(default=datetime.date(2026, 1, 2))`, `l = oryxflow.ListParameter(default=[1, 2])`. The template prints `repr(d), repr(l)`, and the html contains `'2026-01-02'` and `[1, 2]`.
11. `test_persists_rejected`: a subclass with `persists = ['report']`. `oryxflow.run(...)` raises `RuntimeError` whose `__cause__` is a `ValueError` matching `exactly one output`.
12. `test_missing_template`: `notebook = 'nope.ipynb'`. `oryxflow.run(...)` raises `RuntimeError` whose `__cause__` is a `FileNotFoundError` naming the path.

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_notebook_report.py -q`
Expected: FAIL with `ModuleNotFoundError: oryxflow.tasks.notebook`.

- [ ] **Step 3: Implement `oryxflow/tasks/notebook.py`**

Follow the notebook spec: decisions 2–7, plus Implementation step 4 and the `run()` order, without the published-copy step:

- The import-guard message is `"oryxflow.tasks.notebook needs the 'notebook' extra: pip install oryxflow[notebook]"`.
- Parameters go through `json.loads(json.dumps(v, default=str))`, with `EnumParameter` values converted to `.name` first.
- A parameter key starting with `oryxflow_` raises `ValueError`.
- A template with no cell tagged `parameters` raises `ValueError`.
- Call `pm.execute_notebook(..., cwd=os.getcwd(), kernel_name=self.kernel_name, progress_bar=False, log_output=False, request_save_on_cell_execute=False)`.
- A `.running.ipynb` that fails is renamed to `.failed.ipynb` and the error re-raised.

- [ ] **Step 4: Run the tests and confirm they pass, plus the full suite**

Run: `python -m pytest tests/test_notebook_report.py -q`
Expected: 12 passed.

Run: `python -m pytest -q`
Expected: no new failures compared with a pre-change run.

Run: `python -c "import oryxflow, oryxflow.report"` in an env **without** the extra.
Expected: succeeds. `python -c "import oryxflow.tasks.notebook"` there fails with the `pip install oryxflow[notebook]` message.

- [ ] **Step 5: Commit** (only if authorized)

```bash
git add oryxflow/tasks/notebook.py tests/test_notebook_report.py
git commit -m "feat(tasks): TaskNotebook runs parameterized report notebooks with papermill"
```

---

### Task 10: Part B docs, changelog, CLAUDE.md

**Files:**
- Modify:
  - `docs/docs/targets.md`: add a task-table row for `TaskNotebook`
  - `docs/docs/installation.md`: add a row for the `notebook` extra
  - `mkdocs.yml`: add `docs/reports.md` to `nav` (next to "Task I/O formats") **and** to `llmstxt` `sections` → `Guide`
  - `CHANGELOG.md`: add bullets under `## [Unreleased]` → `### Added`
  - `CLAUDE.md`: Layout lines for `tasks/notebook.py` and `targets/notebook.py`, plus the Public API bullet from the notebook spec, Implementation step 7
  - `docs/todo/20261018-output-notebook-reports.md`: append `## Implementation notes (divergences from the plan as built)`. Record the reconciliation rules, the cuts from Deferred that touch this spec, and anything else that changed.
- Create: `docs/docs/reports.md`, "Reports from notebooks"
- Regenerate: `tests/test_docs_targets.py`

**Interfaces:**
- Consumes: the public names from Tasks 1–9, exactly as defined there.

- [ ] **Step 1: Write the pages**

Write for data scientists: what they get and what to type, with no internals. Cover:
- the authoring-guard snippet;
- `rpt.inputLoad(task='a')`, `rpt.table` and `rpt.fmt`, and that the same `formats=` dict used for Excel works in `rpt.table`;
- when a report reruns: its data, parameters, task code or cells change, but not when you re-run it in Jupyter.

`reports.md` is **not** added to `TESTED_PAGES`.

- [ ] **Step 2: Regenerate and check**

Run: `python scripts/build_docs.py`
Expected: it regenerates `tests/test_docs_targets.py` and those tests pass.

Run: `python scripts/build_docs.py --check`
Expected: exit 0.

Run: `mkdocs build`
Expected: no warnings about `reports.md` links, and `site/llms.txt` lists the page.

- [ ] **Step 3: Sanitization grep**

Run: `git diff --cached --name-only | xargs grep -nE '[A-Za-z]:\\\\|/Users/|OneDrive'` (also run it over untracked new files).
Expected: no output. Then manually scan the new prose for client, private-project and data-vendor names.

- [ ] **Step 4: Commit** (only if authorized)

```bash
git add docs/docs/targets.md docs/docs/reports.md docs/docs/installation.md mkdocs.yml CHANGELOG.md CLAUDE.md tests/test_docs_targets.py docs/todo/20261018-output-notebook-reports.md
git commit -m "docs: notebook reports"
```

- [ ] **Step 5: Hand-check (by the executor, not delegated)**

Notebook spec, Verification 5: run the two-choice report project in a scratch directory outside the repo and outside any synced folder. Report the plugin follow-up (the notebook spec, Docs, last bullet) to the maintainer; don't edit the plugin repo.

## Deferred (cut from this plan; skip these parts of the specs)

- matplotlib chart defaults: `report.setup()`, `PALETTE`, `percent_axis`
- Excel hyperlinks (`links=`, also in `report.table`), bold section-break rows (`bold_rows`), and the spec's general `conditional=` rule API (Task 3's `highlight`/`colorscale`/`banded` cover the deliverable cases). Per-sheet `sheet_options` is replaced by sheet spec dicts (Task 2).
- Suppressing Excel's "number stored as text" warning on `id_columns`: openpyxl cannot write it.
- the `formats(**by_preset)` helper, `pct()`/`num()` (use `fmt(x, 'pct')`), the `bp_signed` preset
- the published report copy (`report_name`, `publish_dir`) and the `_template_digest` memo cache
- `test_excel_codes_valid_in_openpyxl` (openpyxl does not validate number formats, so it proves nothing)

## Out of scope (open questions stay open)

From the Excel spec: the large-frame fast path, `formatted=True` as the default, and grouped MultiIndex headers. (Title rows were pulled into scope by Task 3.) From the notebook spec: auto-tracking template imports, PDF output, cloud storage, notebook-to-flow values, and plugin changes. The Excel spec's "final home" question is settled by this plan: the presets live in `oryxflow/report.py`, and the writer stays in `utils.py`.
