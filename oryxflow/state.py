"""
Per-data-dir record store for code-aware invalidation.

One JSON file per data directory (``<dirpath>/.oryxflow-code-status.json``) holding the latest record
per ``task_id``: code fingerprint, code_version, per-file normalized source hashes, ts.
The file describes those exact artifacts, so it travels with the data dir (move/restore
the dir whole). Writes are atomic (tmp + ``os.replace``) on the local filesystem and
routed through fsspec/upath when cloud storage is enabled. This is the only mutable
store in the design; correctness depends only on it, never on the event log.

Top-level imports are stdlib only; ``settings`` is lazy-imported (import-cycle-safe).
"""

import os
import json
import tempfile
from pathlib import Path, PurePosixPath

# record schema version, stamped as 'v' in every record. Bump when the fingerprint
# formula changes: _code_ok treats a record with a different/missing v as unverifiable
# (complete) and build()'s advisory sweep silently re-stamps it -- a one-time
# re-baseline at the trust level of grandfathering, never a mass rerun.
# v2: code_version_auto (fingerprint may fold the AST auto token).
RECORD_V = 2

# str(store path) -> dict of records, invalidated on write
_cache = {}


def _cloud_enabled():
    from oryxflow import settings
    return settings.cloud_fs_enabled


def _store_path(dirpath):
    from oryxflow import settings
    name = getattr(settings, 'state_filename', '.oryxflow-code-status.json')
    if settings.cloud_fs_enabled:
        import upath
        return upath.UPath('{}/{}'.format(settings.cloud_fs_prefix,
                                          PurePosixPath(Path(dirpath) / name)))
    return Path(dirpath) / name


def _load(dirpath):
    path = _store_path(dirpath)
    key = str(path)
    if key in _cache:
        return _cache[key]
    records = {}
    try:
        if path.exists():
            with path.open('r') as fh:
                records = json.load(fh)
    except Exception:
        records = {}
    _cache[key] = records
    return records


def _write(dirpath, records):
    path = _store_path(dirpath)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    payload = json.dumps(records, indent=1, sort_keys=True)
    if _cloud_enabled():
        with path.open('w') as fh:
            fh.write(payload)
    else:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as fh:
                fh.write(payload)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _cache[str(path)] = records


def get_record(dirpath, task_id):
    """Latest record for ``task_id`` in the store at ``dirpath``, or None."""
    return _load(dirpath).get(task_id)


def put_record(dirpath, task_id, record):
    """Insert/replace the record for ``task_id`` (read-modify-write, atomic)."""
    records = dict(_load(dirpath))
    records[task_id] = record
    _write(dirpath, records)


def all_records(dirpath):
    """``{task_id: record}`` for every record in the store at ``dirpath``."""
    return dict(_load(dirpath))


def clear_cache():
    _cache.clear()
