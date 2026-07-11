from oryxflow.core import flatten
import os
import warnings
import pathlib


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
        result += print_tree(child, indent, (index+1) == len(children), clip_params)
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

def concat_iter(items, concat_fn=None, keys=None, ignore_index=True):
    """Stack an iterable of (identifier, params, data) triples into one DataFrame.
    params: dict of raw values -> added as columns by default (groupby keys survive).
    data: a single DataFrame, or a list/dict of DataFrames (multi-persists).
    concat_fn(identifier, params, df)->df: hook called per frame instead of default tagging.
    keys: subset of param names to tag (default all)."""
    import pandas as pd
    frames = []
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
                    df[col] = val
            frames.append(df)
    return pd.concat(frames, ignore_index=ignore_index) if frames else pd.DataFrame()


def requires_grid(task_cls, param, values, **base):
    """Build a requires() dict {value: task_cls(param=value, **base)} for a native
    iterate-and-aggregate task. Sugar for the house-style dict comprehension."""
    return {v: task_cls(**{param: v}, **base) for v in values}


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
