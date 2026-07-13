"""
AST-normalized, symbol-level, transitive code hashes.

``task_hashes(task)`` returns ``{'<relpath>::<symbol>': md5}`` for the task's own class
definition plus the transitive closure of the module-level symbols it actually references
(helpers, constants, project-local base classes), followed across project-local modules --
so editing one task never invalidates unrelated siblings in the same file. Referenced Task
subclasses are excluded (that is dependency *wiring*, tracked via output identity, and
including their bodies would break pin orthogonality); project-local base classes ARE
included via the MRO. Each symbol is hashed after AST normalization (parse -> strip
docstrings and class-body ``code_version`` pin lines -> ``ast.dump``), so comments,
docstrings, formatting edits and adding/removing/bumping a ``code_version`` pin produce no
hash change (the pin is a token, compared as its own record dimension -- never a source
edit). What symbol analysis can't carve up degrades conservatively: module side-effect
statements share one ``<relpath>::<module>`` bucket, unresolvable/star-imported modules and
non-top-level classes fall back to whole-file hashes (``<relpath>::*`` /
``module_hashes``) -- never finer-grained than correct.

Two consumers: with ``settings.code_version_auto`` (the default), ``task_code_hash``
supplies the code-identity token for tasks that declare no explicit ``code_version``,
so a real logic edit reruns them automatically. For tasks WITH an explicit
``code_version``, the hash stays advisory only -- it powers the "code changed but
code_version didn't" warning and never drives a rerun. Blind spots (data files, dynamic
imports, external APIs, modules outside the project root, helpers called *on* another
Task class) mean "inert / no warning", never a false rerun and never a false green claim.
"""

import ast
import os
import sys
import hashlib
import importlib.util
from pathlib import Path

# Overridable project root under which files are considered "project-local".
# None -> auto-detected. Tests (or unusual layouts) point this at a dir explicitly.
PROJECT_ROOT = None

_ROOT_MARKERS = ('.git', 'pyproject.toml', 'setup.py', 'setup.cfg')

# ast.dump output changes across minor Python versions (e.g. 3.12 added type_params),
# so normalized hashes are only comparable within one interpreter version. Records
# stamp this tag; a mismatch means "can't compare", never "changed".
PY_TAG = '{}.{}'.format(sys.version_info.major, sys.version_info.minor)

# Per-file caches are keyed by abspath with the mtime INSIDE the value
# (mtime_ns, payload), so an edited file replaces its entry instead of leaking one
# entry per save in long-lived sessions (notebooks).
# abspath -> (mtime_ns, md5 of the normalized AST)
_hash_cache = {}

# abspath -> (mtime_ns, parsed import tuples)
_imports_cache = {}

# abspath -> (mtime_ns, per-file symbol index (see _symbol_index))
_symindex_cache = {}

# Walk caches hold a multi-file result revalidated against every file's mtime
# (see _walk_cache_get/_walk_cache_put).
# (modname, root) -> (((abspath, mtime_ns), ...), hashes) full-walk result
_module_cache = {}

# (modname, clsname, root) -> (((abspath, mtime_ns), ...), hashes) closure result
_task_hash_cache = {}

# sentinel: distinguishes "name absent from the module namespace" from a None value
_MISSING = object()

# str(start dir) -> resolved project root
_root_cache = {}

# >0 while a build is in flight (core.build brackets processing with freeze/unfreeze):
# module_hashes then mtime-revalidates each module's walk at most ONCE per build and
# trusts it for the rest -- code must not change mid-build, and the per-complete() stat
# storm dominates small-DAG runtimes otherwise. Calls outside a build always revalidate.
_freeze_depth = 0
_freeze_gen = 0
_freeze_validated = {}   # (modname, root) -> generation it was last (re)validated in


def freeze():
    global _freeze_depth, _freeze_gen
    if _freeze_depth == 0:
        _freeze_gen += 1
    _freeze_depth += 1


def unfreeze():
    global _freeze_depth
    _freeze_depth = max(0, _freeze_depth - 1)


def _walk_cache_get(cache, key):
    """Cached multi-file walk result if still fresh: inside a build (freeze) an entry
    already validated this generation is trusted outright, otherwise every recorded
    file mtime must match. None -> recompute."""
    cached = cache.get(key)
    if cached is None:
        return None
    files_key, value = cached
    if _freeze_depth and _freeze_validated.get(key) == _freeze_gen:
        return dict(value)
    try:
        if all(os.stat(p).st_mtime_ns == m for p, m in files_key):
            if _freeze_depth:
                _freeze_validated[key] = _freeze_gen
            return dict(value)
    except OSError:
        pass
    return None


def _walk_cache_put(cache, key, paths, value):
    """Store a walk result keyed to the current mtimes of the files it covered
    (a changed file also changes its own mtime when it gains/loses references,
    so the file set stays honest)."""
    mtimes = []
    for p in paths:
        try:
            mtimes.append((p, os.stat(p).st_mtime_ns))
        except OSError:
            pass
    cache[key] = (tuple(mtimes), dict(value))
    if _freeze_depth:
        _freeze_validated[key] = _freeze_gen


def _project_root(start=None):
    """Project root: walk up from ``start`` (a file/dir; default cwd) to the nearest
    directory holding a repo/project marker. Anchoring on a marker rather than cwd keeps
    the hash unit and relpath keys stable across subdir invocations, test runners and
    notebooks whose cwd differs."""
    if PROJECT_ROOT is not None:
        return Path(PROJECT_ROOT)
    p = Path(start) if start is not None else Path.cwd()
    try:
        p = p.resolve()
        if not p.is_dir():
            p = p.parent
    except OSError:
        return Path.cwd()
    key = str(p)
    if key in _root_cache:
        return _root_cache[key]
    root = next((anc for anc in [p, *p.parents]
                 if any((anc / m).exists() for m in _ROOT_MARKERS)), Path.cwd())
    _root_cache[key] = root
    return root


def _is_code_version_stmt(stmt):
    # a class-body `code_version = ...` pin (plain or annotated assignment)
    if isinstance(stmt, ast.Assign):
        return (len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == 'code_version')
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == 'code_version'
    return False


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
        if isinstance(node, ast.ClassDef):
            # the pin line itself must be invisible to the hash: adding, removing or
            # bumping `code_version` is a mode/token change (handled by the mode-aware
            # record comparison), never a source change -- otherwise opting in could
            # never be free (the opt-in edit would always move the hash it is
            # compared against)
            node.body = [s for s in node.body if not _is_code_version_stmt(s)]
    return tree


def file_hash(path):
    """Normalized hash of one source file; raw-bytes fallback on SyntaxError.

    Returns None if the file can't be read.
    """
    path = Path(path)
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    cached = _hash_cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        src = path.read_bytes()
    except OSError:
        return None
    try:
        tree = _strip_docstrings(ast.parse(src))
        digest = hashlib.md5(ast.dump(tree).encode('utf-8')).hexdigest()
    except SyntaxError:
        digest = hashlib.md5(src).hexdigest()
    except Exception:
        return None
    _hash_cache[str(path)] = (mtime, digest)
    return digest


def _module_file(modname, package=None, level=0):
    """Best-effort resolve a module name to its source file, executing nothing new."""
    try:
        if level:
            # resolve relative import against the importing module's package
            if not package:
                return None
            parts = package.split('.')
            if level > len(parts):
                return None
            base = '.'.join(parts[:len(parts) - level + 1])
            modname = '{}.{}'.format(base, modname) if modname else base
        mod = sys.modules.get(modname)
        f = getattr(mod, '__file__', None) if mod is not None else None
        if f is None:
            spec = importlib.util.find_spec(modname)
            f = getattr(spec, 'origin', None) if spec is not None else None
        if f and f.endswith('.py'):
            return Path(f)
    except Exception:
        pass
    return None


def _is_local(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def _imports_of(path):
    """(modname, level, from_names) tuples for every import statement in the file."""
    try:
        mtime = os.stat(path).st_mtime_ns
        cached = _imports_cache.get(str(path))
        if cached is not None and cached[0] == mtime:
            return cached[1]
    except OSError:
        mtime = None
    out = []
    try:
        tree = ast.parse(Path(path).read_bytes())
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, 0, ()))
        elif isinstance(node, ast.ImportFrom):
            names = tuple(a.name for a in node.names)
            out.append((node.module or '', node.level, names))
    if mtime is not None:
        _imports_cache[str(path)] = (mtime, out)
    return out


def _package_of(modname, path):
    # package context for relative imports: the module itself if a package, else its parent
    if Path(path).name == '__init__.py':
        return modname
    return modname.rpartition('.')[0]


def _class_source(cls):
    """``(abspath, root, modname)`` for a class's defining module; ``abspath`` is None
    when the module has no project-local ``.py`` source (feature inert for it)."""
    modname = cls.__module__
    mod = sys.modules.get(modname)
    start = getattr(mod, '__file__', None) if mod is not None else None
    if start is None:
        start = _module_file(modname)
    root = _project_root(start)
    if start is None or not _is_local(start, root):
        return None, root, modname
    return str(Path(start).resolve()), root, modname


def root_for(task_or_cls):
    """Project root the task's stored hash keys are relative to -- derived from the
    task's module file, NOT cwd, so consumers resolving those keys (e.g. the mtime
    guard) agree with ``task_hashes``/``module_hashes`` even when cwd differs."""
    cls = task_or_cls if isinstance(task_or_cls, type) else type(task_or_cls)
    return _class_source(cls)[1]


def module_hashes(task_or_cls):
    """``{relpath: md5}`` over the task module and its transitively imported local files."""
    cls = task_or_cls if isinstance(task_or_cls, type) else type(task_or_cls)
    start, root, modname = _class_source(cls)
    if start is None:
        return {}

    cache_key = (modname, str(root))
    cached = _walk_cache_get(_module_cache, cache_key)
    if cached is not None:
        return cached

    hashes = {}
    seen = set()
    queue = [(start, modname)]
    while queue:
        path, name = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        digest = file_hash(path)
        if digest is None:
            continue
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        hashes[rel] = digest
        pkg = _package_of(name, path)
        for target, level, from_names in _imports_of(path):
            candidates = [(target, level)]
            # `from mod import name` where name is itself a submodule
            for n in from_names:
                candidates.append(('{}.{}'.format(target, n) if target else n, level))
            for cand, lvl in candidates:
                f = _module_file(cand, package=pkg, level=lvl)
                if f is not None and _is_local(f, root):
                    fr = str(Path(f).resolve())
                    if fr not in seen:
                        # reconstruct the resolved module name for package context
                        queue.append((fr, cand if not lvl else '{}.{}'.format(pkg, cand)))
    _walk_cache_put(_module_cache, cache_key, seen, hashes)
    return hashes


def task_code_hash(task_or_cls):
    """Single md5 over the task_hashes set; None when nothing is hashable
    (module not project-local) so auto degrades to inert, never false-green."""
    hashes = task_hashes(task_or_cls)
    if not hashes:
        return None
    blob = '|'.join('{}={}'.format(k, hashes[k]) for k in sorted(hashes))
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# symbol-level hashing: per-file symbol index + per-task reference closure
# ---------------------------------------------------------------------------

def _is_main_guard(stmt):
    # `if __name__ == '__main__':` never runs on import -- editing it is inert
    return (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare)
            and isinstance(stmt.test.left, ast.Name)
            and stmt.test.left.id == '__name__')


def _assign_names(stmt):
    # plain Name targets of an Assign/AnnAssign (tuple targets unpacked)
    names = []
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    for t in targets:
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            names.extend(e.id for e in t.elts if isinstance(e, ast.Name))
    return names


def _bound_names(stmt):
    # names bound by defs/classes/Name-assigns anywhere inside a compound statement:
    # `if X: def f(): ...` maps f to the whole `if` block's digest
    names = []
    for node in ast.walk(stmt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names.extend(_assign_names(node))
    return names


def _collect_refs(node):
    """``{(rootname, attr_chain), ...}`` referenced in ``node``: attribute chains rooted
    at a Name (``mod.sub.attr`` -> ``('mod', ('sub', 'attr'))``) and bare Name loads as
    ``(name, ())``. Over-approximation is fine (locals shadowing module names add edges,
    never drop them); an attribute root is not double-counted as a bare load.
    Iterative (explicit stack): pathological expression nesting must not hit the
    recursion limit."""
    refs = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Attribute):
            chain = []
            cur = n
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                refs.add((cur.id, tuple(reversed(chain))))
            else:
                stack.append(cur)
            continue
        if isinstance(n, ast.Name):
            if isinstance(n.ctx, ast.Load):
                refs.add((n.id, ()))
            continue
        stack.extend(ast.iter_child_nodes(n))
    return refs


def _symbol_index(path):
    """Per-file symbol table over the NORMALIZED top-level statements, or None when the
    file can't be parsed: ``symbols`` name->digest, ``refs`` name->frozenset of
    ``_collect_refs`` tuples, ``imports`` local-name->(module, orig, level) from-import
    bindings, ``stars`` star-import (module, level) tuples, ``sideeffect`` one digest over
    the import-time side-effect statements (calls, compound blocks, attribute assigns)."""
    path = Path(path)
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    cached = _symindex_cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        tree = _strip_docstrings(ast.parse(path.read_bytes()))
    except Exception:
        _symindex_cache[str(path)] = (mtime, None)
        return None
    symbols, refs, imports, stars, side = {}, {}, {}, [], []

    def _bind_imports(node):
        # import statements anywhere in `node` (top level OR function-local lazy
        # imports) become resolution bindings; top-level bindings win (setdefault)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for a in sub.names:
                    imports.setdefault(a.asname or a.name.split('.')[0],
                                       (a.name, None, 0))
            elif isinstance(sub, ast.ImportFrom):
                for a in sub.names:
                    if a.name == '*':
                        stars.append((sub.module or '', sub.level))
                    else:
                        imports.setdefault(a.asname or a.name,
                                           (sub.module or '', a.name, sub.level))

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [stmt.name]
            _bind_imports(stmt)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            names = _assign_names(stmt)
            if not names:
                side.append(stmt)     # `obj.attr = ...` at module level: side effect
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            _bind_imports(stmt)
            continue
        else:
            if _is_main_guard(stmt):
                continue
            side.append(stmt)
            names = _bound_names(stmt)
            _bind_imports(stmt)
        digest = hashlib.md5(ast.dump(stmt).encode('utf-8')).hexdigest()
        stmt_refs = frozenset(_collect_refs(stmt))
        for n in names:
            # a name bound by SEVERAL statements (`def f` then `f = cache(f)`,
            # `X = 1` then `X = 2`, try/except fallback assigns) folds every
            # binding statement into its digest, in statement order -- editing
            # any of them must move the hash, mirroring the refs union below
            prev = symbols.get(n)
            symbols[n] = digest if prev is None else hashlib.md5(
                (prev + digest).encode('utf-8')).hexdigest()
            refs[n] = refs.get(n, frozenset()) | stmt_refs
    side_digest = None
    if side:
        blob = '|'.join(ast.dump(s) for s in side)
        side_digest = hashlib.md5(blob.encode('utf-8')).hexdigest()
    index = {'symbols': symbols, 'refs': refs, 'imports': imports,
             'stars': tuple(stars), 'sideeffect': side_digest}
    _symindex_cache[str(path)] = (mtime, index)
    return index


def _local_module_file(mod, root):
    f = getattr(mod, '__file__', None)
    if f and f.endswith('.py') and _is_local(f, root):
        return str(Path(f).resolve())
    return None


def _is_task_cls(obj):
    try:
        from oryxflow.core import Task
        return isinstance(obj, type) and issubclass(obj, Task)
    except Exception:
        return False


def task_hashes(task_or_cls):
    """``{'<relpath>::<symbol>': md5}`` for the task's class def + the transitive closure
    of module-level symbols it references, per-symbol so sibling tasks in the same file
    don't share fate. ``::<module>`` keys carry a module's import-time side effects,
    ``::*`` a whole-file fallback. Falls back to file-level ``module_hashes`` when the
    class isn't a top-level ClassDef in its module (dynamic/nested classes)."""
    import inspect as _inspect
    cls = task_or_cls if isinstance(task_or_cls, type) else type(task_or_cls)
    start, root, modname = _class_source(cls)
    if start is None:
        return {}

    root_idx = _symbol_index(start)
    if root_idx is None or cls.__name__ not in root_idx['symbols']:
        return module_hashes(cls)      # dynamic/nested class: file-level fallback

    cache_key = (modname, cls.__name__, str(root))
    cached = _walk_cache_get(_task_hash_cache, cache_key)
    if cached is not None:
        return cached

    hashes = {}
    files_seen = {}                    # abspath -> modname (for star-import context)
    seen = set()
    queue = [(start, cls.__name__, modname)]

    def _rel(path):
        return os.path.relpath(path, root).replace(os.sep, '/')

    def _enqueue_bases(c):
        # project-local base classes are a code dependency (unlike referenced Tasks,
        # which are wiring) -- follow the MRO, aliasing-proof
        for base in c.__mro__[1:]:
            bf = _local_module_file(sys.modules.get(base.__module__), root)
            if bf is not None:
                queue.append((bf, base.__name__, base.__module__))

    def _add_file(path):
        h = file_hash(path)
        if h is not None:
            files_seen.setdefault(str(Path(path).resolve()), None)
            hashes[_rel(path) + '::*'] = h

    def _resolve_obj(obj, chain):
        # route one runtime object: skip Tasks (wiring), enqueue functions/classes by
        # defining file, walk attribute chains through modules, whole-file for bare
        # local-module references. Returns True when handled.
        if _is_task_cls(obj):
            return True
        if _inspect.ismodule(obj):
            f = _local_module_file(obj, root)
            if f is None:
                return True
            if not chain:
                _add_file(f)           # module object used bare: can't narrow
                return True
            attr = getattr(obj, chain[0], None)
            if attr is None or not _resolve_obj(attr, chain[1:]):
                # plain data value (or missing at runtime): hash the defining statement
                queue.append((f, chain[0], obj.__name__))
            return True
        if isinstance(obj, type) or _inspect.isfunction(obj):
            m = sys.modules.get(getattr(obj, '__module__', None) or '')
            f = _local_module_file(m, root)
            if f is not None:
                queue.append((f, obj.__name__, obj.__module__))
                if isinstance(obj, type):
                    _enqueue_bases(obj)
            return True
        return False                   # plain data: caller falls back to AST lookup

    _enqueue_bases(cls)
    while queue:
        path, sym, mname = queue.pop()
        if (path, sym) in seen:
            continue
        seen.add((path, sym))
        idx = _symbol_index(path)
        if idx is None:
            _add_file(path)
            continue
        files_seen[path] = mname
        digest = idx['symbols'].get(sym)
        if digest is None:
            # not defined here -- follow a re-export (`from .impl import helper` in an
            # __init__.py) before giving up to the whole-file fallback
            imp = idx['imports'].get(sym)
            if imp is not None:
                tmod, orig, level = imp
                f = _module_file(tmod, package=_package_of(mname, path), level=level)
                if f is not None and _is_local(f, root):
                    queue.append((str(Path(f).resolve()), orig or sym, tmod))
                    continue
            _add_file(path)            # can't locate the symbol: whole file
            continue
        hashes['{}::{}'.format(_rel(path), sym)] = digest
        ns = vars(sys.modules[mname]) if mname in sys.modules else {}
        for name, chain in idx['refs'].get(sym, ()):
            obj = ns.get(name, _MISSING)
            if obj is not _MISSING and _resolve_obj(obj, chain):
                continue
            # AST fallback: own top-level symbol, else a from-import of a data value
            if name in idx['symbols']:
                queue.append((path, name, mname))
                continue
            imp = idx['imports'].get(name)
            if imp is not None:
                tmod, orig, level = imp
                f = _module_file(tmod, package=_package_of(mname, path), level=level)
                if f is not None and _is_local(f, root):
                    fr = str(Path(f).resolve())
                    if orig is None:
                        # plain `import mod` binding (e.g. a lazy function-local
                        # import): the ref names the module -- narrow to the
                        # attribute used, whole file when used bare
                        if chain:
                            queue.append((fr, chain[0], tmod))
                        else:
                            _add_file(fr)
                    else:
                        queue.append((fr, orig, tmod))

    for path, mname in files_seen.items():
        idx = _symbol_index(path)
        if idx is None:
            continue
        if idx['sideeffect'] is not None:
            hashes[_rel(path) + '::<module>'] = idx['sideeffect']
        for tmod, level in idx['stars']:
            f = _module_file(tmod, package=_package_of(mname or '', path), level=level)
            if f is not None and _is_local(f, root):
                _add_file(f)

    _walk_cache_put(_task_hash_cache, cache_key, files_seen, hashes)
    return hashes


def current_hash_for_key(root, key):
    """Recompute the current digest for one stored ``source_hashes`` key, or None when
    it can't be computed (file unreadable, symbol vanished). Understands ``rel::sym``,
    ``rel::<module>``, ``rel::*`` and legacy bare-``rel`` (file-level v2 records)."""
    rel, sep, sym = key.partition('::')
    path = Path(root) / rel
    if not sep or sym == '*':
        return file_hash(path)
    idx = _symbol_index(path)
    if idx is None:
        return None
    if sym == '<module>':
        return idx['sideeffect']
    return idx['symbols'].get(sym)
