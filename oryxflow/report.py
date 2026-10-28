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
    'pct1': Format('{:.1%}', '0.0%'),
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


def resolve_formats(df, formats=None, defaults=True, float_default=None):
    """Resolve the format of each column of df as ``{column: spec}``.

    formats: ``{column_or_glob: spec}``; False disables all formatting. An exact column key
    beats a glob key (``*``, ``?``, ``[``). With defaults=True, columns without a match get
    float_default (float dtype; None means ``'float'``), ``'date'`` or ``'datetime'``
    (datetime dtype).
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
            out[col] = float_default or 'float'
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


def _is_numeric(s):
    # a transposed frame holds floats in object columns: still numbers, still right-aligned
    if pd.api.types.is_bool_dtype(s):
        return False
    if pd.api.types.is_numeric_dtype(s):
        return True
    return s.dtype == object and pd.api.types.infer_dtype(s, skipna=True) in (
        'floating', 'integer', 'mixed-integer-float', 'decimal')


def table(df, formats=None, index='auto', rename=None, columns=None, caption=None, na_rep=NA_REP):
    """Return a pandas Styler showing df with the same ``formats`` dict as the Excel writer.

    columns selects and orders columns, then rename relabels them; formats are keyed by the
    renamed labels. index='auto' hides a default RangeIndex and keeps a meaningful one;
    True/False force it. A named index is shown as ordinary leading columns, headed by its
    name (rename and formats can use it), so the table gets no extra header row. Needs jinja2 (``oryxflow[notebook]``); ``format_frame`` does not.
    """
    try:
        import jinja2  # noqa: F401
    except ImportError:
        raise ImportError('report.table needs jinja2: install oryxflow[notebook], '
                          'or use report.format_frame, which works without it')
    if columns is not None:
        df = df[list(columns)]
    if index is not False and any(n is not None for n in df.index.names):
        # a named index would get its own header row, blank under the columns: show it as
        # ordinary leading columns instead
        df = df.reset_index()
        index = False
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
    numeric = [c for c in df.columns if _is_numeric(df[c])]
    if numeric:
        styler = styler.set_properties(subset=numeric, **{'text-align': 'right'})
    return styler


def _target_entry(target, key):
    from oryxflow.targets import CacheTarget
    if isinstance(target, CacheTarget):
        raise TypeError('input {!r} is an in-memory (cache) target, which a report notebook '
                        'cannot reach; use a persisted task type such as TaskPqPandas'.format(key))
    import pathlib
    cls = type(target)
    return {'target': '{}.{}'.format(cls.__module__, cls.__qualname__),
            'path': str(pathlib.Path(target.path).resolve())}


def _dep_entry(dep, key):
    if isinstance(dep, dict):
        return {'persists': {str(k): _target_entry(v, '{}.{}'.format(key, k)) for k, v in dep.items()}}
    return _target_entry(dep, key)


def inputs_of(task):
    """Describe a task's upstream inputs as a JSON-safe dict for a report notebook.

    Returns ``{name: {'target': 'module.Class', 'path': str}}``, with ``{'persists': {...}}`` for a
    dependency that saves several outputs. A single unnamed dependency is keyed ``'0'`` and list
    dependencies ``'0'``, ``'1'``, ... ``TaskNotebook`` injects this as ``oryxflow_inputs``, and
    ``inputLoad`` reads it back without any Task objects.
    """
    import oryxflow.settings
    if oryxflow.settings.cloud_fs_enabled:
        raise NotImplementedError('report.inputs_of does not support cloud storage yet')
    inp = task.input()
    requires = task.requires()
    if isinstance(requires, dict):
        items = [(str(k), v) for k, v in inp.items()]
    elif isinstance(requires, (list, tuple)):
        items = [(str(i), v) for i, v in enumerate(inp)]
    else:
        items = [('0', inp)]
    return {k: _dep_entry(v, k) for k, v in items}


def default_inputs(task, **params):
    """The inputs a report notebook should use: the ``oryxflow_inputs`` injected by
    ``TaskNotebook``, or, when the notebook is run by hand, those of ``task``.

    task is a report task instance, or its class plus the parameters to build it with (pass
    ``**flow.params`` too when the task carries flow parameters). Put it right after the
    parameters cell, so the same template runs under the flow and by hand in Jupyter::

        oryxflow_inputs = rpt.default_inputs(tasks.RegionReport, region=region)
    """
    injected = _injected_inputs()
    if injected is not None:
        return injected
    if isinstance(task, type):
        task = task(**params)
    elif params:
        raise TypeError('default_inputs: pass parameters with the task class, not an instance')
    missing = [str(t.path) for t in _flat_targets(task.input()) if not t.exists()]
    if missing:
        raise FileNotFoundError('{} inputs are not built yet ({}); run the flow first'.format(
            task.task_id, ', '.join(missing[:3])))
    return inputs_of(task)


def _flat_targets(inp):
    if isinstance(inp, dict):
        return [t for v in inp.values() for t in _flat_targets(v)]
    if isinstance(inp, (list, tuple)):
        return [t for v in inp for t in _flat_targets(v)]
    return [inp]


def _injected_inputs():
    import sys
    try:
        from IPython import get_ipython
        shell = get_ipython()
    except ImportError:
        shell = None
    ns = shell.user_ns if shell is not None else vars(sys.modules['__main__'])
    return ns.get('oryxflow_inputs')


def _load_entry(entry, keys=None):
    import importlib
    import inspect
    import pathlib
    mod, _, name = entry['target'].rpartition('.')
    cls = getattr(importlib.import_module(mod), name)
    target = cls(pathlib.Path(entry['path']))
    if keys is not None and 'keys' in inspect.signature(target.load).parameters:
        return target.load(keys=keys)
    return target.load()


class Outputs(list):
    """The outputs of a multi-output dependency, in its ``persists`` order. A list, so
    ``a, b = rpt.inputLoad(task='x')`` unpacks the data as ``self.inputLoad`` does, that also
    takes an output's name: ``out['train']``, ``out.keys()``, ``out.items()``."""

    def __init__(self, items):
        items = list(items)
        super().__init__(v for _, v in items)
        self._names = [k for k, _ in items]

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return list.__getitem__(self, self._names.index(key))
            except ValueError:
                raise KeyError('{!r}; outputs are {}'.format(key, ', '.join(self._names))) from None
        return list.__getitem__(self, key)

    def keys(self):
        return list(self._names)

    def values(self):
        return list(self)

    def items(self):
        return list(zip(self._names, self))


def _load_dep(entry, keys):
    if 'persists' not in entry:
        return _load_entry(entry, keys)
    persists = entry['persists']
    if keys is None:
        return Outputs((k, _load_entry(v)) for k, v in persists.items())
    if isinstance(keys, str):
        return _load_entry(persists[keys])
    return [_load_entry(persists[k]) for k in keys]


def inputLoad(spec=None, task=None, keys=None):
    """Load a dependency's data from an ``inputs_of`` spec, like ``self.inputLoad`` in a task.

    spec defaults to the ``oryxflow_inputs`` variable injected into the notebook. task picks one
    dependency by name (or position); with none, one dependency is loaded directly and several
    come back as ``{name: data}``. keys picks outputs of a multi-output dependency: a string gives
    one value, a list gives a list in that order, None gives all of them as ``Outputs``: a list in
    ``persists`` order, as ``self.inputLoad`` returns, that also takes a name (``out['train']``).
    A single-file target that selects by key itself, such as the sheets of a ``TaskExcelPandas``
    file, gets ``keys`` passed through. task takes a name or an index, not a class.
    """
    if spec is None:
        spec = _injected_inputs()
    if spec is None:
        raise ValueError(
            'no inputs available: a report notebook run by TaskNotebook gets oryxflow_inputs '
            'injected; to run it by hand, add after the parameters cell\n'
            '    oryxflow_inputs = rpt.default_inputs(tasks.MyReport, param=value, ...)\n'
            'or pass the spec: rpt.inputLoad(spec, task=...)')
    if task is not None:
        return _load_dep(spec[str(task)], keys)
    if len(spec) == 1:
        return _load_dep(next(iter(spec.values())), keys)
    return {k: _load_dep(v, keys) for k, v in spec.items()}
