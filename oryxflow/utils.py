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
