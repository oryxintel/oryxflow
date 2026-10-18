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
