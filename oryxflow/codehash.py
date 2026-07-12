"""
AST-normalized, file-level, transitive code hashes (advisory only).

``module_hashes(task)`` returns ``{relpath: md5}`` for every project-local source file
reachable from the task's defining module via ``import`` statements. Each file is hashed
after AST normalization (parse -> strip docstrings -> ``ast.dump``), so comments,
docstrings and formatting edits produce no hash change. Hashing never drives reruns --
it only powers the "code changed but code_version didn't" warning. Blind spots (data
files, dynamic imports, external APIs) mean "no warning", never a false rerun.
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

# (abspath, mtime) -> md5 of the normalized AST
_hash_cache = {}

# (abspath, mtime) -> parsed import tuples
_imports_cache = {}

# modname -> (((abspath, mtime_ns), ...), hashes) full-walk result, revalidated by mtime
_module_cache = {}

# str(start dir) -> resolved project root
_root_cache = {}


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


def _strip_docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, 'body', None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
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
    key = (str(path), mtime)
    if key in _hash_cache:
        return _hash_cache[key]
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
    _hash_cache[key] = digest
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
        key = (str(path), os.stat(path).st_mtime_ns)
        if key in _imports_cache:
            return _imports_cache[key]
    except OSError:
        key = None
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
    if key is not None:
        _imports_cache[key] = out
    return out


def _package_of(modname, path):
    # package context for relative imports: the module itself if a package, else its parent
    if Path(path).name == '__init__.py':
        return modname
    return modname.rpartition('.')[0]


def module_hashes(task_or_cls):
    """``{relpath: md5}`` over the task module and its transitively imported local files."""
    cls = task_or_cls if isinstance(task_or_cls, type) else type(task_or_cls)
    modname = cls.__module__
    mod = sys.modules.get(modname)
    start = getattr(mod, '__file__', None) if mod is not None else None
    if start is None:
        start = _module_file(modname)
    root = _project_root(start)
    if start is None or not _is_local(start, root):
        return {}

    # full-walk result cache, revalidated cheaply by file mtimes (a changed file also
    # changes its own mtime when it gains/loses imports, so the file set stays honest)
    cached = _module_cache.get((modname, str(root)))
    if cached is not None:
        files_key, cached_hashes = cached
        try:
            if all(os.stat(p).st_mtime_ns == m for p, m in files_key):
                return dict(cached_hashes)
        except OSError:
            pass

    hashes = {}
    file_mtimes = []
    seen = set()
    queue = [(str(Path(start).resolve()), modname)]
    while queue:
        path, name = queue.pop()
        if path in seen:
            continue
        seen.add(path)
        digest = file_hash(path)
        if digest is None:
            continue
        try:
            file_mtimes.append((path, os.stat(path).st_mtime_ns))
        except OSError:
            pass
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
    _module_cache[(modname, str(root))] = (tuple(file_mtimes), dict(hashes))
    return hashes
