"""
Append-only event stream: plain JSONL, queried by scanning.

The active log is always ``.oryxflow/events.jsonl`` (a stable head). Before appending,
the writer checks whether the head's first event belongs to an earlier month; if so the
head is renamed to ``events-YYYYMM.jsonl`` (rename only -- offloaded files are immutable
forever) and a fresh head starts. All history = glob ``events*.jsonl``. The stream is
plain text: ``tail -30 .oryxflow/events.jsonl``, ``grep``, ``jq`` all work, and the
query surface (``runs()``/``warnings()``/``status()``) is a direct scan of the same
files -- no derived index, no migrations, nothing to rebuild. At realistic volumes
(months of runs) a scan is milliseconds; if that ever changes, an index can be layered
on without touching the format.

Every event carries a sync-ready envelope: unique ``id`` (uuid4 hex), UTC ``ts``,
``type``, ``v``, plus attribution fields ``project_id``/``machine``/``user`` (nullable,
unused locally). One writer seam (``append``) that a future sync client tees. Events are
never mutated; corrections (``code_accepted``, tags) are new events and queries derive
current state.

Writes are asynchronous (one daemon writer thread, ordering preserved) so event I/O
never slows the build's hot path; ``flush()`` drains the queue and the query surface
flushes before answering. An event write must never fail a run: everything is wrapped
and failures are logged at DEBUG. Top-level imports are stdlib only; ``settings`` is
lazy-imported.
"""

import os
import json
import uuid
import queue
import atexit
import platform
import threading
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from oryxflow.log import logger

V = 1          # event payload version, per-type

HEAD_NAME = 'events.jsonl'
CONFIG_NAME = 'config.json'

_identity = None            # cached (project_id, machine, user)
_head_month = {}            # str(head path) -> 'YYYY-MM' of its first event


def _settings():
    from oryxflow import settings
    return settings


def _dir():
    return Path(_settings().eventspath)


def _enabled():
    return getattr(_settings(), 'events', True)


def _utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------------------------
# identity
# ------------------------------------------------------------------------------------

def _git_user_email():
    try:
        out = subprocess.run(['git', 'config', 'user.email'], capture_output=True,
                             text=True, timeout=5)
        email = out.stdout.strip()
        return email or None
    except Exception:
        return None


def _get_identity():
    global _identity
    if _identity is not None:
        return _identity
    project_id = None
    try:
        cfg_path = _dir() / CONFIG_NAME
        cfg = {}
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
        project_id = cfg.get('project_id')
        if project_id is None:
            project_id = uuid.uuid4().hex
            cfg['project_id'] = project_id
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, indent=1))
    except Exception:
        pass
    try:
        machine = platform.node() or None
    except Exception:
        machine = None
    _identity = (project_id, machine, _git_user_email())
    return _identity


# ------------------------------------------------------------------------------------
# writer
# ------------------------------------------------------------------------------------

# Async writer: appends are enqueued and drained by one daemon thread, so file I/O never
# slows the build's hot path. Ordering is preserved (single queue, single writer).
# flush() blocks until the queue is drained; build() flushes at run end and the query
# surface flushes before answering, so results are always current.
_queue = None
_writer = None
_writer_lock = threading.Lock()


def _writer_loop():
    while True:
        item = _queue.get()
        try:
            _write_event(item)
        except Exception as e:
            try:
                logger.debug("event write failed: {}", e)
            except Exception:
                pass
        finally:
            _queue.task_done()


def _ensure_writer():
    global _queue, _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is not None and _writer.is_alive():
            return
        if _queue is None:
            _queue = queue.Queue()
        _writer = threading.Thread(target=_writer_loop, name='oryxflow-events',
                                   daemon=True)
        _writer.start()


def flush(timeout=10):
    """Block until all enqueued events are written (bounded by ``timeout`` seconds)."""
    if _queue is None:
        return
    import time as _time
    deadline = _time.monotonic() + timeout
    while _queue.unfinished_tasks and _time.monotonic() < deadline:
        _time.sleep(0.001)


atexit.register(flush)


def _offload_if_stale(head, now_month):
    key = str(head)
    month = _head_month.get(key)
    if month is None:
        if not head.exists():
            _head_month[key] = now_month
            return
        try:
            with head.open('r', encoding='utf-8') as fh:
                first = fh.readline()
            month = json.loads(first)['ts'][:7]
        except Exception:
            month = now_month
        _head_month[key] = month
    if month < now_month:
        target = head.parent / 'events-{}.jsonl'.format(month.replace('-', ''))
        if target.exists():
            # extremely rare (clock skew / manual seeding): fold head into the month file
            with target.open('a', encoding='utf-8') as out, head.open('r', encoding='utf-8') as src:
                out.write(src.read())
            head.unlink()
        else:
            os.replace(str(head), str(target))
        _head_month[key] = now_month


def _write_event(event):
    # runs on the writer thread
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    head = d / HEAD_NAME
    _offload_if_stale(head, event['ts'][:7])
    line = json.dumps(event, default=str)
    with head.open('a', encoding='utf-8') as fh:   # open-append-close per event
        fh.write(line + '\n')


def append(type, payload=None, run_id=None, flow=None):
    """Append one event to the stream.

    Never raises; a failed event write must never fail a run. Complete no-op when
    ``settings.events`` is False (no dir created, no thread started). The write itself
    happens on a background thread (see ``flush``).
    """
    try:
        if not _enabled():
            return None
        now = _utcnow()
        project_id, machine, user = _get_identity()
        event = {'id': uuid.uuid4().hex, 'ts': now.isoformat(), 'type': type, 'v': V,
                 'project_id': project_id, 'machine': machine, 'user': user,
                 'run_id': run_id, 'flow': str(flow) if flow is not None else None}
        if payload:
            for k, v in payload.items():
                if k not in event:
                    event[k] = v
        _ensure_writer()
        _queue.put(event)
        return event
    except Exception as e:
        try:
            logger.debug("event write failed: {}", e)
        except Exception:
            pass
        return None


# ------------------------------------------------------------------------------------
# query surface -- direct scans of the JSONL stream
# ------------------------------------------------------------------------------------

def _stream_files():
    d = _dir()
    files = sorted(d.glob('events-*.jsonl'))
    head = d / HEAD_NAME
    if head.exists():
        files.append(head)
    return files


def iter_events(types=None):
    """Yield every event (oldest first), tolerating bad lines and pruned months."""
    for path in _stream_files():
        try:
            with path.open('r', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if types is None or event.get('type') in types:
                        yield event
        except OSError:
            continue


def runs(task_family=None, last=20, flow=None):
    """Recent task executions (``task_ran``/``task_failed`` events), newest first."""
    flush()
    rows = [e for e in iter_events(types=('task_ran', 'task_failed'))
            if (task_family is None or e.get('family') == task_family)
            and (flow is None or e.get('flow') == str(flow))]
    rows.sort(key=lambda e: e.get('ts', ''), reverse=True)
    return rows[:last] if last is not None else rows


def warnings():
    """Pending code warnings: latest ``code_warning`` per task not since accepted or rerun."""
    flush()
    warned = {}
    clears = []
    for e in iter_events(types=('code_warning', 'code_accepted', 'task_ran')):
        if e['type'] == 'code_warning':
            warned[e.get('task_id')] = e       # oldest -> newest, keeps newest per task
        else:
            clears.append(e)
    pending = []
    for w in warned.values():
        cleared = any(
            c.get('ts', '') > w.get('ts', '') and (
                (c.get('task_id') and c.get('task_id') == w.get('task_id'))
                or (c.get('type') == 'code_accepted'
                    and c.get('family') and c.get('family') == w.get('family')))
            for c in clears)
        if not cleared:
            pending.append(w)
    return pending


def status():
    """One session-start call: pending warnings, last run per family, recent failures.

    Returns data (a dict) and prints nothing -- for programmatic filtering. When you
    just want the summary on stdout (a script, ``python -c``, a REPL), call
    :func:`print_status` instead."""
    flush()
    last_runs = {}
    failures = []
    for e in iter_events(types=('task_ran', 'task_failed')):
        fam = e.get('family')
        if fam:
            last_runs[fam] = e                 # oldest -> newest, keeps newest per family
        if e['type'] == 'task_failed':
            failures.append(e)
    return {'pending_warnings': warnings(), 'last_runs': last_runs,
            'recent_failures': failures[-10:][::-1]}


def print_status():
    """Print the :func:`status` summary to stdout: pending code warnings (with changed
    files and exits), last run per family (outcome, reason, duration), recent failures.

    The default session-start orientation call for scripts, ``python -c`` one-liners
    and REPLs alike -- ``status()`` returns the same facts as a dict but prints
    nothing, so a bare ``status()`` in a script shows nothing. Returns ``None``."""
    s = status()
    warns, last_runs, failures = s['pending_warnings'], s['last_runs'], s['recent_failures']

    def _ts(e):
        return (e.get('ts') or '')[:16].replace('T', ' ')

    if not warns and not last_runs and not failures:
        print('oryxflow: no events recorded yet ({})'.format(_dir() / HEAD_NAME))
        return
    print('pending code warnings: {}'.format(len(warns)))
    for w in warns:
        files = ', '.join(w.get('changed_files') or []) or '?'
        print('  {} (code_version={}): changed {} -- bump code_version, or '
              'accept_code()/reset()'.format(w.get('family'), w.get('code_version'), files))
    if last_runs:
        print('last run per family:')
        for fam, e in sorted(last_runs.items()):
            outcome = 'FAILED' if e['type'] == 'task_failed' else (e.get('reason') or 'ran')
            dur = e.get('duration_s')
            dur = '  {:.2f}s'.format(dur) if isinstance(dur, (int, float)) else ''
            print('  {:<28} {}  {}{}'.format(fam, _ts(e), outcome, dur))
    if failures:
        print('recent failures: {}'.format(len(failures)))
        for e in failures:
            err = (e.get('error') or '').splitlines()
            print('  {}  {}  {}'.format(e.get('family'), _ts(e), err[0][:120] if err else ''))
