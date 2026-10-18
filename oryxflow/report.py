"""
Display-format presets shared by the Excel writer and notebook report tables.

One table, ``FORMATS``, maps a preset name to a ``Format(py, excel)`` pair: the Python form
(a ``str.format`` spec or a callable) used when rendering text, and the Excel number-format
code used when writing a workbook. A format value is one of:

- a preset name, such as ``'pct'``;
- a ``str.format`` spec (any string containing ``{``), such as ``'{:,.0f}'``;
- a callable taking the value;
- otherwise a raw Excel number-format code, such as ``'0.000'``.

A string that looks like a preset name but is not one raises ``KeyError`` listing the valid
names. Values that cannot be formatted are shown as ``str(value)`` and never raise.

This module imports only pandas at module level.
"""
import fnmatch
import re
from collections import namedtuple

import pandas as pd

Format = namedtuple('Format', ['py', 'excel'])

NA_REP = '-'


def _int_signed(v):
    return '0' if v == 0 else '{:+.0f}'.format(v)


def _thousands(v):
    return '{:,.0f}k'.format(v / 1000)


FORMATS = {
    'int': Format('{:,.0f}', '#,##0'),
    'int_plain': Format('{:.0f}', '0'),
    'int_signed': Format(_int_signed, '+0;-0;0'),
    'float': Format('{:,.2f}', '#,##0.00'),
    'float4': Format('{:.4f}', '0.0000'),
    'num1': Format('{:,.1f}', '#,##0.0'),
    'num2': Format('{:,.2f}', '#,##0.00'),
    'pct': Format('{:.1%}', '0.0%'),
    'pct0': Format('{:.0%}', '0%'),
    'pct2': Format('{:.2%}', '0.00%'),
    'pct_signed': Format('{:+.1%}', '+0.0%;-0.0%;0.0%'),
    'multiple': Format('{:.2f}x', '0.00"x"'),
    'thousands': Format(_thousands, '#,##0,"k"'),
    'date': Format('{:%Y-%m-%d}', 'yyyy-mm-dd'),
    'datetime': Format('{:%Y-%m-%d %H:%M}', 'yyyy-mm-dd hh:mm'),
    'text': Format('{}', '@'),
}

_PRESET_LIKE = re.compile(r'^[a-z][a-z_0-9]*$')


def _check_spec(spec):
    """Raise KeyError if spec looks like a preset name but is not one."""
    if (isinstance(spec, str) and '{' not in spec
            and (spec == '' or (_PRESET_LIKE.match(spec) and spec not in FORMATS))):
        raise KeyError('unknown format %r; valid names: %s' % (spec, ', '.join(FORMATS)))


def excel_code(spec):
    """Excel number-format code for a format value, or None if it has no Excel form."""
    _check_spec(spec)
    if not isinstance(spec, str):
        return None
    if spec in FORMATS:
        return FORMATS[spec].excel
    if '{' in spec:
        return None
    return spec


def _py(spec):
    """Python form (format string or callable) of a format value, or None for a raw Excel code."""
    _check_spec(spec)
    if callable(spec):
        return spec
    if not isinstance(spec, str):
        return None
    if spec in FORMATS:
        return FORMATS[spec].py
    if '{' in spec:
        return spec
    return None


def fmt(value, spec='num1', na_rep=NA_REP):
    """Format one value with a preset name, ``str.format`` spec or callable.

    Missing values give ``na_rep``; a value the spec cannot format gives ``str(value)``.
    """
    py = _py(spec)
    if not isinstance(value, str) and pd.api.types.is_scalar(value) and pd.isna(value):
        return na_rep
    if py is None:
        return str(value)
    try:
        return py(value) if callable(py) else py.format(value)
    except (ValueError, TypeError):
        return str(value)


def resolve_formats(df, formats=None, defaults=True):
    """Resolve the format of each column of df as ``{column: spec}``.

    formats: ``{column_or_glob: spec}``; False disables all formatting. An exact column key
    beats a glob key (``*``, ``?``, ``[``). With defaults=True, columns without a match get
    ``'float'`` (float dtype), ``'date'`` or ``'datetime'`` (datetime dtype).
    """
    if formats is False:
        return {}
    formats = formats or {}
    globs = [(k, v) for k, v in formats.items()
             if isinstance(k, str) and any(c in k for c in '*?[')]
    out = {}
    for col in df.columns:
        if col in formats:
            out[col] = formats[col]
            continue
        hit = next((v for k, v in globs if fnmatch.fnmatchcase(str(col), k)), None)
        if hit is not None:
            out[col] = hit
            continue
        if not defaults:
            continue
        s = df[col]
        if pd.api.types.is_float_dtype(s):
            out[col] = 'float'
        elif pd.api.types.is_datetime64_any_dtype(s):
            nn = s.dropna()
            out[col] = 'date' if (nn.dt.normalize() == nn).all() else 'datetime'
    return out


def format_frame(df, formats=None, na_rep=NA_REP):
    """Return a copy of df as strings, formatted with the same ``formats`` dict as the Excel writer.

    Columns without a format are shown with ``str()``; missing values give ``na_rep``. Needs no
    jinja2, so it suits markdown and prose tables.
    """
    specs = resolve_formats(df, formats, defaults=False)
    out = df.copy()
    for col in df.columns:
        spec = specs.get(col)
        out[col] = [fmt(v, spec, na_rep) if spec is not None else
                    (na_rep if (not isinstance(v, str) and pd.api.types.is_scalar(v) and pd.isna(v))
                     else str(v)) for v in df[col]]
    return out


def table(df, formats=None, index='auto', rename=None, columns=None, caption=None, na_rep=NA_REP):
    """Return a pandas Styler showing df with the same ``formats`` dict as the Excel writer.

    columns selects and orders columns, then rename relabels them; formats are keyed by the
    renamed labels. index='auto' hides a default RangeIndex and keeps a meaningful one;
    True/False force it. Needs jinja2 (``oryxflow[notebook]``); ``format_frame`` does not.
    """
    try:
        import jinja2  # noqa: F401
    except ImportError:
        raise ImportError('report.table needs jinja2: install oryxflow[notebook], '
                          'or use report.format_frame, which works without it')
    if columns is not None:
        df = df[list(columns)]
    if rename:
        df = df.rename(columns=rename)
    specs = resolve_formats(df, formats, defaults=False)
    for spec in specs.values():
        _py(spec)
    formatter = {c: (lambda v, s=s: fmt(v, s, na_rep)) for c, s in specs.items()}
    styler = df.style.format(formatter=formatter, na_rep=na_rep) if formatter else df.style.format(na_rep=na_rep)
    if index is False or (index == 'auto' and isinstance(df.index, pd.RangeIndex) and df.index.name is None):
        styler = styler.hide(axis='index')
    if caption:
        styler = styler.set_caption(caption)
    styler = styler.set_table_styles([{'selector': 'th', 'props': [('font-weight', 'bold')]}])
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if numeric:
        styler = styler.set_properties(subset=numeric, **{'text-align': 'right'})
    return styler
