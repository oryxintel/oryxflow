# Formatted Excel writer (`oryxflow.utils.excel_write`)

## Context

oryxflow can already save dataframes to Excel (`ExcelPandasTarget`, `ExcelPandasSheetsTarget`,
`TaskExcelPandas`, `TaskExcelPandasSingle`), but the files are raw `df.to_excel` dumps. Every
column is about 8 characters wide, floats show 15 digits (`0.123456789012345`), percentages show
as `0.0532` instead of `5.3%`, datetimes show as `2026-01-01 00:00:00`, the header row scrolls
out of view, and there is no autofilter. Excel is the format we pick *because a human will open
it*, so the human ends up spending the first minute of every file tidying it up by hand.

An earlier, unpublished reporting helper solved this with a single
`to_excel(path, {sheet: df}, ...)` function. What it offers, and how much of it is
worth keeping:

| Earlier helper feature | Verdict |
| --- | --- |
| `{sheet: df}` dict → one sheet per frame | **keep** |
| bold, wrapped, shaded (`#d3d9e3`), bordered header row | **keep** |
| `freeze_panes='index'` (freeze header row + index columns) | **keep** (as `freeze=True`) |
| `width='auto'` with `width_min`/`width_max` clamp | **keep**, but computed from the in-memory frame |
| `filter=True` → autofilter over the table | **keep, default on** |
| `format_pct="0.0%"` + `float_pct_columns` | **keep the idea**, folded into one `formats` spec |
| `format_float="%.2f"` (pandas `float_format`) | **drop** — it *rounds the stored values*, so the file no longer loads back to the same data. We set a display number format instead. |
| `as_datetime=False` → `df[col].dt.date` | **drop** — it rewrites the data; a `yyyy-mm-dd` number format gives the same look losslessly |
| `rename=` (dict or YAML string) | **drop** — `df.rename(...)` already exists; no `pyyaml` dependency |
| `title=` / `title_format=` | **drop** — never implemented there; a title row also shifts the header so `pd.read_excel` stops round-tripping (see Open questions) |
| A report-builder class | **drop** — an unfinished stub |
| A sheet-to-image export (win32com screenshot) | **drop** — Windows-only, needs Excel installed |
| ppt / mako text-report modules | out of scope |

The earlier helper's engine choice is also instructive: it wrote with **xlsxwriter**, then re-read the file
with `pd.read_excel` to compute widths, then re-opened it with **openpyxl** to set widths and
filters, saving twice — three passes, two engines, and (per its own `# todo: preserve column
formats`) it lost formats along the way. oryxflow already requires openpyxl and pandas' openpyxl
writer hands us the live worksheet, so all of this can be done in **one write pass with one
engine**.

Intended outcome: one function, `oryxflow.utils.excel_write(path, data, ...)`, that writes a
nicely formatted workbook (autofilter, frozen header, sensible widths, number/percent/date
formats, styled header, optional conditional formatting, multiple sheets), and an opt-in
`formatted=True` on the existing Excel targets so a task can save formatted output with
`self.save(data, formatted=True)` — while the file still loads back to the same dataframe.

### Design decisions

- **A function, not a writer class.** `excel_write(path, data, **options)` mirrors
  `df.to_excel` and the earlier helper's `to_excel`; nothing needs to persist between calls. A class was
  rejected (the earlier helper's report-builder class never got past a stub, and a builder API is more to
  learn for no gain). The per-sheet work lives in a private `_excel_format_sheet(ws, df, ...)`
  so a later writer (e.g. a report module adding charts) can reuse it on any openpyxl worksheet.
- **Engine: openpyxl only, no xlsxwriter.** openpyxl is already in `install_requires`, can read
  the file back (which the tests need anyway), and `pd.ExcelWriter(engine='openpyxl').sheets[name]`
  is a live `openpyxl.worksheet.worksheet.Worksheet` we can style before the writer closes — one
  pass, one save. xlsxwriter was rejected: a new dependency, write-only (cannot verify its own
  output), and its only real advantage is speed on very large sheets. Measured with the openpyxl
  engine on a 100,000 × 10 float frame: `to_excel` ≈ 7 s, per-cell number formatting ≈ 5 s, total
  with save ≈ 29 s. Formatted Excel of a million cells is not a human-readable report anyway; the
  speed question is recorded under Open questions rather than solved with a second engine.
- **Formats are display-only; the data is never changed.** No `float_format`, no `.dt.date`, no
  rounding. Every option sets cell *styles* (number formats, fonts, fills, widths, filters,
  panes). Consequence, and a hard requirement: `pd.read_excel(path, sheet_name=None)` of a
  formatted file equals what an unformatted save would load (same values, same dtypes), so
  `ExcelPandasSheetsTarget.load()` / `TaskExcelPandas.outputLoad()` are unaffected.
- **One `formats` spec: `{column_or_glob: alias_or_excel_code}`, plus dtype defaults.**
  Resolution order per column, first match wins:
  1. exact column name in `formats`;
  2. `fnmatch` glob keys in `formats`, in dict order (`{'*_pct': 'pct', 'ret_*': 'pct'}` — this
     replaces the earlier helper's separate `float_pct_columns`, and handles "percent by name" without a
     second parameter);
  3. dtype default: float → `'float'`, datetime64 → `'date'` if every non-null value is at
     midnight else `'datetime'`, everything else (ints, bools, strings, objects) → untouched
     (`General`). Ints are deliberately **not** given a thousands separator by default — year,
     id and code columns (`2026` → `2,026`) are too common; ask for `'int'` explicitly.
  Values are either an alias from a module-level `EXCEL_FORMATS` table or a raw Excel number
  format code passed through verbatim (`'0.000'`, `'#,##0,"k"'`). `formats=False` disables
  number formatting entirely (dtype defaults included). The key `None` is not special.
- **Alias table shaped for sharing with a notebook/report formatter.** A separately planned module
  will format dataframes for display in notebooks (pandas `Styler.format` / display formats). So
  `EXCEL_FORMATS` is not Excel-only: each alias maps to *both* an Excel code and a Python format
  string, and resolution is a separate public function:
  ```python
  FORMATS = {   # alias: (excel number format, python format spec)
      'int':      ('#,##0',            '{:,.0f}'),
      'float':    ('#,##0.00',         '{:,.2f}'),
      'float4':   ('0.0000',           '{:.4f}'),
      'pct':      ('0.0%',             '{:.1%}'),
      'pct2':     ('0.00%',            '{:.2%}'),
      'thousands':('#,##0,"k"',        None),
      'date':     ('yyyy-mm-dd',       '{:%Y-%m-%d}'),
      'datetime': ('yyyy-mm-dd hh:mm', '{:%Y-%m-%d %H:%M}'),
      'text':     ('@',                '{}'),
  }
  def resolve_formats(df, formats=None):   # -> {column: alias_or_code}
  ```
  `resolve_formats` returns aliases (or raw codes) per column; the Excel writer maps them through
  column 0 of `FORMATS`, a notebook formatter through column 1 (raw Excel codes have no Python
  equivalent, and a `None` Python spec means "leave as is" — that formatter decides). The same
  `formats=` dict therefore styles both the Excel export and the notebook view of a frame. This
  plan only builds the Excel side; it must not build the notebook side.
- **Defaults are "on" for everything cheap and safe:** `filters=True`, `freeze=True`,
  `widths='auto'`, `header_style=True`, dtype-default formats. `conditional=None` (opt-in).
  `index=False` by default, matching the existing targets (`opts = {'index': False}`) so
  round-trip loading keeps working; `index=True` is supported (freeze and filter then include
  the index columns).
- **Widths from the in-memory frame, not a re-read.** Per column:
  `clamp(max(len of rendered values in a sample, min(len(header), 20)) + 2, width_min, width_max)`
  with `width_min=8`, `width_max=60`, sample = first 1,000 rows. Rendered length uses the Python
  half of the resolved alias when there is one (so `1234567.891` with `'float'` counts as
  `1,234,567.89`), else `str(value)`. Headers longer than 20 characters wrap (the header style
  sets `wrap_text`) instead of forcing the column wide — that is why the header term is capped.
  `widths` also accepts an int (all columns) or a `{column: int}` dict merged over the auto
  values.
- **Header style:** bold, `wrap_text=True`, vertical top / horizontal left, fill `D3D9E3`, thin
  bottom border — the earlier helper's look. Applied by restyling row 1 after `to_excel` (pandas' own header
  style is bold+border+centered; we overwrite it). The earlier helper's `startrow=1, header=False` trick
  (write the header manually) is not needed.
- **Conditional formatting: a small named set plus an escape hatch.**
  `conditional={column_or_glob: kind}` where `kind` is `'color_scale'` (red–yellow–green 3-colour
  scale), `'data_bar'`, `'negative_red'` (dark red font for `< 0`), or any openpyxl rule object
  (passed straight to `ws.conditional_formatting.add`). Applied to the data rows of the column.
  Rules apply to the cells' display only, so they are as round-trip safe as number formats.
- **Multiple sheets and per-sheet overrides.** `data` is a `DataFrame` (written to one sheet,
  `sheet_name='Sheet1'` — pandas' default, so it matches what `ExcelPandasTarget` writes today) or
  a `{sheet: df}` dict. All options apply to every sheet; `sheet_options={sheet: {option: value}}`
  overrides any of them for one sheet (e.g. `{'readme': {'filters': False, 'freeze': False}}`).
  `formats`/`conditional` given in `sheet_options` replace (not merge with) the global ones for
  that sheet.
- **Fail early on Excel's sheet-name rules.** Names over 31 characters or containing any of
  `[]:*?/\` raise `ValueError` naming the sheet. openpyxl would otherwise warn and/or truncate, and
  a truncated name silently breaks `ExcelPandasSheetsTarget.load(keys=...)`, which looks sheets up
  by the persist key.
- **MultiIndex columns are flattened** to `' / '`-joined strings before writing. pandas raises
  `NotImplementedError: Writing to Excel with MultiIndex columns and no index ('index'=False) is
  not yet implemented.` for the default `index=False`, and multi-row headers would also break
  freeze/filter placement. (Loading back then returns the flat names — documented, see Open
  questions.)
- **Opt-in on the existing targets, not a new target or task class.**
  `ExcelPandasSheetsTarget.save(data, formatted=False, **kwargs)` and
  `ExcelPandasTarget.save(df, formatted=False, **kwargs)`: when `formatted=True` the kwargs are
  `excel_write` options and the target delegates to it; when false the current code path runs
  byte-for-byte unchanged. Both `TaskExcelPandas.save` and `TaskData.save` already forward
  `**kwargs` to the target, so a task needs **no change**:
  `self.save({'summary': df1, 'detail': df2}, formatted=True, formats={'*_pct': 'pct'})`.
  Rejected: a new `TaskExcelPandasFormatted` class (doubles the class table in the docs for what
  is a save option), and making formatting the default (changes every existing Excel output and
  the cost on large frames; see Open questions). Formatting options are *not* parameters, so they
  do not enter `task_id` and changing them does not invalidate — the same as every other save
  kwarg today.
- **Lives in `oryxflow/utils.py` for now, with lazy imports.** `oryxflow.targets` imports
  `oryxflow.utils` at package import, so module-level `openpyxl` imports there would slow down
  `import oryxflow`; all openpyxl imports go inside the functions. **Why it should move later:**
  `utils.py` is a grab bag (tree printing, param generators, hashing, data-dir warnings); a ~200
  line Excel section plus the shared `FORMATS` table belongs with the planned notebook/report
  formatter in a dedicated output module (e.g. `oryxflow/output.py` or `oryxflow/report/`), so
  that the targets depend on an output module rather than on `utils`, and so Excel, notebook and
  any future report formats share one formats table in one place. When it moves,
  `oryxflow.utils.excel_write` / `resolve_formats` / `FORMATS` stay as re-exports so user code
  does not break.

## Implementation

1. **`oryxflow/utils.py` — add an "Excel output" section at the end of the file.** Plain style,
   no type annotations, docstrings like the rest of the file. Public names: `FORMATS`,
   `resolve_formats`, `excel_write`. Private: `_EXCEL_SHEET_BAD_CHARS`, `_excel_check_sheet_name`,
   `_excel_flatten_columns`, `_excel_widths`, `_excel_format_sheet`, `_excel_conditional_rule`.

   ```python
   FORMATS = {...}  # exactly the table in Design decisions

   _EXCEL_DEFAULTS = dict(formats=None, index=False, filters=True, freeze=True, widths='auto',
                          width_min=8, width_max=60, header_style=True, conditional=None)

   def resolve_formats(df, formats=None):
       '''
       Resolve the display format for each column of df.
       formats: {column_or_glob: alias_or_excel_code}; False disables all formatting.
       Returns {column: alias_or_code} for formatted columns only.
       '''
       import fnmatch
       if formats is False:
           return {}
       formats = formats or {}
       out = {}
       for col in df.columns:
           name = str(col)
           if col in formats:
               out[col] = formats[col]; continue
           hit = next((v for k, v in formats.items()
                       if isinstance(k, str) and any(c in k for c in '*?[') and fnmatch.fnmatchcase(name, k)), None)
           if hit is not None:
               out[col] = hit; continue
           s = df[col]
           if pd.api.types.is_float_dtype(s):
               out[col] = 'float'
           elif pd.api.types.is_datetime64_any_dtype(s):
               nn = s.dropna()
               out[col] = 'date' if (nn.dt.normalize() == nn).all() else 'datetime'
       return out
   ```
   `utils.py` does not import pandas at module level today (only `flatten`, `os`, `warnings`,
   `pathlib`, `hashlib`); import `pandas as pd` inside each Excel function, like openpyxl. Use `fnmatchcase` (case-sensitive, OS
   independent — plain `fnmatch` is case-insensitive on Windows only).

   `_excel_check_sheet_name(name)`: `str(name)`; raise `ValueError(f"Excel sheet name {name!r}
   ...")` if `len > 31` or any char in `[]:*?/\`, or empty.

   `_excel_flatten_columns(df)`: if `isinstance(df.columns, pd.MultiIndex)` return a copy with
   `columns = [' / '.join(str(x) for x in t if str(x) != '') for t in df.columns]`; else return
   `df` unchanged (no copy — never mutate the caller's frame; flattening works on a copy).

   `_excel_widths(df, resolved, index, width_min, width_max, widths)`: returns a list of widths
   for the written columns in sheet order (index levels first if `index`). For each column take
   `df[col].head(1000)`, render each non-null value with the Python spec of its alias
   (`FORMATS[alias][1].format(v)` inside `try`, falling back to `str(v)`), take the max length
   (0 if empty), combine with `min(len(str(header)), 20)`, add 2, clamp. If `widths` is an int,
   every column gets it; if a dict, its entries override auto values by column name.

   `_excel_conditional_rule(kind)`: map `'color_scale'` →
   `ColorScaleRule(start_type='min', start_color='F8696B', mid_type='percentile', mid_value=50,
   mid_color='FFEB84', end_type='max', end_color='63BE7B')`, `'data_bar'` →
   `DataBarRule(start_type='min', end_type='max', color='638EC6')`, `'negative_red'` →
   `CellIsRule(operator='lessThan', formula=['0'], font=Font(color='9C0006'))`; a non-string is
   returned as is; an unknown string raises `ValueError` listing the valid kinds.

   `_excel_format_sheet(ws, df, index=False, formats=None, filters=True, freeze=True,
   widths='auto', width_min=8, width_max=60, header_style=True, conditional=None)` — `df` is the
   frame exactly as written. With `nidx = df.index.nlevels if index else 0`,
   `ncols = nidx + df.shape[1]`, `nrows = df.shape[0]`, `last = get_column_letter(ncols)`:
   - `if ncols == 0: return`.
   - header: for `cell in ws[1]` set `font=Font(bold=True)`,
     `alignment=Alignment(wrap_text=True, vertical='top', horizontal='left')`,
     `fill=PatternFill('solid', fgColor='D3D9E3')`, `border=Border(bottom=Side(style='thin'))`.
   - number formats: `resolved = resolve_formats(df, formats)`; for each `(j, col)` in
     `enumerate(df.columns)` with a resolved entry, `code = FORMATS[v][0] if v in FORMATS else v`,
     then `for (cell,) in ws.iter_rows(min_row=2, max_row=nrows + 1, min_col=nidx + j + 1,
     max_col=nidx + j + 1): cell.number_format = code`. (Per cell is required: openpyxl's
     `column_dimensions[...].number_format` does not apply to cells that already exist.)
     Index columns get dtype defaults too (`resolve_formats(df.index.to_frame(), None)`).
   - freeze: `True` → `ws.freeze_panes = ws.cell(row=2, column=nidx + 1).coordinate`
     (`'A2'` without index, `'B2'` with one index level); a string is used as the cell;
     `False`/`None` → nothing.
   - filters: `ws.auto_filter.ref = f'A1:{last}{nrows + 1}'` (an empty frame gives a header-only
     range, which Excel accepts).
   - widths: unless `widths is None or widths is False`, set
     `ws.column_dimensions[get_column_letter(i + 1)].width = w` for each computed width.
   - conditional: for each `(key, kind)` in `conditional or {}`, match columns exactly-then-glob
     (same rules as `resolve_formats`), and for each match with `nrows > 0`,
     `ws.conditional_formatting.add(f'{L}2:{L}{nrows + 1}', _excel_conditional_rule(kind))`.

   ```python
   def excel_write(path, data, sheet_options=None, **options):
       '''
       Write one or more dataframes to a formatted Excel file.

       data: DataFrame (written to sheet 'Sheet1') or {sheet_name: DataFrame}
       options (all sheets): formats, index, filters, freeze, widths, width_min, width_max,
           header_style, conditional -- see docs/todo/20261018-utils-excel-writer.md
       sheet_options: {sheet_name: {option: value}} per-sheet overrides
       Returns: path
       '''
       unknown = set(options) - set(_EXCEL_DEFAULTS)
       if unknown: raise TypeError(f'excel_write() got unexpected options {sorted(unknown)}')
       if isinstance(data, pd.DataFrame): data = {'Sheet1': data}
       sheet_options = sheet_options or {}
       for name in data: _excel_check_sheet_name(name)
       for name in sheet_options:
           if name not in data: raise KeyError(f'sheet_options for unknown sheet {name!r}')
       path = pathlib.Path(path) if not hasattr(path, 'open') else path  # keep UPath as is
       path.parent.mkdir(parents=True, exist_ok=True)
       with pd.ExcelWriter(path, engine='openpyxl') as writer:
           for name, df in data.items():
               opts = {**_EXCEL_DEFAULTS, **options, **sheet_options.get(name, {})}
               df = _excel_flatten_columns(df)
               df.to_excel(writer, sheet_name=name, index=opts['index'])
               _excel_format_sheet(writer.sheets[name], df, **opts)
       return path
   ```
   `header_style=False` skips the header block; validate `sheet_options` keys against
   `_EXCEL_DEFAULTS` the same way. The cloud-path case (`settings.cloud_fs_enabled`, `UPath`)
   must behave like the existing `ExcelPandasSheetsTarget.save`, which passes `self.path`
   straight to `pd.ExcelWriter` — do the same (don't coerce a `UPath` to `pathlib.Path`).

2. **`oryxflow/targets/__init__.py` — `ExcelPandasSheetsTarget.save` (around line 254).**
   ```python
   def save(self, data, formatted=False, **kwargs):
       '''... formatted (bool): write with oryxflow.utils.excel_write (autofilter, frozen header,
       column widths, number formats); kwargs are then excel_write options ...'''
       if formatted:
           (self.path).parent.mkdir(parents=True, exist_ok=True)
           oryxflow.utils.excel_write(self.path, data, **kwargs)
           return self.path
       ... existing body unchanged ...
   ```
   `oryxflow.utils` is already imported at the top of this module.

3. **`oryxflow/targets/__init__.py` — `ExcelPandasTarget.save` (around line 206).** Same switch:
   `if formatted: excel_write(self.path, {'Sheet1': df}, **kwargs); return self.path`, else the
   existing `super().save(df, 'to_excel', **opts)`. Fix the docstrings' copy-paste
   "`pd.read_csv`" / "`df.to_csv`" while there (they say csv in the Excel targets).

4. **No change to `oryxflow/tasks/__init__.py`.** `TaskExcelPandas.save(data, from_list=False,
   **kwargs)` and `TaskData.save(data, from_list=False, **kwargs)` already forward kwargs to the
   target. Verify by the integration test in step 5, not by editing.

5. **`tests/test_utils_excel.py` — new file** (collected: `test_*.py`). Use pytest's `tmp_path`;
   read results back with `openpyxl.load_workbook` and `pd.read_excel`. Fixture frame:
   `pd.DataFrame({'name': ['a', 'bb'], 'year': [2025, 2026], 'value': [1234567.891, -2.5],
   'ret_pct': [0.0532, -0.1], 'when': pd.to_datetime(['2026-01-01', '2026-02-01']),
   'stamp': pd.to_datetime(['2026-01-01 09:30', '2026-01-02 10:00'])})`. Tests:
   - `test_defaults`: autofilter `ws.auto_filter.ref == 'A1:F3'`, `ws.freeze_panes == 'A2'`,
     header `ws['A1'].font.b` and `ws['A1'].fill.fgColor.rgb` ends with `'D3D9E3'`,
     `ws['A1'].alignment.wrap_text`.
   - `test_number_formats_dtype_defaults`: `value` → `'#,##0.00'`, `when` → `'yyyy-mm-dd'`,
     `stamp` → `'yyyy-mm-dd hh:mm'`, `year` and `name` → `'General'`.
   - `test_formats_precedence`: `formats={'ret_*': 'pct', 'value': '0.000', 'year': 'int'}` →
     `ret_pct` `'0.0%'`, `value` `'0.000'` (raw code passthrough), `year` `'#,##0'`; and exact
     beats glob: `{'*': 'text', 'value': 'pct'}` → `value` `'0.0%'`, `name` `'@'`.
   - `test_formats_false`: `formats=False` leaves every data cell `'General'` (dates excepted
     only if pandas itself stamps them — assert on `value`/`ret_pct` only).
   - `test_round_trip`: `pd.read_excel(path)` equals `pd.read_excel` of a plain
     `df.to_excel(index=False)` file (`pd.testing.assert_frame_equal`) — values not rounded
     (`1234567.891` intact).
   - `test_widths`: long text column (`'x' * 200`) gets exactly `width_max`; one-char column
     gets `width_min`; `widths=15` gives 15 everywhere; `widths={'name': 30}` overrides one.
   - `test_index_true`: frame with `set_index('name')`, `index=True` → `freeze_panes == 'B2'`,
     filter starts at `A1` and spans `ncols + 1` columns.
   - `test_multi_sheet_and_sheet_options`: `{'a': df, 'b': df}` with
     `sheet_options={'b': {'filters': False, 'freeze': False}}` → sheet `a` has a filter,
     sheet `b` has `auto_filter.ref is None` and `freeze_panes is None`.
   - `test_conditional`: `conditional={'ret_pct': 'color_scale', 'value': 'negative_red'}` →
     `{str(cf.sqref): [r.type for r in cf.rules] for cf in ws.conditional_formatting}` contains
     `colorScale` on the `ret_pct` range and `cellIs` on the `value` range; unknown kind raises
     `ValueError`.
   - `test_bad_sheet_name`: 32-char name and `'a/b'` raise `ValueError`; unknown option raises
     `TypeError`; `sheet_options` for a missing sheet raises `KeyError`.
   - `test_multiindex_columns_flattened`: two-level columns write without error and header
     cells read `'a / x'`, `'a / y'`.
   - `test_empty_frame`: `pd.DataFrame({'a': []})` writes; filter `'A1:A1'`.
   - `test_resolve_formats_public`: returns aliases, not codes (`{'value': 'float', ...}`).
   - `test_task_formatted_save`: `oryxflow.set_dir(tmp_path / 'data')` (restore
     `oryxflow.settings.dir`/`dirpath` in a `try/finally`, as `tests/conftest.py` does), a
     `TaskExcelPandas` with `persists = ['summary', 'detail']` whose `run()` calls
     `self.save({'summary': df, 'detail': df}, formatted=True, formats={'ret_*': 'pct'})`;
     run it, assert `outputLoad(as_dict=True)['summary']` equals the unformatted round-trip, and
     the file's `summary` sheet has the autofilter and `'0.0%'` on `ret_pct`. Also a
     `TaskExcelPandasSingle` saving one frame with `formatted=True` (sheet `'Sheet1'`).
   - `test_unformatted_path_unchanged`: `ExcelPandasSheetsTarget(...).save(data)` (no
     `formatted`) produces no autofilter and no freeze — the old path is untouched.

6. **`docs/docs/targets.md` — user-facing paragraph** after the "Which task class should I use?"
   table's guidance paragraph (the one starting "A rough guide"), titled "Excel files people will
   open". Written for data scientists: what they get (filters on every column, header stays
   visible while scrolling, readable column widths, numbers shown as `1,234.57`, `5.3%`,
   `2026-01-01`, the data itself unchanged so it loads back the same), and what to type:
   `self.save({...}, formatted=True, formats={'*_pct': 'pct'})` and the standalone
   `oryxflow.utils.excel_write('report.xlsx', {'summary': df})`. List the aliases in a small
   table. `targets.md` is a phmdoctest-tested page (`TESTED_PAGES` in `scripts/build_docs.py`):
   make the snippet runnable (define a small `df` in it and use a distinct task class name so it
   does not collide with other tasks on the page) or mark it `<!--phmdoctest-skip-->`. Either
   way, inserting prose shifts the source line numbers that name every later generated test, so
   **always** regenerate with `python scripts/build_docs.py` and commit the regenerated
   `tests/test_docs_targets.py` (never hand-edit it). No assertions in the page.

7. **`CHANGELOG.md` (repo root)** — one entry at the top in the existing style: "Formatted Excel
   output: `oryxflow.utils.excel_write()` and `formatted=True` on Excel task saves (autofilter,
   frozen header, column widths, number/percent/date formats, optional conditional formatting)."

8. **`CLAUDE.md` — Layout line for `utils.py`**: append `excel_write/resolve_formats/FORMATS
   (formatted Excel output; candidate to move to an output module)`, so future sessions find it
   before reinventing it.

## Files modified

- `oryxflow/utils.py` — new "Excel output" section: `FORMATS`, `resolve_formats`, `excel_write`, private helpers (lazy openpyxl imports).
- `oryxflow/targets/__init__.py` — `formatted=False` switch on `ExcelPandasSheetsTarget.save` and `ExcelPandasTarget.save`; docstring csv→Excel fixes.
- `tests/test_utils_excel.py` — new test module (writer, formats, widths, sheets, conditional, task integration, unchanged default path).
- `docs/docs/targets.md` — short "Excel files people will open" section.
- `tests/test_docs_targets.py` — regenerated by `scripts/build_docs.py` (test names follow source line numbers).
- `CHANGELOG.md` — release note.
- `CLAUDE.md` — Layout line for `utils.py`.
- `docs/todo/20261018-utils-excel-writer.md` — this plan (plus divergence notes once built).

No change to `setup.py` (openpyxl is already required) or `oryxflow/tasks/__init__.py`.

## Verification

1. New tests:
   ```bash
   python -m pytest tests/test_utils_excel.py -q
   ```
   Expected: all pass (≈15 tests).
2. Baseline held — Excel round-trip tests in `test_main.py` (`test_excel_default_persist`,
   `test_excel_sheets`) exercise the unformatted path:
   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```
   Expected: **86 passing** (unchanged).
3. Docs tests: `python scripts/build_docs.py --check` passes (generated `tests/test_docs_targets.py` is current and its tests pass).
4. Import cost unchanged: `python -X importtime -c "import oryxflow" 2>&1 | grep -i openpyxl`
   prints nothing (openpyxl is imported only when an Excel file is written/read).
5. Manual look (once): write the fixture frame with `excel_write('check.xlsx', {'a': df, 'b': df},
   conditional={'ret_pct': 'color_scale'})`, open it in Excel / LibreOffice and confirm: filter
   arrows on the header, header frozen and shaded, `1,234,567.89`, `5.3%`, `2026-01-01`, columns
   wide enough, colour scale on `ret_pct`, no "repaired content" prompt.

## Open questions

- **Large frames.** Per-cell formatting with openpyxl costs ≈5 s per million cells on top of a
  slow write. Options: do nothing (formatted Excel is for human-sized tables); warn above N
  cells; skip number formats past N rows; or an optional xlsxwriter fast path when it happens to
  be installed. Default plan: do nothing.
- **Should `formatted=True` become the default** for `TaskExcelPandas` / `TaskExcelPandasSingle`
  (Excel is chosen for humans)? It changes every existing Excel output's styling, but not its
  data. Or a class attribute (`excel_options = {...}`) so a task sets formatting once instead of
  in every `save()` call.
- **Title / notes row above the table** (the earlier helper's unfinished `title=`). Needs `startrow` and a
  matching `header=` offset in `ExcelPandasSheetsTarget.load`, or a separate "readme" sheet
  instead. Left out until there is a use.
- **MultiIndex columns:** flattening to `'a / x'` means loading back gives flat names. Acceptable,
  or should `index=True` + multi-row headers be supported for reports that need grouped headers?
- **Alias set and dtype defaults:** is `float` → `#,##0.00` right for small-valued columns
  (coefficients, p-values show as `0.00`)? An alternative is `'float'` = `#,##0.00` only when the
  column's median magnitude is ≥ 1, else `0.0000`. Also confirm the alias names with the planned
  notebook formatter before either ships, since they become the shared vocabulary.
- **Final home:** `oryxflow/output.py` vs a `oryxflow/report/` package, decided when the notebook
  formatter is planned.

## Implementation notes (divergences from the plan as built)

Built from `20261018-output-formats-impl-plan.md` (Part A). Where this file and the plan differed, the plan won. What changed:

**Spec reconciliation**
1. One preset table, in `oryxflow/report.py`: `Format(py, excel)` with the union of both alias sets, minus `bp_signed`. `utils.excel_write` reads `FORMATS[name].excel` and does not re-export `FORMATS`. (The spec's `(excel, py)` tuple in `utils` is gone.)
2. `resolve_formats` lives in `report.py`, not `utils.py`, and has `defaults=True`. The Excel writer uses the dtype defaults; glob keys (`'*_pct'`) work everywhere.
3. One rule for a format value: preset name, `str.format` spec (contains `{`), callable, otherwise a raw Excel code. A string that looks like a preset name (`^[a-z][a-z_0-9]*$`) but is not one raises `KeyError` listing the valid names, so a typo never writes an invalid number format that triggers Excel's "repaired content" prompt.
4. Bad values never raise while formatting; they fall back to `str(value)`.
5. Every preset renders the same on both sides (`thousands` has a Python form matching `#,##0,"k"`).

**Cuts from the plan's Deferred list that touch this spec**
- Hyperlinks (`links=`), bold section-break rows (`bold_rows`) and the general `conditional=` rule API. `highlight`, `colorscale` and `banded` cover the deliverable cases.
- Per-sheet `sheet_options` is replaced by sheet spec dicts: `{sheet: {'df': df, **options}}`.
- Suppressing Excel's "number stored as text" warning on `id_columns`: openpyxl cannot write the setting that hides it, so the warning stays.
- The `formats(**by_preset)` helper, `pct()`/`num()` and the `bp_signed` preset.
- `test_excel_codes_valid_in_openpyxl`: openpyxl does not validate number formats, so it proves nothing.
- The spec's open question on a title row was resolved in scope: `title=` and `notes=` are built, and the header moves down (read back with `skiprows=len(notes) + 3`).

**Other changes while building**
- Note-row height: wrapped note rows get an estimated 12pt per line, because Excel does not auto-fit merged cells. The plan did not ask for this.
- `id_columns` read-back: cells are stored as text, so a plain `pd.read_excel` re-infers them as numbers. Use `dtype={col: str}` to keep leading zeros.
- Width padding: column widths add the spec formula's +2 padding.
- `freeze=n` is literal: it freezes exactly n columns, even with `index=True`.
- `formatted=False` rejects Excel-only options: `title=` (or any `excel_write`-only option) with `formatted=False` raises `TypeError` from pandas.
- Out of scope, still open: the large-frame fast path, `formatted=True` as the default, grouped MultiIndex headers. The "final home" question is settled: presets in `oryxflow/report.py`, writer in `utils.py`.

**Review-fix changes (behaviour added after the first build)**
- `notes` accepts a single string (treated as a one-item list), globally and per sheet.
- `id_columns`: a whole-number float (an int code column holding NaN) is written without `.0`; NaN stays an empty cell.
- Timezone-aware datetime columns raise `ValueError` naming the column and suggesting `.dt.tz_localize(None)`, before any file is written.
- `highlight` keys that are int/float (not bool) use an unquoted formula, so `{1: 'green'}` matches the number 1; string keys stay quoted. A non-dict `highlight`, or a non-dict inner value, raises `TypeError`.
- `freeze=n` raises `ValueError` when n is negative or larger than the number of written columns.
- Every value of a dict `formats` is validated up front, even when its key matches no column, so a typo'd preset raises `KeyError` before any file is written. An empty-string format also raises `KeyError`.
