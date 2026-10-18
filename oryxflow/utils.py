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

    h = hashlib.md5()
    for key, f in sorted(keyed, key=lambda kv: kv[0]):
        h.update(key.encode('utf-8'))  # the name is part of the digest: a rename is a change
        h.update(b'\0')
        h.update(f.read_bytes())
        h.update(b'\0')
    return h.hexdigest()


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
                       width_min=8, width_max=60, header_style=True)
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
                        widths='auto', width_min=8, width_max=60, header_style=True, **styling):
    '''
    Style a worksheet written from df (as written, columns already flattened).
    row_header is the 1-based sheet row holding the column headers.
    '''
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

    if freeze is True:
        ws.freeze_panes = ws.cell(row=first, column=nidx + 1).coordinate
    elif freeze is not False and freeze is not None:
        ws.freeze_panes = ws.cell(row=first, column=int(freeze) + 1).coordinate

    if filters:
        ws.auto_filter.ref = 'A{}:{}{}'.format(row_header, get_column_letter(ncols), last_row)

    if widths is not None and widths is not False:
        for c, w in enumerate(_excel_widths(cols, filters, widths, width_min, width_max), start=1):
            ws.column_dimensions[get_column_letter(c)].width = w


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
        out.append((name, item, dict(_EXCEL_DEFAULTS, **options, **own)))
    return out


def excel_write(path, data, **options):
    '''
    Write one or more dataframes to a formatted Excel file and return path.

    data: DataFrame (sheet 'Sheet1') or {sheet_name: df_or_spec}; a spec is
        {'df': df, **per_sheet_options}, overriding the options below for that sheet.
    options: formats, index, filters, freeze, widths, width_min, width_max, header_style.
        formats maps column names or globs to a preset name, str.format spec or Excel code;
        formats=False writes no number formats. Formats are display-only: values are unchanged.
    '''
    import pandas as pd
    sheets = _excel_sheet_specs(data, options)
    _excel_check_sheet_names([n for n, _, _ in sheets])
    prepared = []
    for name, df, opts in sheets:
        df = _excel_flatten_columns(df)
        _excel_specs(df, opts['index'], opts['formats'])  # fail on a bad format before any write
        prepared.append((name, df, opts))
    if isinstance(path, str):
        path = pathlib.Path(path)
    if isinstance(path, pathlib.Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for name, df, opts in prepared:
            df.to_excel(writer, sheet_name=str(name), index=opts['index'])
            _excel_format_sheet(writer.sheets[str(name)], df, 1, **opts)
    return path
