import subprocess
import sys

import numpy as np
import openpyxl
import pandas as pd
import pytest

from oryxflow.utils import excel_write


def make_df():
    return pd.DataFrame({
        'name': ['a', 'bb'], 'year': [2025, 2026], 'value': [1234567.891, -2.5],
        'ret_pct': [0.0532, -0.1],
        'when': pd.to_datetime(['2026-01-01', '2026-02-01']),
        'stamp': pd.to_datetime(['2026-01-01 09:30', '2026-01-02 10:00'])})


def sheet(p, name=None):
    wb = openpyxl.load_workbook(p)
    return wb[name] if name else wb.worksheets[0]


def width(ws, letter):
    return ws.column_dimensions[letter].width


def test_defaults(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df())
    ws = sheet(p)
    assert ws.title == 'Sheet1'
    assert ws.auto_filter.ref == 'A1:F3'
    assert ws.freeze_panes == 'A2'
    assert ws['A1'].font.b
    assert ws['A1'].fill.fgColor.rgb.endswith('D3D9E3')
    assert ws['A1'].alignment.wrap_text


def test_number_formats_dtype_defaults(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df())
    ws = sheet(p)
    assert ws['C2'].number_format == '#,##0.00'
    assert ws['E2'].number_format == 'yyyy-mm-dd'
    assert ws['F2'].number_format == 'yyyy-mm-dd hh:mm'
    assert ws['B2'].number_format == 'General'
    assert ws['A2'].number_format == 'General'


def test_formats_precedence(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df(), formats={'ret_*': 'pct', 'value': '0.000', 'year': 'int'})
    ws = sheet(p)
    assert ws['D2'].number_format == '0.0%'
    assert ws['C2'].number_format == '0.000'
    assert ws['B2'].number_format == '#,##0'
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, make_df(), formats={'value': '{:.1f}'})
    assert sheet(p2)['C2'].number_format == 'General'


def test_formats_typo_raises(tmp_path):
    p = tmp_path / 'a.xlsx'
    with pytest.raises(KeyError):
        excel_write(p, make_df(), formats={'ret_*': 'pctt'})
    assert not p.exists()


def test_formats_false(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df(), formats=False)
    ws = sheet(p)
    assert ws['C2'].number_format == 'General'
    assert ws['D2'].number_format == 'General'


def test_round_trip(tmp_path):
    df = make_df()
    p = tmp_path / 'a.xlsx'
    plain = tmp_path / 'plain.xlsx'
    excel_write(p, df, formats={'ret_*': 'pct'})
    df.to_excel(plain, index=False)
    got = pd.read_excel(p)
    pd.testing.assert_frame_equal(got, pd.read_excel(plain))
    assert got['value'][0] == 1234567.891


def test_widths(tmp_path):
    df = pd.DataFrame({'long': ['x' * 200], 'c': ['a'], 'name': ['abc']})
    p = tmp_path / 'a.xlsx'
    excel_write(p, df)
    ws = sheet(p)
    assert width(ws, 'A') == 60
    assert width(ws, 'B') == 8
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, df, widths=15)
    ws = sheet(p2)
    assert [width(ws, c) for c in 'ABC'] == [15, 15, 15]
    p3 = tmp_path / 'c.xlsx'
    excel_write(p3, df, widths={'name': 30})
    ws = sheet(p3)
    assert width(ws, 'C') == 30
    assert width(ws, 'A') == 60
    assert width(ws, 'B') == 8


def test_numeric_width_from_format(tmp_path):
    df = pd.DataFrame({'r': [0.047196123456]})
    p = tmp_path / 'a.xlsx'
    excel_write(p, df, formats={'r': 'pct'})
    assert width(sheet(p), 'A') <= 12


def test_filter_arrow_width(tmp_path):
    df = pd.DataFrame({'a' * 10: ['x']})
    on = tmp_path / 'on.xlsx'
    off = tmp_path / 'off.xlsx'
    excel_write(on, df, filters=True, width_min=1)
    excel_write(off, df, filters=False, width_min=1)
    assert width(sheet(on), 'A') == width(sheet(off), 'A') + 3


def test_freeze_int(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df(), freeze=2)
    assert sheet(p).freeze_panes == 'C2'
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, make_df(), freeze=False)
    assert sheet(p2).freeze_panes is None


def test_sheet_spec_overrides(tmp_path):
    df = make_df()
    p = tmp_path / 'a.xlsx'
    excel_write(p, {'a': df, 'b': {'df': df, 'filters': False, 'freeze': 1}},
                formats={'ret_*': 'pct'})
    wb = openpyxl.load_workbook(p)
    a, b = wb['a'], wb['b']
    assert a.auto_filter.ref and a.freeze_panes == 'A2'
    assert not b.auto_filter.ref
    assert b.freeze_panes == 'B2'
    assert b['D2'].number_format == '0.0%'
    with pytest.raises(TypeError):
        excel_write(tmp_path / 'c.xlsx', {'b': {'df': df, 'colour': 1}})
    with pytest.raises(TypeError):
        excel_write(tmp_path / 'd.xlsx', {'b': {'filters': False}})


def test_width_sample_capped(tmp_path):
    df = pd.DataFrame({'c': ['a'] * 1000 + ['x' * 200]})
    p = tmp_path / 'a.xlsx'
    excel_write(p, df)
    assert width(sheet(p), 'A') == 8


def test_index_true(tmp_path):
    df = make_df().set_index('name')
    p = tmp_path / 'a.xlsx'
    excel_write(p, df, index=True)
    ws = sheet(p)
    assert ws.freeze_panes == 'B2'
    assert ws.auto_filter.ref == 'A1:F3'


def test_multi_sheet(tmp_path):
    df = make_df()
    p = tmp_path / 'a.xlsx'
    excel_write(p, {'a': df, 'b': df})
    wb = openpyxl.load_workbook(p)
    for ws in wb.worksheets:
        assert ws.auto_filter.ref
        assert ws.freeze_panes == 'A2'
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, {'a': df, 'b': df}, filters=False)
    for ws in openpyxl.load_workbook(p2).worksheets:
        assert not ws.auto_filter.ref


def test_bad_sheet_name(tmp_path):
    df = make_df()
    with pytest.raises(ValueError):
        excel_write(tmp_path / 'a.xlsx', {'x' * 32: df})
    with pytest.raises(ValueError):
        excel_write(tmp_path / 'b.xlsx', {'a/b': df})
    with pytest.raises(TypeError):
        excel_write(tmp_path / 'c.xlsx', df, colour=1)


def test_sheet_names_case_insensitive_duplicate(tmp_path):
    df = make_df()
    with pytest.raises(ValueError, match='data'):
        excel_write(tmp_path / 'a.xlsx', {'Data': df, 'data': df})


def test_multiindex_columns_flattened(tmp_path):
    df = pd.DataFrame([[1, 2]], columns=pd.MultiIndex.from_tuples([('a', 'x'), ('a', 'y')]))
    p = tmp_path / 'a.xlsx'
    excel_write(p, df)
    ws = sheet(p)
    assert ws['A1'].value == 'a / x'
    assert ws['B1'].value == 'a / y'


def test_empty_frame(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, pd.DataFrame({'a': []}))
    assert sheet(p).auto_filter.ref == 'A1:A1'


def test_non_string_columns(tmp_path):
    df = pd.DataFrame({2025: [1.5], 2026: [2.5], 'x': ['a']})
    p = tmp_path / 'a.xlsx'
    excel_write(p, df)
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, df, formats={'202*': 'pct'})
    ws = sheet(p2)
    assert ws['A2'].number_format == '0.0%'
    assert ws['B2'].number_format == '0.0%'
    assert width(ws, 'A') >= 8 and width(ws, 'B') >= 8


def test_nulls_and_all_nat_column(tmp_path):
    df = pd.DataFrame({'f': [1.5, np.nan],
                       'd': pd.to_datetime([None, None]),
                       'o': [None, 'b']})
    p = tmp_path / 'a.xlsx'
    excel_write(p, df)
    ws = sheet(p)
    assert ws['A3'].value is None
    assert ws['B2'].number_format == 'yyyy-mm-dd'
    for c in 'ABC':
        assert 8 <= width(ws, c) <= 60


def test_does_not_mutate_input(tmp_path):
    df = pd.DataFrame([[1, 2]], columns=pd.MultiIndex.from_tuples([('a', 'x'), ('a', 'y')]))
    excel_write(tmp_path / 'a.xlsx', df)
    assert isinstance(df.columns, pd.MultiIndex)


def test_import_cost():
    out = subprocess.run(
        [sys.executable, '-c',
         'import sys, oryxflow; print("openpyxl" in sys.modules)'],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'False'


def make_df2():
    df = make_df()
    df['rating'] = ['Green', 'Red']
    df['rank_chg'] = [3, -2]
    df['fips'] = [39580, 6037]
    df['note'] = ['long text ' * 20, 'short']
    return df


def rules(ws):
    out = []
    for cf in ws.conditional_formatting:
        for r in cf.rules:
            out.append((str(cf.sqref), r))
    return out


def test_title_and_notes_shift_header(tmp_path):
    df = make_df2()
    p = tmp_path / 'a.xlsx'
    excel_write(p, df, title='Report - 2026Q2', notes=['a', 'b'])
    ws = sheet(p)
    assert ws['A1'].value == 'Report - 2026Q2'
    assert ws['A1'].font.b
    assert ws['A2'].value == 'a'
    assert ws['A3'].value == 'b'
    assert ws['A5'].value == 'name'
    assert ws['A6'].value == 'a'
    assert ws.freeze_panes == 'A6'
    assert ws.auto_filter.ref.startswith('A5')
    assert 'A1:F1' in ws.merged_cells
    plain = tmp_path / 'plain.xlsx'
    df.to_excel(plain, index=False)
    pd.testing.assert_frame_equal(pd.read_excel(p, skiprows=4), pd.read_excel(plain))
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, df, title='T', notes=['a', 'b'], freeze=2)
    assert sheet(p2).freeze_panes == 'C6'


def test_title_one_column_not_merged(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, pd.DataFrame({'a': [1]}), title='T')
    assert not sheet(p).merged_cells.ranges


def test_highlight_named_and_raw(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), highlight={
        'rating': {'Green': 'green', 'Red': {'bg': '112233', 'font': 'FFFFFF'}}})
    rs = [(s, r) for s, r in rules(sheet(p)) if r.type == 'cellIs']
    assert len(rs) == 2
    assert all(s == 'G2:G3' for s, _ in rs)
    assert [r.formula for _, r in rs] == [['"Green"'], ['"Red"']]
    assert rs[0][1].dxf.fill.bgColor.rgb.endswith('C6EFCE')
    assert rs[0][1].dxf.font.color.rgb.endswith('006100')


def test_highlight_bad_fill_raises(tmp_path):
    p = tmp_path / 'a.xlsx'
    with pytest.raises(KeyError, match='green'):
        excel_write(p, make_df2(), highlight={'rating': {'Green': 'grean'}})
    assert not p.exists()


def test_unknown_column_raises(tmp_path):
    p = tmp_path / 'a.xlsx'
    with pytest.raises(KeyError, match='nope'):
        excel_write(p, make_df2(), colorscale=['nope'])
    assert not p.exists()


def test_colorscale(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), colorscale=['rank_chg'])
    rs = [(s, r) for s, r in rules(sheet(p)) if r.type == 'colorScale']
    assert len(rs) == 1
    s, r = rs[0]
    assert s == 'H2:H3'
    assert r.colorScale.cfvo[1].type == 'num'
    assert float(r.colorScale.cfvo[1].val) == 0


def test_banded_after_value_rules(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), banded=True, highlight={'rating': {'Green': 'green'}})
    rs = rules(sheet(p))
    band = [r for _, r in rs if r.type == 'expression']
    hl = [r for _, r in rs if r.type == 'cellIs']
    assert len(band) == 1 and hl
    assert band[0].priority > max(r.priority for r in hl)
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, make_df2())
    assert not [r for _, r in rules(sheet(p2)) if r.type == 'expression']


def test_wrap_columns_top_align(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), wrap_columns=['note'], width_max=40)
    ws = sheet(p)
    assert ws['J2'].alignment.wrap_text
    assert width(ws, 'J') == 40
    assert ws['A2'].alignment.vertical == 'top'
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, make_df2())
    assert sheet(p2)['A2'].alignment.vertical != 'top'


def test_group_starts_border(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), group_starts=['value'])
    ws = sheet(p)
    assert ws['C1'].border.left.style == 'medium'
    assert ws['C2'].border.left.style == 'medium'
    assert ws['B2'].border.left.style is None


def test_id_columns_text(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df2(), id_columns=['fips'])
    c = sheet(p)['I2']
    assert c.data_type == 's'
    assert c.value == '39580'
    assert c.alignment.horizontal == 'right'
    assert c.number_format == '@'


def test_deliverable_end_to_end(tmp_path):
    df = make_df2()
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
    p = tmp_path / 'a.xlsx'
    excel_write(p, sheets)
    wb = openpyxl.load_workbook(p)
    assert wb.sheetnames == ['Retail', 'Notes']
    ws = wb['Retail']
    assert ws['A4'].value == 'name'
    assert ws.freeze_panes == 'C5'
    assert ws['D5'].number_format == '0.0%'
    assert ws['H5'].number_format == '+0;-0;0'
    assert ws['I5'].data_type == 's'
    types = [r.type for _, r in rules(ws)]
    assert types.count('cellIs') == 2
    assert 'colorScale' in types and 'expression' in types
    assert not wb['Notes'].auto_filter.ref
    assert wb['Notes'].freeze_panes == 'B4'
    plain = tmp_path / 'plain.xlsx'
    df.to_excel(plain, index=False)
    got = pd.read_excel(p, sheet_name='Retail', skiprows=3)
    want = pd.read_excel(plain)
    pd.testing.assert_frame_equal(got.drop(columns='fips'), want.drop(columns='fips'))
    # read_excel infers numbers from text cells unless told otherwise
    got = pd.read_excel(p, sheet_name='Retail', skiprows=3, dtype={'fips': str})
    assert list(got['fips']) == ['39580', '6037']


def _with_dir(tmp_path, fn):
    import oryxflow
    old_dir, old_path = oryxflow.settings.dir, oryxflow.settings.dirpath
    oryxflow.set_dir(str(tmp_path / 'data'))
    try:
        fn()
    finally:
        oryxflow.settings.dir = old_dir
        oryxflow.settings.dirpath = old_path


def test_task_formatted_save(tmp_path):
    import oryxflow
    import oryxflow.tasks

    class TaskFmtSheets(oryxflow.tasks.TaskExcelPandas):
        persists = ['summary', 'detail']

        def run(self):
            df = make_df()
            self.save({'summary': df, 'detail': df}, formatted=True, formats={'ret_*': 'pct'})

    def go():
        oryxflow.run(TaskFmtSheets())
        out = TaskFmtSheets().outputLoad(as_dict=True)
        plain = tmp_path / 'plain.xlsx'
        make_df().to_excel(plain, index=False)
        pd.testing.assert_frame_equal(out['summary'], pd.read_excel(plain))
        path = TaskFmtSheets().output().path
        ws = openpyxl.load_workbook(path)['summary']
        assert ws.auto_filter.ref
        col = [c.column_letter for c in ws[1] if c.value == 'ret_pct'][0]
        assert ws[col + '2'].number_format == '0.0%'

    _with_dir(tmp_path, go)


def test_task_single_formatted_save(tmp_path):
    import oryxflow
    import oryxflow.tasks

    class TaskFmtSingle(oryxflow.tasks.TaskExcelPandasSingle):
        def run(self):
            self.save(make_df(), formatted=True)

    def go():
        oryxflow.run(TaskFmtSingle())
        wb = openpyxl.load_workbook(TaskFmtSingle().output().path)
        assert wb.sheetnames == ['Sheet1']
        assert wb['Sheet1'].auto_filter.ref

    _with_dir(tmp_path, go)


def test_task_formatted_typo_raises(tmp_path):
    import oryxflow
    import oryxflow.tasks

    class TaskFmtTypo(oryxflow.tasks.TaskExcelPandasSingle):
        def run(self):
            self.save(make_df(), formatted=True, formats={'value': 'pctt'})

    def go():
        with pytest.raises(RuntimeError) as e:
            oryxflow.run(TaskFmtTypo())
        assert isinstance(e.value.__cause__, KeyError)

    _with_dir(tmp_path, go)


def test_unformatted_path_unchanged(tmp_path):
    from oryxflow.targets import ExcelPandasSheetsTarget
    p = tmp_path / 'x.xlsx'
    ExcelPandasSheetsTarget(p).save({'a': make_df()})
    ws = openpyxl.load_workbook(p)['a']
    assert ws.auto_filter.ref is None
    assert ws.freeze_panes is None


def test_notes_bare_string(tmp_path):
    p = tmp_path / 'n.xlsx'
    excel_write(p, make_df(), title='T', notes='only note')
    ws = sheet(p)
    assert ws['A2'].value == 'only note'
    assert ws['A4'].value == 'name'
    assert ws.auto_filter.ref.startswith('A4')
    p2 = tmp_path / 'n2.xlsx'
    excel_write(p2, {'s': {'df': make_df(), 'notes': 'per sheet'}})
    assert sheet(p2)['A2'].value == 'per sheet'
    assert sheet(p2)['A3'].value is None


def test_id_columns_float_codes(tmp_path):
    p = tmp_path / 'i.xlsx'
    excel_write(p, pd.DataFrame({'code': [39580.0, np.nan], 'x': [1, 2]}), id_columns=['code'])
    ws = sheet(p)
    assert ws['A2'].value == '39580'
    assert ws['A3'].value is None


def test_formats_unmatched_key_validated(tmp_path):
    p = tmp_path / 'f.xlsx'
    with pytest.raises(KeyError):
        excel_write(p, make_df(), formats={'valeu': 'pctt'})
    assert not p.exists()


def test_tz_aware_column_rejected(tmp_path):
    p = tmp_path / 'tz.xlsx'
    df = pd.DataFrame({'t': pd.to_datetime(['2026-01-01']).tz_localize('UTC')})
    with pytest.raises(ValueError, match="'t'.*tz_localize"):
        excel_write(p, df)
    assert not p.exists()


def test_highlight_numeric_key(tmp_path):
    p = tmp_path / 'h.xlsx'
    excel_write(p, pd.DataFrame({'r': [1, 2]}), highlight={'r': {1: 'green', 'x': 'red'}})
    cf = sheet(p).conditional_formatting
    formulas = sorted(r.formula[0] for c in cf for r in c.rules)
    assert formulas == ['"x"', '1']


def test_freeze_out_of_range(tmp_path):
    p = tmp_path / 'z.xlsx'
    with pytest.raises(ValueError, match='freeze'):
        excel_write(p, make_df(), freeze=99)
    with pytest.raises(ValueError, match='freeze'):
        excel_write(p, make_df(), freeze=-1)
    assert not p.exists()


def test_highlight_bad_type(tmp_path):
    p = tmp_path / 'ht.xlsx'
    with pytest.raises(TypeError, match='highlight'):
        excel_write(p, make_df(), highlight=['value'])
    with pytest.raises(TypeError, match='highlight'):
        excel_write(p, make_df(), highlight={'value': 'green'})
    assert not p.exists()


def test_sheet_formats_merge_over_shared(tmp_path):
    df = make_df()
    p = tmp_path / 'a.xlsx'
    excel_write(p, {'a': df,
                    'b': {'df': df, 'formats': {'value': '0.000', 'year': None}},
                    'c': {'df': df, 'formats': {'*': 'text'}},
                    'd': {'df': df, 'formats': False}},
                formats={'ret_*': 'pct', 'year': 'int', 'value': 'int'})
    wb = openpyxl.load_workbook(p)
    assert wb['a']['D2'].number_format == '0.0%' and wb['a']['C2'].number_format == '#,##0'
    b = wb['b']
    assert b['D2'].number_format == '0.0%'                 # shared key kept
    assert b['C2'].number_format == '0.000'                # the sheet's key wins
    assert b['B2'].number_format == 'General'              # None drops the shared format
    assert wb['c']['D2'].number_format == '@'              # the sheet's glob beats the shared one
    assert wb['d']['D2'].number_format == 'General'


def test_file_handle(tmp_path):
    import os
    p = tmp_path / 'a.xlsx'
    tmp = tmp_path / 'a.xlsx.tmp'
    with open(tmp, 'wb') as f:
        excel_write(f, make_df())
    os.replace(tmp, p)
    assert sheet(p)['D1'].value == 'ret_pct'


def test_shared_column_options_skip_sheets_without_the_column(tmp_path):
    df = make_df()
    other = pd.DataFrame({'note': ['x']})
    p = tmp_path / 'a.xlsx'
    excel_write(p, {'a': df, 'b': other}, wrap_columns=['name'], id_columns=['name'],
                highlight={'name': {'a': 'green'}}, colorscale=['value'])
    wb = openpyxl.load_workbook(p)
    assert wb['a']['A2'].alignment.wrap_text and wb['a']['A2'].number_format == '@'
    with pytest.raises(KeyError, match='nope'):                # in no sheet: still a typo
        excel_write(tmp_path / 'b.xlsx', {'a': df, 'b': other}, wrap_columns=['name', 'nope'])
    with pytest.raises(KeyError, match='note'):                # a sheet's own option stays strict
        excel_write(tmp_path / 'c.xlsx', {'a': {'df': df, 'wrap_columns': ['note']}, 'b': other})


def test_format_float(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df(), format_float='pct')
    ws = sheet(p)
    assert ws['C2'].number_format == '0.0%'
    assert ws['D2'].number_format == '0.0%'
    assert ws['B2'].number_format == 'General'
    assert ws['A2'].number_format == 'General'
    assert ws['E2'].number_format == 'yyyy-mm-dd'


def test_format_float_formats_win(tmp_path):
    p = tmp_path / 'a.xlsx'
    excel_write(p, make_df(), format_float='pct', formats={'value': 'num1', 'year': 'int'})
    ws = sheet(p)
    assert ws['C2'].number_format == '#,##0.0'
    assert ws['B2'].number_format == '#,##0'
    assert ws['D2'].number_format == '0.0%'
    p2 = tmp_path / 'b.xlsx'
    excel_write(p2, make_df(), format_float='pct', formats=False)
    assert sheet(p2)['D2'].number_format == 'General'


def test_format_float_sheet_override(tmp_path):
    df = make_df()
    p = tmp_path / 'a.xlsx'
    excel_write(p, {'a': df, 'b': {'df': df, 'format_float': 'float'},
                    'c': {'df': df, 'format_float': '0.000'}}, format_float='pct')
    wb = openpyxl.load_workbook(p)
    assert wb['a']['C2'].number_format == '0.0%'
    assert wb['b']['C2'].number_format == '#,##0.00'
    assert wb['c']['C2'].number_format == '0.000'
    with pytest.raises(KeyError):
        excel_write(tmp_path / 'd.xlsx', df, format_float='pctt')


def test_format_float_none_is_default(tmp_path):
    p, p2 = tmp_path / 'a.xlsx', tmp_path / 'b.xlsx'
    excel_write(p, make_df())
    excel_write(p2, make_df(), format_float=None)
    a, b = sheet(p), sheet(p2)
    for c in 'ABCDEF':
        assert a[c + '2'].number_format == b[c + '2'].number_format
