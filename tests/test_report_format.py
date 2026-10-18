import sys
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


def test_empty_spec_rejected():
    from oryxflow import report
    with pytest.raises(KeyError):
        report.excel_code('')
    with pytest.raises(KeyError):
        report.fmt(1, '')


def test_format_frame():
    df = pd.DataFrame({'r': [0.05, float('nan')], 'n': [1234, 5]})
    before = df.copy()
    out = rpt.format_frame(df, {'r': 'pct', 'n': 'int'})
    assert out.values.tolist() == [['5.0%', '1,234'], ['-', '5']]
    pd.testing.assert_frame_equal(df, before)


def test_table_hides_range_index():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'r': [0.05, 0.1]})
    assert 'row_heading' not in rpt.table(df).to_html()
    df2 = pd.DataFrame({'r': [0.05, 0.1]}, index=['a', 'b']).rename_axis('sector')
    assert 'row_heading' in rpt.table(df2).to_html()
    assert 'row_heading' in rpt.table(df, index=True).to_html()


def test_table_rename_then_format():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'r': [0.05]})
    html = rpt.table(df, formats={'Return': 'pct'}, rename={'r': 'Return'}).to_html()
    assert '5.0%' in html and 'Return' in html


def test_table_glob_formats():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'ret_pct': [0.05]})
    assert '5.0%' in rpt.table(df, formats={'*_pct': 'pct'}).to_html()


def test_table_same_formats_as_excel():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'n': [1234000]})
    assert '1,234k' in rpt.table(df, formats={'n': 'thousands'}).to_html()


def test_table_mixed_types():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'r': [0.05, 'n/a']})
    html = rpt.table(df, formats={'r': 'pct'}).to_html()
    assert '5.0%' in html and 'n/a' in html


def test_table_columns_and_caption():
    pytest.importorskip('jinja2')
    df = pd.DataFrame({'a': [1.0], 'b': [2.0]})
    html = rpt.table(df, columns=['a'], caption='Cap').to_html()
    assert 'Cap' in html and '>b<' not in html


def test_table_missing_jinja2(monkeypatch):
    monkeypatch.setitem(sys.modules, 'jinja2', None)
    with pytest.raises(ImportError, match='format_frame'):
        rpt.table(pd.DataFrame({'a': [1.0]}))
