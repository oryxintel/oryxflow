from oryxflow.core import flatten
import os
import warnings
import pathlib
import hashlib


class bcolors:
    '''
    colored output for task status
    '''
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    ENDC = '\033[0m'


def print_tree(task, indent='', last=True, show_params=True, clip_params=False):
    '''
    Return a string representation of the tasks, their statuses/parameters in a dependency tree format
    '''
    # dont bother printing out warnings about tasks with no output
    with warnings.catch_warnings():
        warnings.filterwarnings(action='ignore', message='Task .* without outputs has no custom complete\\(\\) method')
        is_task_complete = task.complete()
    is_complete = (bcolors.OKGREEN + 'COMPLETE' if is_task_complete else bcolors.OKBLUE + 'PENDING') + bcolors.ENDC
    name = task.__class__.__name__
    if show_params:
        params = task.to_str_params(only_significant=True)
        if len(params)>1 and clip_params:
            params = next(iter(params.items()), None)  # keep only one param
            params = str(dict([params]))+'[more]'
    else:
        params = ''
    result = '\n' + indent
    if(last):
        result += '+--'
        indent += '   '
    else:
        result += '|--'
        indent += '|  '
    result += '[{0}-{1} ({2})]'.format(name, params, is_complete)
    children = flatten(task.requires())
    for index, child in enumerate(children):
        result += print_tree(child, indent, (index+1) == len(children),
                             show_params=show_params, clip_params=clip_params)
    return result


def traverse(t, path=None):
    '''
    Get upstream dependencies
    '''
    if path is None: path = []
    path = path + [t]
    for node in flatten(t.requires()):
        if not node in path:
            path = traverse(node, path)
    return path


def to_parquet(df, path, **kwargs):
    opts = {**{'compression': 'gzip', 'engine': 'pyarrow'}, **kwargs}
    pathlib.Path(path).parent.mkdir(exist_ok=True)
    df.to_parquet(path, **opts)


def generate_exps_for_multi_param(params_dict, current_key = 0, multi_exp_dict = {}):
    current_multi_exp_dict = {}
    permutation_keys_list = list(params_dict.keys())
    permutation_keys_list.sort()
    input_key = permutation_keys_list[current_key]
    for current_key_val in params_dict[input_key]:
        if current_key == 0:
            current_key_val_multi_exp_dict = {f'{input_key}_{current_key_val}': {f'{input_key}' : current_key_val}}
            current_key_val_multi_exp_dict_results = generate_exps_for_multi_param(params_dict, current_key = current_key + 1, multi_exp_dict = current_key_val_multi_exp_dict)
            current_multi_exp_dict = {**current_multi_exp_dict, **current_key_val_multi_exp_dict_results}
        if current_key == len(permutation_keys_list) - 1:
            current_multi_exp_dict = {**current_multi_exp_dict, **{f'{k}_{input_key}_{current_key_val}': {**v, **{f'{input_key}' : current_key_val}} for k,v in multi_exp_dict.items()}}
        else:
            current_key_val_multi_exp_dict = {f'{k}_{input_key}_{current_key_val}': {**v, **{f'{input_key}' : current_key_val}} for k,v in multi_exp_dict.items()}
            current_key_val_multi_exp_dict_results = generate_exps_for_multi_param(params_dict, current_key = current_key + 1, multi_exp_dict = current_key_val_multi_exp_dict)
            current_multi_exp_dict = {**current_multi_exp_dict, **current_key_val_multi_exp_dict_results}
    return current_multi_exp_dict

params_generator_multiple = generate_exps_for_multi_param

def params_generator_single(dict_,params_base=None):
    # example input: {'a':[1,2,3]}
    key,list_=list(dict_.items())[0]

    params = {}
    for i, v in enumerate(list_):
        params[i] = {**params_base, **{key: v}} if params_base is not None else {key: v}

    return params

def params_generator_df(df, params_base = None) -> dict:
    params = {}
    for i, row in df.dropna().iterrows():
        row_dict = row.to_dict()
        combined = {**params_base, **row_dict} if params_base else row_dict
        params[i] = combined
    return params


def params_generator_dictlist(params_dict, params_base=None):
    """
    Generate permutations of parameter values from a dictionary of lists.
    
    Args:
        params_dict (dict): Dictionary where keys are parameter names and values are lists of possible values
        params_base (dict, optional): Base parameters to be added to all combinations
        
    Returns:
        dict: Dictionary where keys are iteration numbers and values are dictionaries of parameter combinations
        
    Example:
        params_values1 = ['a', 'b']
        params_values2 = ['c', 'd']
        params = {'param1': params_values1, 'param2': params_values2}
        params_base = {'base_param': 'value'}
        result = params_generator_dictlist(params, params_base)
        # Returns: {0: {'param1': 'a', 'param2': 'c', 'base_param': 'value'}, ...}
    """
    # Get all parameter names and their possible values
    param_names = list(params_dict.keys())
    param_values = list(params_dict.values())
    
    # Calculate total number of combinations
    total_combinations = 1
    for values in param_values:
        total_combinations *= len(values)
    
    # Initialize result dictionary
    result = {}
    
    # Generate all combinations
    for i in range(total_combinations):
        combination = {}
        temp = i
        for j, values in enumerate(param_values):
            idx = temp % len(values)
            combination[param_names[j]] = values[idx]
            temp //= len(values)
        # Merge with params_base if provided
        if params_base is not None:
            combination = {**params_base, **combination}
        result[i] = combination
    
    return result

def _tag_changes_data(col, val):
    """True if overwriting `col` with `val` would lose information. Comparison failures
    (odd dtypes) count as 'changes' -- better a spurious warning than a silent overwrite."""
    try:
        return bool((col != val).any())
    except Exception:
        return True


def concat_iter(items, concat_fn=None, keys=None, ignore_index=True):
    """Stack an iterable of (identifier, params, data) triples into one DataFrame.
    params: dict of raw values -> added as columns by default (groupby keys survive).
    data: a single DataFrame, or a list/dict of DataFrames (multi-persists).
    concat_fn(identifier, params, df)->df: hook called per frame instead of default tagging.
    keys: subset of param names to tag (default all)."""
    import pandas as pd
    frames = []
    clashed = set()
    for identifier, params, data in items:
        subframes = list(data.values()) if isinstance(data, dict) \
            else list(data) if isinstance(data, (list, tuple)) else [data]
        params = params or {}
        for df in subframes:
            if concat_fn is not None:
                df = concat_fn(identifier, params, df)
            else:
                df = df.copy()                       # avoid mutating cached inputs
                tagcols = params if keys is None else {k: params[k] for k in keys if k in params}
                for col, val in tagcols.items():
                    # A param whose name is already a data column: tagging replaces real
                    # per-row values with one scalar. Warn -- silently is how that hurts.
                    # Not when every existing value already equals the tag: re-tagging with
                    # the same value is how multi-level aggregation legitimately works.
                    if col in df.columns and col not in clashed and _tag_changes_data(df[col], val):
                        clashed.add(col)
                        warnings.warn(
                            "concat: column '{}' already holds data and is being overwritten by the "
                            "'{}' parameter value. Pass tagkeys=[...] to tag only the params you "
                            "want, or tag=False for none.".format(col, col),
                            UserWarning, stacklevel=3)
                    df[col] = val
            frames.append(df)
    return pd.concat(frames, ignore_index=ignore_index) if frames else pd.DataFrame()


def apply_noise(dfg, cfg_cols, seed=123):
    import numpy as np
    dfg = dfg.copy() # return a copy
    dfc = dfg.copy()
    np.random.seed(seed)
    for col in cfg_cols:
        noise = np.random.uniform(0.25, 3, size=len(dfg))
        dfg[col] = dfg[col] * noise
        idxSel = (dfg[col]==0) | (dfg[col].isna())
        idxSel = ~idxSel
        assert (noise==1).sum()==0
        assert idxSel.sum()>0, f'column {col} has no values that should be different'
        assert (dfc.loc[idxSel,col] != dfg.loc[idxSel,col]).all(), f'column {col} not all different'

    return dfg


def hash_files(*patterns, root=None):
    """Stable digest of the CONTENT of every file matching these glob patterns.

    For use inside a task's ``code_version()`` when the bytes that determine its
    result are files -- SQL, templates, config -- rather than Python. Paths are
    resolved relative to ``root`` (default: the current working directory) and
    sorted, so the digest does not depend on filesystem enumeration order.

    Raises FileNotFoundError when a pattern matches nothing: a typo'd path that
    silently hashed to "no files" would report every run as unchanged, which is
    the failure this exists to prevent.
    """
    base = pathlib.Path(root or '.')
    files = set()
    for pattern in patterns:
        pat = pathlib.Path(pattern)
        if pat.is_absolute():
            # an absolute pattern globs from its own anchor; root does not apply
            found = pathlib.Path(pat.anchor).glob(pat.relative_to(pat.anchor).as_posix())
        else:
            found = base.glob(pattern)
        found = [f for f in found if f.is_file()]  # a glob may also match directories
        if not found:
            raise FileNotFoundError("hash_files: pattern '{}' matched no files (root '{}')".format(pattern, base))
        files.update(found)

    keyed = []
    for f in files:
        try:
            key = f.relative_to(base).as_posix()
        except ValueError:
            key = f.as_posix()
        keyed.append((key, f))

    keyed.sort(key=lambda kv: kv[0])
    # runs on every completeness check: an unchanged file set (same names, sizes, mtimes) is
    # not re-read -- a large data file would otherwise be read in full every time
    stamp = []
    for key, f in keyed:
        st = f.stat()
        stamp.append((key, str(f.resolve()), st.st_size, st.st_mtime_ns))
    stamp = tuple(stamp)
    cached = _hash_files_cache.get(stamp)
    if cached is not None:
        return cached
    h = hashlib.md5()
    for key, f in keyed:
        h.update(key.encode('utf-8'))  # the name is part of the digest: a rename is a change
        h.update(b'\0')
        for chunk in _file_chunks(f):
            h.update(chunk)
        h.update(b'\0')
    digest = h.hexdigest()
    if len(_hash_files_cache) > 256:
        _hash_files_cache.clear()
    _hash_files_cache[stamp] = digest
    return digest


# (key, abspath, size, mtime_ns) per file -> digest (see hash_files)
_hash_files_cache = {}


def _file_chunks(f, size=1 << 20):
    with open(f, 'rb') as fh:
        for chunk in iter(lambda: fh.read(size), b''):
            yield chunk


# resolved output directories already reported, so a re-entrant flow (a task's run()
# calling oryxflow.run()) warns once per process, not once per nested build
_warned_nested_dirs = set()


def _dir_has_content(path):
    '''
    True when path is a directory that holds at least one entry
    '''
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def warn_if_nested_data_dir(dirpath):
    '''
    Warn once when this run would build a second output directory below an existing one.

    The output directory resolves against the current working directory, so running a flow
    from a subdirectory finds an empty directory beside it, reports every task incomplete
    and recomputes everything -- paying again for any metered API call. Only advisory: the
    directory that was resolved is never changed. Silent when the local directory already
    holds output (a deliberate second project), when a .git directory bounds the walk, or
    when settings.warn_nested_dir is False.
    '''
    from oryxflow import settings
    from oryxflow.log import logger

    if not settings.warn_nested_dir:
        return
    try:
        here = pathlib.Path(dirpath).resolve()
    except OSError:
        return
    if here in _warned_nested_dirs or _dir_has_content(here):
        return

    start = here.parent
    if (start / '.git').exists():
        return
    for parent in start.parents:
        candidate = parent / here.name
        if _dir_has_content(candidate):
            _warned_nested_dirs.add(here)
            logger.warning(
                "an oryxflow output directory already exists at {}, and this run will build a "
                "second one at {}. Tasks completed there will be rebuilt from scratch, and any "
                "paid call they make will be paid for again. Run from {}, or set "
                "oryxflow.settings.warn_nested_dir = False if two separate caches are "
                "intended.".format(candidate, here, parent)
            )
            return
        if (parent / '.git').exists():
            return


# ---------------------------------------------------------------------------
# Excel output
# ---------------------------------------------------------------------------

_EXCEL_DEFAULTS = dict(formats=None, index=False, filters=True, freeze=True, widths='auto',
                       width_min=8, width_max=60, header_style=True,
                       title=None, notes=(), highlight={}, colorscale=(), banded=False,
                       wrap_columns=(), group_starts=(), id_columns=())
_EXCEL_FILLS = {'green': ('C6EFCE', '006100'), 'yellow': ('FFEB9C', '9C6500'),
                'red': ('FFC7CE', '9C0006'), 'grey': ('E7E6E6', '3F3F3F')}
_EXCEL_TITLE_COLS = 6
_EXCEL_NOTE_COLS = 12
_EXCEL_WIDTH_SAMPLE = 1000
_EXCEL_FILTER_ARROW = 3
_EXCEL_SHEET_BAD_CHARS = '[]:*?/\\'


def _excel_check_sheet_names(names):
    '''
    Raise ValueError for a sheet name Excel rejects, or for names equal when lower-cased
    (Excel treats them as one sheet).
    '''
    seen = {}
    for name in names:
        s = str(name)
        if not s or len(s) > 31 or any(c in s for c in _EXCEL_SHEET_BAD_CHARS):
            raise ValueError(
                "invalid Excel sheet name {!r}: must be 1-31 characters without any of "
                "{}".format(name, _EXCEL_SHEET_BAD_CHARS))
        key = s.lower()
        if key in seen:
            raise ValueError(
                "Excel sheet names are case-insensitive: {!r} and {!r} clash".format(seen[key], s))
        seen[key] = s


def _excel_flatten_columns(df):
    '''
    Flatten MultiIndex columns to 'a / b' labels. Returns a copy when it changes anything.
    '''
    import pandas as pd
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    df = df.copy()
    df.columns = [' / '.join(str(x) for x in t if str(x) != '') for t in df.columns]
    return df


def _excel_specs(df, index, formats):
    '''
    Resolved format spec of each written column as [(header, values, spec)], index levels first.
    Raises KeyError for a mistyped preset name.
    '''
    from oryxflow import report
    if isinstance(formats, dict):
        for spec in formats.values():
            report.excel_code(spec)
    out = []
    if index:
        idx = df.index.to_frame()
        res = report.resolve_formats(idx, formats)
        for i, col in enumerate(idx.columns):
            name = df.index.names[i]
            out.append(('' if name is None else str(name), idx.iloc[:, i], res.get(col)))
    res = report.resolve_formats(df, formats)
    for j, col in enumerate(df.columns):
        out.append((str(col), df.iloc[:, j], res.get(col)))
    for _, _, spec in out:
        if spec is not None:
            report.excel_code(spec)
    return out


def _excel_isna(v):
    import pandas as pd
    return not isinstance(v, str) and pd.api.types.is_scalar(v) and pd.isna(v)


def _excel_id_text(v):
    '''
    Text of an id value: a whole-number float (an int code column holding NaN) loses its '.0'.
    '''
    from oryxflow import report
    if isinstance(v, float) and v.is_integer():
        return report.fmt(v, 'int_plain')
    return str(v)


def _excel_fill(fill):
    '''
    (bg, font) colours of a highlight fill: a name in _EXCEL_FILLS or {'bg': .., 'font': ..}.
    '''
    if isinstance(fill, dict):
        if 'bg' not in fill:
            raise KeyError("highlight fill dict needs a 'bg' key, got {}".format(fill))
        return fill['bg'], fill.get('font')
    if fill not in _EXCEL_FILLS:
        raise KeyError("unknown highlight fill {!r}; valid names: {}".format(
            fill, ', '.join(_EXCEL_FILLS)))
    return _EXCEL_FILLS[fill]


def _excel_check_styling(sheet_name, df, opts):
    '''
    Raise KeyError for a styling option naming a column the frame lacks or an unknown fill.
    '''
    import pandas as pd
    if not isinstance(opts['highlight'], dict):
        raise TypeError("highlight must be a dict {{column: {{value: fill}}}}, got {!r}".format(
            opts['highlight']))
    for col, mapping in opts['highlight'].items():
        if not isinstance(mapping, dict):
            raise TypeError("highlight[{!r}] must be a dict {{value: fill}}, got {!r}".format(
                col, mapping))
    freeze = opts['freeze']
    if isinstance(freeze, int) and not isinstance(freeze, bool):
        ncols = df.shape[1] + (df.index.nlevels if opts['index'] else 0)
        if freeze < 0 or freeze > ncols:
            raise ValueError("sheet {!r}: freeze={} is outside 0..{} (the number of columns)".format(
                sheet_name, freeze, ncols))
    for col, dtype in zip(df.columns, df.dtypes):
        if isinstance(dtype, pd.DatetimeTZDtype):
            raise ValueError(
                "sheet {!r}: column {!r} is timezone-aware and Excel cannot store timezones; "
                "use df[{!r}].dt.tz_localize(None) first".format(sheet_name, col, col))
    have = {str(c) for c in df.columns}
    names = list(opts['highlight']) + list(opts['colorscale']) + list(opts['wrap_columns']) \
        + list(opts['group_starts']) + list(opts['id_columns'])
    for col in names:
        if str(col) not in have:
            raise KeyError("sheet {!r}: column {!r} not found in the frame".format(sheet_name, col))
    for mapping in opts['highlight'].values():
        for fill in mapping.values():
            _excel_fill(fill)


def _excel_widths(cols, filters, widths, width_min, width_max):
    '''
    Column widths in sheet order for cols = [(header, values, spec)].
    Sized from the header (plus the filter arrow) and the displayed text of the first
    _EXCEL_WIDTH_SAMPLE values, then clamped to [width_min, width_max].
    '''
    import pandas as pd
    from oryxflow import report
    out = []
    for header, values, spec in cols:
        if isinstance(widths, (int, float)) and not isinstance(widths, bool):
            out.append(widths)
            continue
        if isinstance(widths, dict) and header in widths:
            out.append(widths[header])
            continue
        use_fmt = spec is not None and report._py(spec) is not None
        longest = 0
        for v in values.iloc[:_EXCEL_WIDTH_SAMPLE]:
            if not isinstance(v, str) and pd.api.types.is_scalar(v) and pd.isna(v):
                continue
            longest = max(longest, len(report.fmt(v, spec) if use_fmt else str(v)))
        head = len(header) + (_EXCEL_FILTER_ARROW if filters else 0)
        out.append(max(width_min, min(width_max, max(longest, head) + 2)))
    return out


def _excel_format_sheet(ws, df, row_header, index=False, formats=None, filters=True, freeze=True,
                        widths='auto', width_min=8, width_max=60, header_style=True, title=None,
                        notes=(), highlight={}, colorscale=(), banded=False, wrap_columns=(),
                        group_starts=(), id_columns=()):
    '''
    Style a worksheet written from df (as written, columns already flattened).
    row_header is the 1-based sheet row holding the column headers; a title and notes
    sit above it.
    '''
    from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from oryxflow import report

    cols = _excel_specs(df, index, formats)
    nidx = df.index.nlevels if index else 0
    ncols = len(cols)
    nrows = df.shape[0]
    if ncols == 0:
        return
    first, last_row = row_header + 1, row_header + nrows
    pos = {}
    for j, col in enumerate(df.columns):
        pos.setdefault(str(col), []).append(nidx + 1 + j)

    def positions(names):
        return {p for n in names for p in pos[str(n)]}

    if title or notes:
        if title:
            ws.cell(row=1, column=1, value=title).font = Font(bold=True, size=14)
            if ncols > 1:
                ws.merge_cells(start_row=1, start_column=1, end_row=1,
                               end_column=min(ncols, _EXCEL_TITLE_COLS))
        for i, note in enumerate(notes):
            cell = ws.cell(row=2 + i, column=1, value=note)
            cell.font = Font(italic=True, size=9)
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            if ncols > 1:
                ws.merge_cells(start_row=2 + i, start_column=1, end_row=2 + i,
                               end_column=min(ncols, _EXCEL_NOTE_COLS))

    if header_style:
        for c in range(1, ncols + 1):
            cell = ws.cell(row=row_header, column=c)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical='top', horizontal='left')
            cell.fill = PatternFill('solid', fgColor='D3D9E3')
            cell.border = Border(bottom=Side(style='thin'))

    for c, (_, _, spec) in enumerate(cols, start=1):
        code = report.excel_code(spec) if spec is not None else None
        if code is None:
            continue
        for r in range(first, last_row + 1):
            ws.cell(row=r, column=c).number_format = code

    wrap_pos = positions(wrap_columns)
    id_pos = positions(id_columns)
    group_pos = positions(group_starts)
    for c in range(1, ncols + 1):
        if wrap_pos or id_pos:
            for r in range(first, last_row + 1):
                cell = ws.cell(row=r, column=c)
                if c in id_pos:
                    # Excel flags these with a "number stored as text" warning. openpyxl has an
                    # IgnoredError class but its worksheet writer never serializes it, so the
                    # warning cannot be suppressed from here.
                    cell.number_format = '@'
                if wrap_pos or c in id_pos:
                    cell.alignment = Alignment(
                        wrap_text=True if c in wrap_pos else None,
                        vertical='top' if wrap_pos else None,
                        horizontal='right' if c in id_pos else None)
        if c in group_pos:
            for r in range(row_header, last_row + 1):
                cell = ws.cell(row=r, column=c)
                b = cell.border
                cell.border = Border(left=Side(style='medium'), right=b.right, top=b.top,
                                     bottom=b.bottom)

    if nrows:
        for col, mapping in highlight.items():
            for p in positions([col]):
                rng = '{0}{1}:{0}{2}'.format(get_column_letter(p), first, last_row)
                for value, fill in mapping.items():
                    bg, fg = _excel_fill(fill)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        formula = repr(value)
                    else:
                        formula = '"{}"'.format(str(value).replace('"', '""'))
                    ws.conditional_formatting.add(rng, CellIsRule(
                        operator='equal', formula=[formula],
                        fill=PatternFill(start_color=bg, end_color=bg, fill_type='solid'),
                        font=Font(color=fg) if fg else None))
        for p in sorted(positions(colorscale)):
            ws.conditional_formatting.add(
                '{0}{1}:{0}{2}'.format(get_column_letter(p), first, last_row),
                ColorScaleRule(start_type='min', start_color='F8696B', mid_type='num',
                               mid_value=0, mid_color='FFFFFF', end_type='max',
                               end_color='63BE7B'))
        if banded:
            ws.conditional_formatting.add(
                'A{}:{}{}'.format(first, get_column_letter(ncols), last_row),
                FormulaRule(formula=['MOD(ROW(),2)=0'],
                            fill=PatternFill(start_color='F2F5FA', end_color='F2F5FA',
                                             fill_type='solid')))

    if freeze is True:
        ws.freeze_panes = ws.cell(row=first, column=nidx + 1).coordinate
    elif freeze is not False and freeze is not None:
        ws.freeze_panes = ws.cell(row=first, column=int(freeze) + 1).coordinate

    if filters:
        ws.auto_filter.ref = 'A{}:{}{}'.format(row_header, get_column_letter(ncols), last_row)

    if widths is not None and widths is not False:
        for c, w in enumerate(_excel_widths(cols, filters, widths, width_min, width_max), start=1):
            ws.column_dimensions[get_column_letter(c)].width = w
    for c in wrap_pos:
        ws.column_dimensions[get_column_letter(c)].width = width_max

    for i, note in enumerate(notes):
        span = range(1, min(ncols, _EXCEL_NOTE_COLS) + 1)
        chars = sum(ws.column_dimensions[get_column_letter(c)].width or 8.43 for c in span)
        ws.row_dimensions[2 + i].height = 12 * max(1, -(-len(str(note)) // max(1, int(chars))))


def _excel_sheet_specs(data, options):
    '''
    Normalise data to [(sheet_name, df, opts)]. data is a DataFrame or {name: df_or_spec},
    a spec being {'df': df, **per_sheet_options}. Unknown option names raise TypeError.
    '''
    import pandas as pd
    unknown = set(options) - set(_EXCEL_DEFAULTS)
    if unknown:
        raise TypeError('excel_write() got unexpected options {}'.format(sorted(unknown)))
    if isinstance(data, pd.DataFrame):
        data = {'Sheet1': data}
    out = []
    for name, item in data.items():
        own = {}
        if isinstance(item, dict):
            if 'df' not in item:
                raise TypeError("sheet spec for {!r} needs a 'df' key".format(name))
            own = {k: v for k, v in item.items() if k != 'df'}
            unknown = set(own) - set(_EXCEL_DEFAULTS)
            if unknown:
                raise TypeError('sheet {!r} got unexpected options {}'.format(name, sorted(unknown)))
            item = item['df']
        opts = dict(_EXCEL_DEFAULTS, **options, **own)
        if isinstance(opts['notes'], str):
            opts['notes'] = [opts['notes']]
        out.append((name, item, opts))
    return out


def excel_write(path, data, **options):
    '''
    Write one or more dataframes to a formatted Excel file and return path.

    data: DataFrame (sheet 'Sheet1') or {sheet_name: df_or_spec}; a spec is
        {'df': df, **per_sheet_options}, overriding the options below for that sheet.
    options: formats, index, filters, freeze, widths, width_min, width_max, header_style.
        formats maps column names or globs to a preset name, str.format spec or Excel code;
        formats=False writes no number formats. Formats are display-only: values are unchanged.
    deliverable options: title (str) and notes (list of str) go above the table, so the
        header row moves down (read back with pd.read_excel(skiprows=len(notes) + 3));
        highlight {col: {value: fill}} with fill a name
        (green, yellow, red, grey) or {'bg', 'font'};
        colorscale, wrap_columns, group_starts (lists of columns); banded (bool);
        id_columns are written as text so codes keep leading zeros.
    '''
    import pandas as pd
    sheets = _excel_sheet_specs(data, options)
    _excel_check_sheet_names([n for n, _, _ in sheets])
    prepared = []
    for name, df, opts in sheets:
        df = _excel_flatten_columns(df)
        _excel_specs(df, opts['index'], opts['formats'])  # fail on a bad format before any write
        _excel_check_styling(name, df, opts)
        if opts['id_columns']:
            ids = {str(c) for c in opts['id_columns']}
            df = df.copy()
            for j, col in enumerate(df.columns):
                if str(col) in ids:
                    df[col] = [None if _excel_isna(v) else _excel_id_text(v)
                               for v in df.iloc[:, j]]
        prepared.append((name, df, opts))
    if isinstance(path, str):
        path = pathlib.Path(path)
    if isinstance(path, pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for name, df, opts in prepared:
            row_header = 1 + len(opts['notes']) + 2 if (opts['title'] or opts['notes']) else 1
            df.to_excel(writer, sheet_name=str(name), index=opts['index'],
                        startrow=row_header - 1)
            _excel_format_sheet(writer.sheets[str(name)], df, row_header, **opts)
    return path
