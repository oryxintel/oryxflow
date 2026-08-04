"""Static lint: does a task declare a dependency whose loaded data ``run()`` never reads?

A dead dependency is still HONOURED by the scheduler -- its whole upstream band is computed on
every cold build to produce a frame that is discarded. No dependency-graph query finds this (the
edge is real; only the data is dead), so the only place the truth lives is the body of ``run()``,
in a bound name that has no second occurrence. This module reads that body by AST.

Design contract (see docs/todo/20260803-engine-unused-input-lint.md):
- Three verdicts, never two: ``unused`` / ``clean`` / ``unanalyzed`` -- a shape we cannot parse is
  reported as ``unanalyzed`` with a reason, never silently skipped (a report that looks complete
  but is not converts "we didn't check" into "we checked and it's fine").
- No false positives, at the cost of recall: where usage cannot be proven, ``unanalyzed``.
- OUTER unpack elements are dependencies; INNER elements are that dependency's ``persists``
  outputs. Only a whole-dependency discard (a top-level ``_`` or an unreferenced top-level name)
  is a finding; an inner ``_`` is one ``persist`` key and is normal.
- Tasks with no ``run()`` are omitted (aggregators have no data to discard), not counted as misses.
"""

import ast
import inspect
import os
import textwrap
import warnings as _stdwarnings


SUPPRESS_MARKER = 'oryxflow: input-unused'


class UnusedInputWarning(UserWarning):
    """A task declares a dependency whose loaded data run() never references."""


# Python's default warning filter dedups by call site; build() dedups per family itself, so keep
# the raw filter permissive (mirrors StalenessWarning in codecheck).
_stdwarnings.simplefilter('always', UnusedInputWarning)


class InputFinding:
    """One (task, dependency) pair and what static analysis concluded about it."""
    __slots__ = ('task_family', 'dep_family', 'dep_index', 'binding',
                 'verdict', 'reason', 'source')

    def __init__(self, task_family, dep_family, dep_index, binding, verdict,
                 reason=None, source=None):
        self.task_family = task_family      # the task whose run() we read
        self.dep_family = dep_family        # the declared dependency in question
        self.dep_index = dep_index          # its position in the (single) requires() decorator
        self.binding = binding              # the unpack name, '_', or None
        self.verdict = verdict              # 'unused' | 'clean' | 'unanalyzed'
        self.reason = reason                # set only when verdict == 'unanalyzed'
        self.source = source                # 'tasks.py:1039' -- the DECORATOR line, i.e. to edit

    def message(self):
        loc = (self.source + ': ') if self.source else ''
        return ('{}{} loads {} and never uses it -- remove the dependency '
                'or add "# {}"').format(loc, self.task_family, self.dep_family, SUPPRESS_MARKER)

    def __repr__(self):
        return '<InputFinding {} {}->{} {}>'.format(
            self.verdict, self.task_family, self.dep_family, self.reason or '')


_check_cache = {}   # cls -> list[InputFinding]  (per process; class source is fixed once imported)

_LOAD_ATTRS = ('inputLoad', 'inputLoadConcat')


def _decorator_requires(dec):
    """If ``dec`` is a ``requires(...)`` decorator call, return its declared deps as a list of
    ``(family, key)`` (key is None for a positional dep), else None. ``requires_each`` and
    ``inherits`` return None (fan-out / params-only -- not a positional load contract)."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)
    if name != 'requires':
        return None
    deps = []
    for arg in dec.args:
        if isinstance(arg, ast.Dict):
            for k, v in zip(arg.keys, arg.values):
                key = k.value if isinstance(k, ast.Constant) else None
                deps.append((_ref_name(v), key))
        else:
            deps.append((_ref_name(arg), None))
    for kw in dec.keywords:
        if kw.arg is not None:                 # requires(features0=T0)
            deps.append((_ref_name(kw.value), kw.arg))
    return deps


def _ref_name(node):
    """Family name from a class reference: ``Name`` id or ``Attribute`` attr (tasks.ModelTrain)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_self_load(node, attrs=_LOAD_ATTRS):
    """True if ``node`` is a call ``self.<attr>(...)`` for one of ``attrs``."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'self'
            and node.func.attr in attrs)


def _call_kw(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _loaded_names(fn):
    """Set of names read (ctx=Load) anywhere in the function body."""
    return {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
            and isinstance(n.ctx, ast.Load)}


def _string_keys_used(fn):
    """Every string literal used as a ``task=`` argument or as a subscript on a ``self.input*``
    result -- the keyed forms name their dependency, so 'which dep is never named' is a set
    difference on these."""
    keys = set()
    for node in ast.walk(fn):
        if _is_self_load(node, _LOAD_ATTRS + ('input',)):
            t = _call_kw(node, 'task')
            if isinstance(t, ast.Constant) and isinstance(t.value, str):
                keys.add(t.value)
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            keys.add(node.slice.value)
    return keys


def _uses_self_input(fn):
    """True if run() calls ``self.input(`` (the target accessor) -- positional use through it is
    not tracked, so a positional-unpack dep that looks unused is downgraded to ``unanalyzed``."""
    return any(_is_self_load(n, ('input',)) for n in ast.walk(fn))


def _top_unused(target, loaded):
    """Given one top-level unpack element, is the whole dependency discarded? Returns
    (is_unused, binding)."""
    if isinstance(target, ast.Name):
        return (target.id == '_' or target.id not in loaded), target.id
    if isinstance(target, (ast.Tuple, ast.List)):
        names = [n.id for n in ast.walk(target)
                 if isinstance(n, ast.Name) and n.id != '_']
        # inner '_' alone is one persist key, not a finding; unused only if the WHOLE group is dead
        return (bool(names) and all(nm not in loaded for nm in names)), None
    return False, None


def check_class(cls):
    """Analyze one task class. Returns list[InputFinding], one per declared dependency, or [] for
    classes with no ``requires(...)`` decorator or no ``run()`` (memoized per class)."""
    if cls in _check_cache:
        return _check_cache[cls]
    result = _analyze(cls)
    _check_cache[cls] = result
    return result


def _analyze(cls):
    family = getattr(cls, 'task_family', cls.__name__)
    try:
        raw = inspect.getsource(cls)
        start = inspect.getsourcelines(cls)[1]
        filename = inspect.getsourcefile(cls) or '<unknown>'
    except (OSError, TypeError):
        return []          # notebook / dynamically-built class with no retrievable source
    try:
        tree = ast.parse(textwrap.dedent(raw))
    except SyntaxError:
        return []
    classdef = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if classdef is None:
        return []

    # declared deps: only from a SINGLE positional requires() decorator is the load order
    # unambiguous. Multiple requires / a requires_each fan-out -> don't infer position.
    req_decorators = [(d, _decorator_requires(d)) for d in classdef.decorator_list]
    req_decorators = [(d, deps) for d, deps in req_decorators if deps is not None]
    if len(req_decorators) != 1:
        return []
    dec_node, deps = req_decorators[0]
    if not deps or any(fam is None for fam, _ in deps):
        return []

    run = next((n for n in classdef.body
                if isinstance(n, ast.FunctionDef) and n.name == 'run'), None)
    if run is None:
        return []          # no run() -> aggregator, nothing to discard: OMIT (return [])

    dec_line = start + (dec_node.lineno - 1)
    source = '{}:{}'.format(os.path.basename(filename), dec_line)
    suppressed = SUPPRESS_MARKER in raw

    def finding(i, verdict, binding=None, reason=None):
        if suppressed and verdict == 'unused':
            verdict, reason = 'clean', None
        return InputFinding(family, deps[i][0], i, binding, verdict, reason, source)

    keyed_decl = any(key is not None for _, key in deps)
    loaded = _loaded_names(run)

    # --- positional forms: find `a, b, c = self.inputLoad()` (no task=, no flatten=False) ---
    load_assign = None
    keyed_load = False
    for stmt in ast.walk(run):
        if isinstance(stmt, ast.Assign) and _is_self_load(stmt.value):
            call = stmt.value
            if _call_kw(call, 'task') is not None:
                keyed_load = True          # a dep is selected by name -> keyed set-difference
                continue
            flat = _call_kw(call, 'flatten')
            if isinstance(flat, ast.Constant) and flat.value is False:
                continue
            if load_assign is None:
                load_assign = stmt

    # --- keyed forms: the dependency is named by string literal; plain set difference -------
    # applies when the DECLARATION is keyed (@requires({'k': T})) or the LOAD selects by task=,
    # and there is no positional full-unpack to analyze instead.
    if (keyed_decl or keyed_load) and load_assign is None:
        named = _string_keys_used(run)
        out = []
        for i, (fam, key) in enumerate(deps):
            tag = key if key is not None else fam
            hit = (tag in named) or (fam in named)
            out.append(finding(i, 'clean' if hit else 'unused', binding=key))
        return out

    if load_assign is None:
        # maybe `data = self.inputLoad()` then subscripting, or an unrecognised form
        bare = any(isinstance(s, ast.Assign) and _is_self_load(s.value)
                   for s in ast.walk(run))
        reason = 'bare bind, subscript not tracked' if bare else 'unrecognized load form'
        return [finding(i, 'unanalyzed', reason=reason) for i in range(len(deps))]

    tgt = load_assign.targets[0]
    elements = list(tgt.elts) if isinstance(tgt, (ast.Tuple, ast.List)) else [tgt]
    if len(elements) != len(deps):
        reason = 'unpack arity {} vs {} deps'.format(len(elements), len(deps))
        return [finding(i, 'unanalyzed', reason=reason) for i in range(len(deps))]

    input_guard = _uses_self_input(run)
    out = []
    for i, el in enumerate(elements):
        unused, binding = _top_unused(el, loaded)
        if unused and input_guard:
            out.append(finding(i, 'unanalyzed', binding=binding,
                               reason='self.input() used, positional use not tracked'))
        elif unused:
            out.append(finding(i, 'unused', binding=binding))
        else:
            out.append(finding(i, 'clean', binding=binding))
    return out
