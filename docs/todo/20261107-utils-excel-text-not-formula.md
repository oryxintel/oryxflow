# excel_write: text starting with `=` is text, formulas are opt-in

## Context

`oryxflow.utils.excel_write` (also reached through `TaskExcelPandas.save(sheets, formatted=True)`)
writes with pandas `to_excel` on the openpyxl engine. openpyxl's `Cell._bind_value` stores any
string longer than one character that starts with `=` as a formula (`data_type = 'f'`).

A downstream consumer project had a definitions sheet whose text column held
`'= Market quarterly (raw) x 4; annualized = ...'`. That is not a valid formula, so Excel refused
to open the whole workbook ("Unable to get the Open property of the Workbooks class"). The run
succeeded and nothing warned; finding the cell took a sheet-by-sheet bisect.

Intended outcome: a DataFrame's text is written as text, so a run that succeeds produces a file
Excel opens. A deliberate formula column is still possible, by naming it.

### Design decisions

- **Text by default.** After `to_excel`, a cell openpyxl marked as a formula gets
  `data_type = 's'`. The value is unchanged (no visible apostrophe), it reads back as the same
  string with openpyxl and `pd.read_excel`, and Excel shows it as typed. Applies to data cells,
  header cells, index cells, `title` and `notes`.
- **Only `=` is handled.** Verified with openpyxl 3.1: strings starting with `+`, `-`, `@` are
  stored as plain strings (`data_type 's'`), so they never broke a workbook. Strings equal to an
  Excel error code (`'#N/A'`) are stored as error cells (`'e'`); they don't stop Excel opening
  the file, so they are left as they are.
- **`formulas=[col, ...]` opt-in**, a column option like `wrap_columns`: workbook-level or
  per-sheet, a workbook-level column applies to the sheets that have it (a name in no sheet raises
  `KeyError`), a per-sheet column must exist in that sheet. Only data cells of those columns keep
  formulas; their header is always text. Index levels cannot be formula columns.
- **Only columns that need it are scanned.** Data cells are touched only in object / string
  columns that hold at least one string starting with `=`, so numeric-heavy sheets pay nothing.
- **Breaking.** Anyone writing formula strings through `excel_write` today gets text. Migration:
  name those columns in `formulas=`. Conditional-format rules (`highlight`, `banded`) are
  separate openpyxl objects and are unaffected. Nothing in the repo or its tests wrote formulas
  through cell values.

## Implementation

1. `oryxflow/utils.py`: `_EXCEL_DEFAULTS` gains `formulas=()`; `_EXCEL_COLUMN_OPTIONS` gains
   `'formulas'` (so the shared-column rule applies); `_excel_check_styling` validates its names.
2. `_excel_as_text(cell)`: a formula cell holding a string becomes `data_type = 's'`.
3. `_excel_text_not_formula(ws, cols, row_header, first, last_row, formula_pos)`: header cells,
   then data cells of string columns holding a leading `=`, skipping `formula_pos`. Called from
   `_excel_format_sheet` before header styling; `title` and `notes` cells go through
   `_excel_as_text` where they are written.
4. `excel_write` docstring names the default and `formulas`.

## Files modified

- `oryxflow/utils.py` - the option, the two helpers, the call in `_excel_format_sheet`, docstring.
- `tests/test_utils_excel.py` - text stays text (data, header, index, title, notes; round-trip),
  `formulas=` keeps a formula, workbook-level and per-sheet rules, `save(formatted=True)`.
- `CHANGELOG.md` - Added (`formulas=`) and a BREAKING Fixed bullet.

## Verification

```bash
python -m pytest tests/test_utils_excel.py -q
python -m pytest tests -q
```

The new tests fail with `_excel_as_text` made a no-op and pass with it. A file written with
`'= Market quarterly (raw) x 4; ...'` in a text column and `formulas=['calc']` opens in Excel:
the text shows as typed and `calc` computes.
