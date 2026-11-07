"""
Code-aware invalidation logic: the advisory sweep, staleness warnings and their
process-level dedupe, record stamping, and ``accept_code``.

This module is the policy layer between the mechanism modules -- ``codehash`` (what does
the code look like now) and ``state`` (what did it look like when each output was
materialized) -- and the engine: ``core.build()`` instantiates one :class:`Advisor` per
build and otherwise stays free of invalidation rules. ``Task._code_fingerprint`` (core)
and ``TaskData._code_ok`` (tasks) hold only the per-task completeness hooks.

Import discipline: ``core`` imports this module at top level, so everything here imports
``core`` lazily (call time only).
"""

import importlib.util
import inspect
import os
import uuid
import warnings as _stdwarnings
from datetime import datetime, timezone

from oryxflow import state
from oryxflow import codehash
from oryxflow.log import logger


class StalenessWarning(UserWarning):
    """Code changed but code_version didn't (or can't be verified). Python's default
    warning filter dedups by call site, which would silence the second run() in the
    same process -- exactly when it matters -- so the filter is 'always' and build()
    dedups itself: the SAME message prints once per process regardless of which task
    raised it -- the text names only the task family, so parameterized instances and
    per-flow builds (a WorkflowMulti run is one build per flow over shared upstreams)
    would otherwise print identical lines once per task_id and flood stdout. It re-arms
    when every task holding the message reruns or is accepted, and a CHANGED condition
    (more files edited, version bumped) re-warns immediately. RunResult.warnings
    carries the deduped message set per build; only the event stream records every
    occurrence."""


_stdwarnings.simplefilter('always', StalenessWarning)

# (task_id -> last message emitted) -- the process-level print/log dedupe above
_code_warned = {}


def code_state(task):
    """(fingerprint, dirpath) when the code-invalidation feature applies, else (None, None)."""
    fp = task._code_fingerprint
    if fp is None or not hasattr(task, '_resolved_dirpath'):
        return None, None
    return fp, task._resolved_dirpath()


_stale_said = set()


def stale_bytecode_files(task, hashes):
    """The project files of ``task``'s code (its module + the files its hash keys name) whose cached bytecode is
    stale (``codehash.stale_bytecode``): this process may have run their OLD code. Each stale cache file is removed,
    so the next import compiles the source, and a warning names the file once."""
    files = set()
    try:
        files.add(os.path.abspath(inspect.getsourcefile(type(task))))
    except (TypeError, OSError):
        pass
    try:
        root = codehash.root_for(task)
        for k in hashes or {}:
            rel = k.split('::', 1)[0]
            if rel and rel != '*' and root is not None:
                files.add(os.path.abspath(os.path.join(str(root), rel)))
    except Exception:
        pass
    stale = sorted(f for f in files if f.endswith('.py') and codehash.stale_bytecode(f))
    for f in stale:
        try:
            os.remove(importlib.util.cache_from_source(f))
        except OSError:
            pass
        if f not in _stale_said:
            _stale_said.add(f)
            msg = ('oryxflow: {} changed in the same second as its last compile, at the same size, so Python may have '
                   'run its OLD code for {}. Its cached bytecode is removed; the next run redoes the task with the '
                   'current code'.format(os.path.basename(f), task.task_family))
            logger.warning("{}", msg)
            _stdwarnings.warn(msg, UserWarning)
    return stale


def hashes_of(task):
    try:
        return codehash.task_hashes(task)
    except Exception as e:
        # degrade conservatively (invalidation goes inert for this task) but never
        # silently: a bug in the hash machinery must be visible at DEBUG
        logger.debug("task_hashes failed for {}: {}: {}",
                     task.task_id, type(e).__name__, e)
        return {}


def mtime_guard_trips(task, hashes):
    # decision 12c: local-fs heuristic only -- any hashed source newer than the output
    from oryxflow import settings
    from oryxflow.core import flatten
    if settings.cloud_fs_enabled:
        return False
    try:
        # the root the hash keys are relative to (task module, NOT cwd) -- run from
        # a subdir with its own marker and a cwd root would silently disarm the guard
        root = codehash.root_for(task)
        rels = {k.partition('::')[0] for k in hashes}   # symbol keys share files
        src = [os.stat(str(root / rel)).st_mtime for rel in rels]
        out = [os.stat(str(o.path)).st_mtime for o in flatten(task.output())
               if getattr(o, 'path', None) is not None]
        return bool(src) and bool(out) and max(src) > min(out)
    except Exception:
        return False


def comparable_record(task, rec):
    """``rec`` in the current record version's terms, or None when it can't be compared with
    the code. A record an older oryxflow wrote is checked by hashing the current code with
    that version's rules (``codehash.rules_of``): the same hashes mean the code is unchanged
    since the record, so the record gets the current hashes; different ones mean an edit, so
    the stored values stay and the normal comparison sees the change. Same for a computed
    ``code_version()`` token, which may fold code hashes."""
    from oryxflow.core import _resolve_code_version
    v = rec.get('v')
    if v == state.RECORD_V:
        return rec
    if v not in codehash.LEGACY_VERSIONS or (v == 4 and rec.get('py') != codehash.LEGACY_PY_TAG):
        return None
    try:
        with codehash.rules_of(v):
            old_hashes = codehash.task_hashes(task)
            old_token = _resolve_code_version(task)
        new_hashes = codehash.task_hashes(task)
        new_token = _resolve_code_version(task)
    except Exception as e:
        logger.debug("comparing the v{} record of {} failed: {}: {}",
                     v, task.task_id, type(e).__name__, e)
        return None
    out = dict(rec)
    out.pop('py', None)
    out['v'] = state.RECORD_V
    if (rec.get('source_hashes') or {}) == old_hashes:
        out['source_hashes'] = new_hashes
    if rec.get('code_version') == old_token:
        out['code_version'] = new_token
    return out


def _moved_deps(stored, task):
    """Which inputs differ from the record: a dependency rewritten since, or a different one
    (an imported task bound to other parameters -- another task id). '' when unknown."""
    if not stored or not hasattr(task, '_dep_ids'):
        return ''
    current = task._dep_ids()
    new = sorted(t for t in current if t not in stored)
    old = sorted(t for t in stored if t not in current)
    rewritten = sorted(t for t in current if t in stored and stored[t] != current[t])
    parts = []
    if new or old:
        parts.append('now {}'.format(', '.join(new)) + (' (was {})'.format(', '.join(old))
                                                         if old else ''))
    if rewritten:
        parts.append('rewritten {}'.format(', '.join(rewritten)))
    return '; '.join(parts)


def make_record(task, hashes, output_id, duration=None):
    # one shape for every stamp: both dimensions (own hashes + dep output-identity
    # fold) recorded on every write regardless of mode, plus the live fingerprint
    # (informational), the last materialization cost (drives the
    # expensive-recompute guard) and the schema/interpreter tags
    from oryxflow.core import _resolve_code_version
    return {'fingerprint': task._code_fingerprint,
            'code_version': _resolve_code_version(task),
            'source_hashes': hashes, 'output_id': output_id,
            'dep_state': task._dep_state() if hasattr(task, '_dep_state') else None,
            # per dep, so a rerun reason can name the input that moved
            'dep_ids': task._dep_ids() if hasattr(task, '_dep_ids') else None,
            'duration_s': duration,
            'v': state.RECORD_V,
            'ts': datetime.now(timezone.utc).isoformat()}


class Advisor:
    """Per-build code advisory: sweeps complete subtrees (grandfather-stamp, mode-flip
    convergence, staleness warnings) and derives rerun reasons. One instance per
    ``core.build()``; ``emit`` is the build's event+log channel."""

    def __init__(self, emit):
        self.emit = emit
        self.warned = []      # unacknowledged code-change warning strings (RunResult)
        self.advised = set()  # task_ids already checked this build
        self.unverified = {}  # why -> [task_id] kept without comparing code (see finish)

    def _unverified(self, task, why, note):
        # kept complete without any way to compare its code: one summary warning per build
        # (finish), plus a per-task event so print_status() lists it until rerun or accepted
        self.unverified.setdefault(why, []).append(task.task_id)
        self.emit('code_warning',
                  {'task_id': task.task_id, 'family': task.task_family,
                   'changed_files': [note], 'kind': why,
                   'code_version': None}, level='warning')

    def finish(self):
        """After the build: one warning naming every task kept without comparing its code."""
        texts = {
            'old_record': (
                "{n} task(s) have code-status records from an older oryxflow or another "
                "Python version, which can't be compared with the current code, so they were "
                "kept without rerunning: {ids}. If you edited one since its last run, rerun "
                "it: flow.reset(<Task>) (flow.reset_downstream(<Task>) for everything after "
                "it)."),
            'newer_record': (
                "{n} task(s) have code-status records from a NEWER oryxflow (record version "
                "above " + str(state.RECORD_V) + "), so this oryxflow can't compare them with "
                "the code; they were kept as they are, without rerunning or re-stamping: "
                "{ids}. Run this data folder with the newer oryxflow."),
        }
        for why, ids in self.unverified.items():
            shown = ', '.join(ids[:10]) + (' ... and {} more'.format(len(ids) - 10)
                                           if len(ids) > 10 else '')
            msg = 'oryxflow: ' + texts[why].format(n=len(ids), ids=shown)
            if msg not in self.warned:
                self.warned.append(msg)
            try:
                _stdwarnings.warn(msg, StalenessWarning)
            except Exception:
                pass
            logger.warning("{}", msg)

    def warn(self, task, msg, changed_files):
        # decision 12a: the advisory must be visible without enable_logging(), so it
        # goes out on every channel -- warnings.warn, loguru, the event, RunResult.
        # The noisy channels (warnings.warn + loguru) dedupe per process on the
        # message -- see StalenessWarning. RunResult carries the deduped message set
        # for the build (tooling reads len(result.warnings) as "how many pending
        # conditions", so parameterized instances of one family must not inflate it);
        # only the event stream records every occurrence.
        from oryxflow.core import _resolve_code_version
        if msg not in self.warned:
            self.warned.append(msg)
        # message-level: parameterized instances of one family share the exact text
        fresh = msg not in _code_warned.values()
        _code_warned[task.task_id] = msg
        if fresh:
            try:
                _stdwarnings.warn(msg, StalenessWarning)
            except Exception:
                # an app-level 'error' warning filter (-W error, filterwarnings)
                # turns warn() into a raise; an advisory must never abort a build
                # mid-run -- RunResult, the event stream and loguru still carry it
                pass
        self.emit('code_warning',
                  {'task_id': task.task_id, 'family': task.task_family,
                   'changed_files': changed_files,
                   'code_version': _resolve_code_version(task)},
                  msg=msg if fresh else None, level='warning')

    def _put(self, dirpath, tid, record):
        # bookkeeping only: the next build writes it again, so a locked status file (Windows
        # sync/antivirus) must never abort a build that is otherwise fine
        try:
            state.put_record(dirpath, tid, record)
        except Exception as e:
            _stdwarnings.warn('oryxflow: could not write the code-status record for {}: {}: {} '
                              '-- the next build writes it'.format(tid, type(e).__name__, e),
                              UserWarning)

    def advise(self, task):
        # advisory sweep over a complete subtree, POST-ORDER (deps first, so re-stamped
        # dep records exist before this task's dep_state folds their output_ids):
        # grandfather-stamp missing records, converge records across mode flips and
        # schema/interpreter migrations (preserving output_id so nothing ripples
        # downstream), and warn when a pinned task's code changed without a bump
        from oryxflow import settings
        from oryxflow.core import flatten, _resolve_code_version
        tid = task.task_id
        if tid in self.advised:
            return
        self.advised.add(tid)
        if getattr(task, 'external', False):
            # produced elsewhere (another project's export): its record belongs to the
            # producer, compared against the producer's code -- never checked or stamped here
            return
        fp = task._code_fingerprint
        if fp is None:
            # fingerprint folds deps, so None means the entire upstream subtree is
            # untracked too -- nothing to advise anywhere above this task
            return
        for dep in flatten(task.requires()):
            self.advise(dep)
        dirpath = task._resolved_dirpath() if hasattr(task, '_resolved_dirpath') else None
        if dirpath is None:
            return
        try:
            outputs = flatten(task.output())
            outputs_exist = bool(outputs) and all(o.exists() for o in outputs)
        except Exception:
            outputs_exist = False
        if not outputs_exist:
            return
        rec = state.get_record(dirpath, tid)
        if rec is None:
            # grandfathering: output exists, record missing -> treat as current and
            # stamp a baseline (state-only, no event), unless the output predates
            # the code on disk
            hashes = hashes_of(task)
            if mtime_guard_trips(task, hashes):
                self.warn(task, (
                    "task {}: output predates current code; can't verify -- "
                    "reset() to recompute, or accept_code on a task instance / "
                    "flow.accept_code() to confirm the output is current (stamps a "
                    "baseline record)").format(task.task_family),
                    sorted(hashes))
            else:
                self._put(dirpath, tid,
                          make_record(task, hashes, uuid.uuid4().hex[:16]))
            return
        if state.is_newer(rec):
            # written by a newer oryxflow: keep it as it is (no compare, no re-stamp) and
            # say so -- the newer library compares it on its next run
            self._unverified(task, 'newer_record',
                             '<not compared: record from a newer oryxflow (v{})>'
                             .format(rec.get('v')))
            return
        old_version = rec.get('v') != state.RECORD_V
        if old_version:
            translated = comparable_record(task, rec)
            if translated is None:
                # can't be compared with the code at all: keep it (grandfather trust),
                # PRESERVING output_id (and the recorded run cost) so downstream dep_state
                # folds don't move -- and say so, naming the task (finish)
                self._unverified(task, 'old_record',
                                 '<not compared: record from an older oryxflow or Python>')
                self._put(dirpath, tid,
                          make_record(task, hashes_of(task),
                                      rec.get('output_id') or uuid.uuid4().hex[:16],
                                      duration=rec.get('duration_s')))
                return
            # compared exactly: an unchanged task is re-stamped below, an edited one that is
            # still complete (pin, expensive guard) warns and keeps its old record
            rec = translated
        stored = rec.get('source_hashes') or {}
        current = hashes_of(task)
        own = _resolve_code_version(task)
        if stored and current and stored != current:
            # code changed but the task is (deliberately) still complete: pinned by an
            # explicit code_version, or held by the expensive-recompute guard. Warn at
            # the decision point with the exits; never re-stamp here (that would bless
            # the change) -- accept_code is the explicit blessing.
            changed = sorted(k for k in set(stored) | set(current)
                             if stored.get(k) != current.get(k))
            if callable(task.code_version):
                # computed token: "bump code_version" is not the action -- the token
                # only moves when what the method reads or returns moves
                self.warn(task, (
                    "task {}: {} changed since cached run; code_version() still "
                    "computes {} -- reusing cached output. It reruns when what "
                    "code_version() returns changes; otherwise oryxflow.accept_code({}) "
                    "only if certain the output is equivalent (best-effort check: "
                    "can't see data files or dynamic calls).").format(
                        task.task_family, ', '.join(changed),
                        own, task.task_family),
                    changed)
            elif own is not None:
                self.warn(task, (
                    "task {}: {} changed since cached run; code_version still {} -- "
                    "reusing cached output. Bump code_version to recompute, or "
                    "oryxflow.accept_code({}) only if certain the output is "
                    "equivalent -- when unsure, bump (best-effort check: can't "
                    "see data files or dynamic calls).").format(
                        task.task_family, ', '.join(changed),
                        own, task.task_family),
                    changed)
            elif not settings.code_version_auto:
                self.warn(task, (
                    "task {}: {} changed since cached run -- reusing cached output, "
                    "because automatic code invalidation is off "
                    "(settings.code_version_auto = False). reset() to recompute, "
                    "oryxflow.accept_code({}) if output-equivalent, or set code_version "
                    "to manage this task explicitly.").format(
                        task.task_family, ', '.join(changed), task.task_family),
                    changed)
            else:
                self.warn(task, (
                    "task {}: {} changed since cached run; last run took {:.0f}s -- "
                    "reusing cached output (expensive-recompute guard, "
                    "settings.code_version_auto_expensive_s={}). reset() to recompute, "
                    "oryxflow.accept_code({}) if output-equivalent, or set "
                    "code_version to manage this task explicitly.").format(
                        task.task_family, ', '.join(changed),
                        rec.get('duration_s') or 0,
                        getattr(settings, 'code_version_auto_expensive_s', None),
                        task.task_family),
                    changed)
        elif (old_version or rec.get('fingerprint') != fp
                or rec.get('code_version') != own):
            # hashes match but the record predates a mode flip (pin added/removed with
            # unchanged code), an upstream toggle or this record version -- converge
            # silently, preserving output_id and run cost
            self._put(dirpath, tid,
                      make_record(task, current,
                                  rec.get('output_id') or uuid.uuid4().hex[:16],
                                  duration=rec.get('duration_s')))

    def reason_for(self, task, ran, reasons):
        # why is this (incomplete) task about to run? deps were processed first, so a
        # rerun upstream is already in `ran`.
        from oryxflow.core import flatten, _resolve_code_version
        try:
            outputs = flatten(task.output())
            if not outputs or not all(o.exists() for o in outputs):
                return 'output missing'
        except Exception:
            return 'output missing'
        fp, dirpath = code_state(task)
        if fp is not None:
            rec = state.get_record(dirpath, task.task_id)
            if rec is not None:
                older = rec.get('v') != state.RECORD_V
                rec = comparable_record(task, rec) or rec
                own = _resolve_code_version(task)
                if rec.get('code_version') != own:
                    # a task whose computed code_version folds files can name the one that
                    # moved (TaskNotebook: "template dep viz/report.py")
                    detail = getattr(task, 'code_change_detail', None)
                    try:
                        detail = detail() if callable(detail) else None
                    except Exception:
                        detail = None
                    if detail:
                        return 'code change ({})'.format(detail)
                    return 'code change ({} -> {})'.format(
                        rec.get('code_version') if rec.get('code_version') is not None
                        else 'auto',
                        own if own is not None else 'auto')
                if own is None:
                    # own dimension moved (auto): name the changed symbols
                    stored = rec.get('source_hashes') or {}
                    current = hashes_of(task)
                    changed = sorted(k for k in set(stored) | set(current)
                                     if stored.get(k) != current.get(k))
                    if changed:
                        shown = ', '.join(changed[:3]) + ('...' if len(changed) > 3 else '')
                        return 'code change (auto: {})'.format(shown)
                # own code unchanged: the dependency fold moved. Name an upstream rerun only
                # when one happened in this build -- otherwise say what did happen
                if any(d.task_id in reasons or d in ran for d in flatten(task.requires())):
                    return 'upstream rerun'
                if older:
                    return 'dependency check re-based (record from an older oryxflow)'
                moved = _moved_deps(rec.get('dep_ids'), task)
                if moved:
                    return 'upstream output changed since last run: {}'.format(moved)
                return 'upstream output changed since last run'
        if any(d.task_id in reasons or d in ran for d in flatten(task.requires())):
            return 'upstream rerun'
        return 'upstream rerun' if flatten(task.requires()) else 'output missing'


def accept_code(task=None):
    """
    Acknowledge an output-equivalent code change: re-stamp the stored code records at
    current without rerunning, so neither the auto rerun nor the "code changed but
    code_version didn't" warning fires. The three exits from every code change: bump
    ``code_version`` / let auto rerun (semantic change), ``accept_code`` (equivalent
    refactor -- only if certain; when unsure, recompute), or ``reset()`` (recompute
    regardless).

    Args:
        task (obj, class, list): task INSTANCE re-stamps that task AND its entire
            upstream dependency tree in its data dir (a shared-helper edit changes the
            stored source hashes of every task referencing the symbol, so acceptance
            must cover the whole band) -- call it on the most-downstream task you judge
            equivalent, typically the flow's default task, or use
            ``Workflow.accept_code()``. This is also the form that clears the
            "output predates current code" warning: an output with no stored record
            gets a fresh baseline record stamped at current. A task CLASS re-stamps
            every EXISTING record of that family in ``settings.dirpath`` (it cannot
            create missing records -- use the instance/flow form for that). A LIST
            dispatches each element as above with ONE shared walk (overlapping upstream
            trees are stamped once) -- pass every final of a multi-final pipeline.
            With no argument, bulk-accepts: every record in ``settings.dirpath`` whose
            stored hashes differ from the current code is re-stamped. Accepting never
            touches a record's ``output_id``, so it never triggers downstream
            recomputes.

    Returns: list of task_ids re-stamped
    """
    from oryxflow import core, events, settings

    now = datetime.now(timezone.utc).isoformat()
    accepted = []

    def _restamp(dirpath, task_id, rec, hashes, family, fingerprint=None, dep_state=None,
                 dep_ids=None):
        if state.is_newer(rec):
            newer.append(task_id)   # a newer oryxflow's record: never rewritten here
            return
        rec = dict(rec)   # preserves output_id: accepting must not ripple downstream
        if fingerprint is not None:
            rec['fingerprint'] = fingerprint
        if dep_state is not None:
            rec['dep_state'] = dep_state
            rec['dep_ids'] = dep_ids
        rec['source_hashes'] = hashes
        rec.pop('py', None)   # pre-v5 interpreter tag
        rec['v'] = state.RECORD_V
        rec['ts'] = now
        state.put_record(dirpath, task_id, rec)
        # the stamp IS the acceptance -- count it before the reporting channels,
        # so a reporting hiccup can't misfile a stamped task under `skipped`
        accepted.append(task_id)
        _code_warned.pop(task_id, None)   # accepted; a future recurrence re-warns
        try:
            events.append('code_accepted',
                          {'task_id': task_id, 'family': family, 'source_hashes': hashes})
            logger.info("accepted code change for {}", task_id)
        except Exception:
            pass

    skipped = []
    unverified = []
    newer = []
    # one shared walk across a list of roots: multi-final pipelines overlap upstream,
    # each shared task is visited and stamped once
    seen = set()

    def _walk(t):
        # instance form: walk the task and its upstream dep tree POST-ORDER (deps
        # first, so dep_state folds re-stamped dep records), re-stamping each existing
        # record's own dimension (hashes) to current. Each node is fault-isolated: one
        # task whose requires()/output() raises (e.g. needs inputs to enumerate) must
        # not abort blessing the rest of the tree.
        if t.task_id in seen:
            return
        seen.add(t.task_id)
        if getattr(t, 'external', False):
            return    # another project's record: only its producer may accept its code
        # every ingredient degrades independently: the fingerprint folds deps
        # recursively (so one broken requires() upstream would poison it), and a
        # broken requires() must not stop THIS node's blessing -- fingerprint and
        # dep_state are secondary record fields that then just keep their stored
        # values
        try:
            fp = t._code_fingerprint
            if fp is None:
                # None folds upward: the entire upstream subtree is untracked too
                return
        except Exception:
            fp = None             # unavailable, still bless this node
        try:
            deps = core.flatten(t.requires())
        except Exception:
            deps = []
        for dep in deps:
            _walk(dep)
        try:
            dep_state = t._dep_state() if hasattr(t, '_dep_state') else None
            dep_ids = t._dep_ids() if hasattr(t, '_dep_ids') else None
        except Exception:
            dep_state = dep_ids = None
        try:
            dirpath = t._resolved_dirpath() if hasattr(t, '_resolved_dirpath') \
                else settings.dirpath
            rec = state.get_record(dirpath, t.task_id)
            if rec is None:
                # no record but the output exists (grandfathered output held back by
                # the "predates current code" mtime guard): accepting IS the blessing
                # -- stamp a fresh baseline so the guard is satisfied from here on
                outputs = core.flatten(t.output())
                if not outputs or not all(o.exists() for o in outputs):
                    return
                rec = {'output_id': uuid.uuid4().hex[:16],
                       'code_version': core._resolve_code_version(t),
                       'duration_s': None}
            _restamp(dirpath, t.task_id, rec, codehash.task_hashes(type(t)),
                     t.task_family, fingerprint=fp, dep_state=dep_state, dep_ids=dep_ids)
        except Exception:
            skipped.append(t.task_id)

    with core.traversal_scope():
      for task in (list(task) if isinstance(task, (list, tuple, set)) else [task]):
        if task is not None and not isinstance(task, type):
            _walk(task)
        elif task is not None:
            cls = task
            family = cls.task_family
            hashes = codehash.task_hashes(cls)
            dirpath = settings.dirpath
            for tid, rec in state.all_records(dirpath).items():
                if tid.split('_')[0] == family and rec is not None:
                    _restamp(dirpath, tid, rec, hashes, family)
        else:
            dirpath = settings.dirpath
            root = codehash._project_root()
            for tid, rec in state.all_records(dirpath).items():
                stored = rec.get('source_hashes') or {}
                current = {}
                changed = False
                stale_keys = False
                for key, digest in stored.items():
                    cur = codehash.current_hash_for_key(root, key)
                    if cur is None:
                        # file gone or symbol renamed/moved: the record alone can't say
                        # what the key SHOULD be now -- keep the stored digest and flag
                        # it, so this doesn't read as "verified current"
                        current[key] = digest
                        stale_keys = True
                        continue
                    current[key] = cur
                    if cur != digest:
                        changed = True
                if changed:
                    _restamp(dirpath, tid, rec, current, tid.split('_')[0])
                if stale_keys:
                    unverified.append(tid)
    # visible without enable_logging(), like the execution summary: silence here
    # reads as false confidence that something was accepted
    if accepted:
        shown = ', '.join(accepted[:5]) + ('...' if len(accepted) > 5 else '')
        print('accept_code: re-stamped {} record(s): {}'.format(len(accepted), shown))
    else:
        print('accept_code: nothing accepted (no matching stored records -- to bless '
              'outputs that have no record yet, pass a task instance or use '
              'flow.accept_code())')
    if skipped:
        shown = ', '.join(skipped[:5]) + ('...' if len(skipped) > 5 else '')
        print('accept_code: skipped {} task(s) that errored while walking the tree: {} '
              '-- accept them individually with a task instance'.format(
                  len(skipped), shown))
    if newer:
        shown = ', '.join(newer[:5]) + ('...' if len(newer) > 5 else '')
        print('accept_code: left {} record(s) from a newer oryxflow as they are: {} '
              '-- accept them with that oryxflow'.format(len(newer), shown))
    if unverified:
        shown = ', '.join(unverified[:5]) + ('...' if len(unverified) > 5 else '')
        print('accept_code: {} record(s) reference symbols or files that no longer '
              'exist (renamed or moved?): {} -- the bulk form cannot re-key those; '
              'accept them with a task instance or flow.accept_code()'.format(
                  len(unverified), shown))
    return accepted
