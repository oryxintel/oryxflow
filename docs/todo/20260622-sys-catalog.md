# Data-trust / semantic catalog for task outputs

> **Superseded by `20260622-sys-catalog2.md`** — v2 rebases the storage substrate onto the
> unified event stream of `20260712-engine-code-invalidation.md` (drops the private
> SQLAlchemy DB, moves to `.oryxflow/`, enrichment becomes events). The semantics model,
> capture/profile logic, enrichment seam, and MCP/plugin/docs/tests sections here remain
> the referenced spec for v2 — read both.

> Executable spec. Self-contained — written so a clean session can implement it without the
> planning conversation. High-level design rationale is condensed into Context + Design
> decisions below.

## Context

oryxflow runs a DAG of `Task`s; each `save()`s an output that today is *just a file on disk*.
The primary consumer we now design for is an **AI coding agent** (Claude) operating through
the **oryxflow Claude plugin** (`the oryxflow-claude-plugin repo`) and the
**oryxflow-mcp** server (`the oryxflow-mcp repo`), increasingly asked to *analyze*
the data, not just build the pipeline.

The trust problem (see `docs/todo/AI for Financial Analysis_ Trust Starts With Data,
Verification, and Governance.md`): the dominant AI failure mode for data analysis is **not**
dramatic hallucination — it's **reference error / context loss**: an AI uses a right-looking
number in the wrong context (wrong period, unit, segment, definition) and states it
confidently. The prescribed fix is a **semantic layer that travels with the data** (per-field
definition, unit, period, segment, data-type, source/citation, change-vs-prior) plus
**determinism, verifiability, and governance** — the four pillars: *accurate, consistent,
verifiable, governable*.

The gap today (confirmed in the plugin/MCP repos): the agent's only trust sources are
`tasks.py` docstrings and `docs/oryxflow-data.md` — it can describe what the *code claims*, not
what the *data actually is*. Without executing Python it cannot answer: *what outputs exist?
what's the real schema? what does this column mean? where did this number come from? is it
fresh?* `oryxflow-mcp` exposes task CRUD + run/preview but **no** data/schema/metadata/lineage
introspection.

This feature is an **event-driven catalog**: on `save()`/task-completion, automatically record
a rich, authoritative **descriptor** of each output to a SQLAlchemy database (local SQLite by
default; any DB via URL), enabled once then automatic. The result is a queryable, **versioned**
catalog the agent reads — via new read-only MCP tools, a JSON export, and a regenerated
`docs/oryxflow-data.md` — to work with the data reliably and safely.

### Design decisions

All confirmed by the user during planning:

1. **It's a data-trust / semantic layer for AI consumers** — not experiment tracking
   (leaderboards) and not pipeline-ops monitoring ("did the daily job run / what failed").
   Rejected both framings explicitly.
2. **Semantics are AUTOMATIC, not author-declared.** No `describe={...}` boilerplate on tasks.
   We intercept `save()` where the **code and data are both present**, and derive meaning from
   them. Rejected a declarative `describe` attribute.
   - **Recommended `field_semantics` keys** (free-form dict, but this is the standard set the
     enricher targets): `definition`, `unit`, `period`, `segment`/`geography`, `data_type`
     (rate | level | growth | ratio | index | count), `source`, `change_vs_prior`,
     **`derivation`** (how the column was computed and the rule behind it),
     **`role`** (`authoritative` | `reference` | `derived`), and **`caveats`** (free-text
     gotchas).
   - **Two gotcha *classes* the descriptor must capture** (the highest-value, hardest-to-spot
     trust failures — stated as general principles, not tied to any one dataset):
     1. *Derivation depends on `data_type`.* The correct transform for a column is determined
        by what the column **is**. The same operation differs by type — e.g. a
        period-over-period change of a **rate** is an additive difference (in the rate's own
        units, e.g. percentage points), whereas for a **level** it is a growth ratio. A value
        is meaningless without knowing which rule produced it and in what units. This is
        recoverable from the `run()` source, so it belongs in `derivation`/`data_type`/`unit`.
     2. *Authoritative-vs-reference role.* When multiple columns are each individually valid
        but only one is the **canonical/authoritative** source for a concept (others being
        derived cross-checks or alternates), a consumer must know which to use. This is the
        most dangerous class because every candidate looks correct in isolation. Captured via
        the per-field `role` key.
3a. **Enrichment evidence includes existing trust prose.** The evidence bundle the enricher
   reads is not just data + `run()` source — it also includes the task **docstring** and, when
   present, the project's `docs/oryxflow-data.md` **Business rules** section. Hand-maintained
   trust prose is where such gotchas conventionally live; the catalog should turn that prose
   into structured, code-grounded `field_semantics` rather than depend on a human re-writing
   it (and can flag drift when code and prose disagree).
3. **Two-phase to stay fast + deterministic: capture → enrich.**
   - *Capture* (synchronous, in `save()`/`build()`; cheap; deterministic): an **evidence
     bundle** — provenance, structural profile, a bounded data **sample**, and the `run()`
     **source**. Zero task changes.
   - *Enrich* (out-of-band; LLM): generate per-field semantics from the evidence bundle.
     **Never in the run path** (a slow/absent LLM must not affect runs). Determinism preserved
     by **caching semantics keyed by `(code_hash, schema_hash)`** — identical code+schema →
     identical stored descriptor; regenerate only on change; record the model used +
     `review_status`. The enricher is **pluggable** (default: none in OSS; the plugin agent or
     the d6t hosted service supplies it).
4. **SQLAlchemy backend**, SQLite file default, any DB via URL. **Lazy optional dependency**
   (`oryxflow[catalog]`), imported only inside `enable_catalog` — base install stays light
   (matches the cloud-storage extras pattern). Rejected JSON-lines and a hand-rolled sqlite
   layer.
5. **Closed sink, no public `add_sink()`/callback API.** Data lands in d6t's environment;
   advanced analytics is the product. OSS = local store + thin raw readers only.
6. **Versioned history** (one row per generation) — enables drift / change-vs-prior, supports
   the consistency pillar. Rejected current-state-only upsert.
7. **Source citations only in v1** — provenance + lineage down to raw sources (the citation
   trail). **No** declared data-contract pass/fail assertions in v1 (deferred to v2).
8. **Opt-in, off by default, never breaks a run.** Mirrors the `enable_logging()` pattern in
   `oryxflow/log.py` exactly: silent until the app opts in; every capture path is
   try/except-wrapped and warns via loguru rather than propagating.
9. **Naming:** `enable_catalog` / `disable_catalog` / `oryxflow.catalog`.
10. **Agent access surface:** new read-only MCP tools **+** JSON `export()` **+** regenerated
    `docs/oryxflow-data.md`. (User delegated this choice.)

### v1 scope cut line

IN: capture (evidence bundle on every save) · versioned SQLAlchemy store · lineage + raw-source
citations · Python readers · JSON/markdown export · read-only MCP tools · plugin skill update ·
enrichment **seam** with `(code_hash, schema_hash)` caching and a pluggable enricher.
OUT (v2): a bundled LLM enricher, data-contract assertions/validation status, drift analytics UI.

## Implementation

Steps 1–9 are the **oryxflow library** (the bulk). Steps 10–11 are cross-repo
(oryxflow-mcp, plugin). Steps 12–13 are docs + tests.

### 1. `oryxflow/settings.py` — add catalog settings

After the logging settings block (around line 14), add:

```python
# data catalog / trust layer (see docs/todo/20260622-sys-catalog.md)
catalog_enabled = False           # set by oryxflow.enable_catalog(); do not set directly
catalog_url = None                # SQLAlchemy URL; None -> sqlite at dirpath/'.oryxflow-catalog.db'
catalog_sample_rows = 10          # rows sampled into the evidence bundle
catalog_profile_max_rows = 1_000_000  # above this, profile on a sample (bounded cost)
```

### 2. `oryxflow/catalog.py` — new module (the engine)

New file. **Top-level imports are stdlib only** (`hashlib`, `inspect`, `json`, `datetime`,
`sys`) so it is import-cycle-safe (same discipline as `log.py`). SQLAlchemy and pandas are
imported lazily inside functions. Structure:

```python
import sys, json, hashlib, inspect
from datetime import datetime, timezone
from oryxflow.log import logger

_enabled = False
_engine = None
_metadata = None          # sqlalchemy MetaData with the tables
_tables = {}              # {'artifacts': Table, 'lineage': Table}
_current = []             # stack of run-context dicts (reentrant flow-within-flow safe)
_enricher = None          # callable(evidence_dict) -> {'description', 'field_semantics'}
__version__ = None         # cached oryxflow version string

def _build_schema(metadata):
    from sqlalchemy import Table, Column, Integer, Float, String, Text, JSON, DateTime
    artifacts = Table('artifacts', metadata,
        Column('artifact_id', Integer, primary_key=True, autoincrement=True),
        Column('run_id', String), Column('task_id', String, index=True),
        Column('task_family', String, index=True), Column('persist_key', String),
        Column('path', Text), Column('format', String), Column('params', JSON),
        Column('code_hash', String), Column('schema_hash', String),
        Column('oryxflow_version', String), Column('generated_ts', DateTime),
        Column('duration_s', Float), Column('status', String),
        Column('shape_rows', Integer), Column('shape_cols', Integer), Column('size_bytes', Integer),
        Column('columns', JSON), Column('dtypes', JSON), Column('profile', JSON),
        Column('sample', JSON), Column('run_source', Text),
        Column('description', Text), Column('field_semantics', JSON),
        Column('semantics_model', String), Column('review_status', String))
    lineage = Table('lineage', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('run_id', String), Column('downstream_task_id', String, index=True),
        Column('upstream_task_id', String), Column('upstream_task_family', String),
        Column('source_ref', Text))           # raw-source citation at DAG roots
    return {'artifacts': artifacts, 'lineage': lineage}

def enable_catalog(url=None):
    """Turn on the oryxflow data catalog. Idempotent. Creates tables if missing.
    url: SQLAlchemy URL; default sqlite at <dirpath>/.oryxflow-catalog.db."""
    global _enabled, _engine, _metadata, _tables
    from sqlalchemy import create_engine, MetaData
    from oryxflow import settings                  # lazy: settings->core->log cycle
    if url is None:
        url = settings.catalog_url or f"sqlite:///{settings.dirpath/'.oryxflow-catalog.db'}"
        settings.dirpath.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url)
    _metadata = MetaData()
    _tables = _build_schema(_metadata)
    _metadata.create_all(_engine)
    settings.catalog_enabled = _enabled = True
    logger.info("data catalog enabled: {}", url)
    return _engine

def disable_catalog():
    global _enabled
    from oryxflow import settings
    settings.catalog_enabled = _enabled = False
```

Helpers (all defensive — wrapped by callers):

```python
def _version():
    global __version__
    if __version__ is None:
        try:
            from importlib.metadata import version; __version__ = version('oryxflow')
        except Exception:
            __version__ = 'unknown'
    return __version__

def _code_hash(task):
    try:
        src = inspect.getsource(type(task).run)
        return hashlib.md5(src.encode()).hexdigest()[:10], src
    except Exception:
        return None, None

def _profile(obj):
    """Best-effort structural profile + sample for a single output object.
    Returns dict with columns/dtypes/shape/profile/sample (all nullable)."""
    out = {'columns': None, 'dtypes': None, 'shape_rows': None, 'shape_cols': None,
           'profile': None, 'sample': None, 'schema_hash': None}
    try:
        import pandas as pd
        from oryxflow import settings
        if isinstance(obj, pd.DataFrame):
            cols = [str(c) for c in obj.columns]
            dtypes = {str(c): str(t) for c, t in obj.dtypes.items()}
            out['columns'], out['dtypes'] = cols, dtypes
            out['shape_rows'], out['shape_cols'] = int(len(obj)), int(obj.shape[1])
            prof = obj if len(obj) <= settings.catalog_profile_max_rows else obj.head(settings.catalog_profile_max_rows)
            nulls = {c: int(prof[c].isna().sum()) for c in prof.columns}
            ranges = {}
            for c in prof.select_dtypes('number').columns:
                ranges[str(c)] = [float(prof[c].min()), float(prof[c].max())]
            out['profile'] = {'nulls': nulls, 'ranges': ranges, 'index': [str(i) for i in obj.index.names]}
            out['sample'] = json.loads(obj.head(settings.catalog_sample_rows).to_json(orient='records', date_format='iso'))
            out['schema_hash'] = hashlib.md5(json.dumps([cols, dtypes], sort_keys=True).encode()).hexdigest()[:10]
    except Exception as e:
        logger.debug("catalog profile skipped: {}", e)
    return out
```

### 3. `oryxflow/catalog.py` — capture API (called from save)

```python
def record_artifact(task, persist_key, obj):
    """Record one saved output's descriptor (evidence bundle). No-op if disabled; never raises."""
    if not _enabled:
        return
    try:
        from sqlalchemy import insert
        ctx = _current[-1] if _current else {}
        code_hash, run_source = _code_hash(task)
        prof = _profile(obj)
        try:
            path = str(task._getpath(persist_key))
        except Exception:
            path = None
        size = None
        try:
            import pathlib; p = pathlib.Path(path); size = p.stat().st_size if p.exists() else None
        except Exception:
            pass
        row = dict(run_id=ctx.get('run_id'), task_id=task.task_id, task_family=task.task_family,
            persist_key=persist_key, path=path, format=getattr(task, 'target_ext', None),
            params=task.to_str_params(only_significant=True), code_hash=code_hash,
            schema_hash=prof['schema_hash'], oryxflow_version=_version(),
            generated_ts=datetime.now(timezone.utc), duration_s=None, status='ok',
            shape_rows=prof['shape_rows'], shape_cols=prof['shape_cols'], size_bytes=size,
            columns=prof['columns'], dtypes=prof['dtypes'], profile=prof['profile'],
            sample=prof['sample'], run_source=run_source, description=None,
            field_semantics=_cached_semantics(code_hash, prof['schema_hash']),
            semantics_model=None, review_status='pending')
        with _engine.begin() as conn:
            conn.execute(insert(_tables['artifacts']), row)
        logger.debug("catalog recorded artifact {} key={}", task.task_id, persist_key)
    except Exception as e:
        logger.warning("catalog record_artifact failed for {}: {}", getattr(task, 'task_id', '?'), e)

def record_save(task, data, from_list=False):
    """Dispatch a save() into one record_artifact per persist key. Mirrors save() shape."""
    if not _enabled:
        return
    try:
        if getattr(task, 'persist', None) == ['data']:
            record_artifact(task, 'data', data)
        else:
            d = dict(zip(task.persist, data)) if from_list else data
            for k, v in (d.items() if isinstance(d, dict) else []):
                record_artifact(task, k, v)
    except Exception as e:
        logger.warning("catalog record_save failed: {}", e)
```

`_cached_semantics(code_hash, schema_hash)` does a `SELECT field_semantics FROM artifacts
WHERE code_hash=? AND schema_hash=? AND field_semantics IS NOT NULL LIMIT 1` so a re-save of
unchanged code+schema reuses prior enrichment (determinism). Returns None if none yet.

### 4. `oryxflow/catalog.py` — run context + lineage (called from build)

```python
def run_begin():
    if not _enabled: return None
    import uuid
    rid = uuid.uuid4().hex[:12]
    _current.append({'run_id': rid})
    return rid

def run_end(run_id):
    if not _enabled: return
    if _current and _current[-1].get('run_id') == run_id:
        _current.pop()

def set_current_task(task):     # set before task.run(), so save() sees task context if needed
    if _enabled and _current: _current[-1]['task_id'] = task.task_id

def stamp_duration(run_id, task_id, duration_s):
    if not _enabled: return
    try:
        from sqlalchemy import update
        t = _tables['artifacts']
        with _engine.begin() as conn:
            conn.execute(update(t).where(t.c.run_id==run_id, t.c.task_id==task_id,
                                         t.c.duration_s.is_(None)).values(duration_s=duration_s))
    except Exception as e:
        logger.warning("catalog stamp_duration failed: {}", e)

def record_lineage(run_id, task):
    """Record upstream edges for a completed task; raw-source ref at external/root deps."""
    if not _enabled: return
    try:
        from sqlalchemy import insert
        from oryxflow.core import flatten
        rows = []
        for dep in flatten(task.requires()):
            src = None
            if getattr(dep, 'external', False) or getattr(dep, 'run', None) is None:
                try: src = str(dep._getpath(dep.persist[0]))
                except Exception: src = None
            rows.append(dict(run_id=run_id, downstream_task_id=task.task_id,
                upstream_task_id=dep.task_id, upstream_task_family=dep.task_family, source_ref=src))
        if rows:
            with _engine.begin() as conn:
                conn.execute(insert(_tables['lineage']), rows)
    except Exception as e:
        logger.warning("catalog record_lineage failed: {}", e)
```

### 5. `oryxflow/tasks/__init__.py:save()` — capture hook

`save()` is at line 243; the existing trailing log is at line 263. Add the capture call right
after it (data still in scope, file already written so `size_bytes` is available):

```python
        logger.debug("saved {} keys={}", self.task_id, list(self.persist))
        import oryxflow.catalog as _catalog          # import-cycle-safe (stdlib-only top imports)
        _catalog.record_save(self, data, from_list)
```

(Module-level `import oryxflow.catalog as _catalog` is also fine since catalog.py has no
oryxflow top-level imports; inline keeps the diff minimal and the disabled path a single
no-op call.)

### 6. `oryxflow/core.py:build()` — run context, duration, lineage

In `build()` (line 454): wrap the run with a catalog run context and stamp completion data.

- After `tasks = [tasks]` normalization (~line 467), add:
  ```python
  import oryxflow.catalog as _catalog
  _run_id = _catalog.run_begin()
  ```
- In `_process`, before `task.run()` (line 512) add `_catalog.set_current_task(task)`.
- After the success log (line 526–529), add:
  ```python
  logger.info("task complete: {} in {:.3f}s", tid, time.perf_counter() - t0)
  _catalog.stamp_duration(_run_id, tid, time.perf_counter() - t0)
  _catalog.record_lineage(_run_id, task)
  ran.append(task)
  ```
- Before `return RunResult(...)` (line 565), add `_catalog.run_end(_run_id)` (also call it on
  the early `return`s is unnecessary — `run_end` is id-guarded and the stack is per-call;
  simplest is a `try/finally` around the `for task in tasks:` loop so `run_end` always fires).

All `_catalog.*` calls are no-ops when the catalog is disabled (the common case), so the hot
path cost is one cheap function call per task.

### 7. `oryxflow/catalog.py` — readers (`catalog` object) + export

```python
class _Catalog:
    def _df(self, sql, params=None):
        import pandas as pd
        if not _enabled: raise RuntimeError("catalog disabled; call oryxflow.enable_catalog()")
        with _engine.connect() as conn:
            return pd.read_sql(sql, conn, params=params)
    def list(self):
        return self._df("SELECT task_family, task_id, persist_key, generated_ts, status, "
                        "shape_rows, shape_cols FROM artifacts ORDER BY generated_ts DESC")
    def describe(self, task):
        tid = task.task_id if hasattr(task, 'task_id') else task
        df = self._df("SELECT * FROM artifacts WHERE task_id=:t ORDER BY generated_ts DESC", {'t': tid})
        return None if df.empty else df.iloc[0].to_dict()
    def history(self, task):
        tid = task.task_id if hasattr(task, 'task_id') else task
        return self._df("SELECT * FROM artifacts WHERE task_id=:t ORDER BY generated_ts DESC", {'t': tid})
    def lineage(self, task=None):
        if task is None: return self._df("SELECT * FROM lineage")
        tid = task.task_id if hasattr(task, 'task_id') else task
        return self._df("SELECT * FROM lineage WHERE downstream_task_id=:t", {'t': tid})
    def export(self, fmt='json', path=None):
        recs = self.list().to_dict(orient='records')          # extend to full descriptors as needed
        s = json.dumps(recs, default=str, indent=2)
        if path: open(path, 'w').write(s)
        return s

catalog = _Catalog()
```

### 8. `oryxflow/catalog.py` — enrichment seam (pluggable, out-of-band)

```python
def set_enricher(fn):
    """Register callable(evidence_dict)->{'description', 'field_semantics', 'model'}.
    Default None: enrich() is a no-op. The plugin agent / d6t service supplies this."""
    global _enricher; _enricher = fn

def enrich(limit=50):
    """Fill semantics for 'pending' artifacts, caching by (code_hash, schema_hash). Out-of-band."""
    if not _enabled: return 0
    if _enricher is None:
        logger.info("catalog.enrich: no enricher configured (set_enricher)"); return 0
    # SELECT pending rows; for each, reuse _cached_semantics or call _enricher(evidence);
    # UPDATE description/field_semantics/semantics_model/review_status. (Full body in impl.)
```

### 9. `oryxflow/__init__.py` + `setup.py` — wire up + dependency

- `__init__.py` after the logging import (line 4) add:
  ```python
  from oryxflow.catalog import enable_catalog, disable_catalog, catalog
  ```
- `setup.py`: add `extras_require={'catalog': ['sqlalchemy']}` (pandas already required). Keep
  `install_requires` unchanged so the base install stays light.

### 10. `oryxflow-mcp` — read-only catalog tools (cross-repo)

In `the oryxflow-mcp repo` (FastMCP server; inspect `mcp.py` / the `TaskService`
for the existing tool-registration pattern), add three read-only tools that import the user
project's `oryxflow`, enable the catalog against the project DB, and return JSON:

- `describe_output(task, module)` → `oryxflow.catalog.describe(...)` (full descriptor).
- `list_catalog()` → `oryxflow.catalog.list().to_dict(orient='records')`.
- `get_lineage(task, module)` → `oryxflow.catalog.lineage(...)` down to raw-source refs.

These are the agent's no-execution window into "what data exists, what it means, where it came
from." Match the existing subprocess/AST conventions in that repo.

### 11. plugin skill — teach the agent to consult + trust the catalog (cross-repo)

In `the oryxflow-claude-plugin repo, skills/oryxflow/SKILL.md` (+ `reference.md`):
add a short section directing the agent, before analyzing outputs, to consult the catalog
(MCP `describe_output`/`list_catalog`/`get_lineage`, the JSON export, or the regenerated
`docs/oryxflow-data.md`) and to **trust the field semantics** (definition/unit/period/segment/
data_type) to avoid reference errors. Note the agent may also act as the enricher
(`oryxflow.catalog.set_enricher`). Keep consistent with the skill's existing "read & trust the
data doc" idiom.

### 12. docs — `docs/source/catalog.rst`

User-facing page mirroring `docs/source/logging.rst`: what the catalog is, `enable_catalog()`,
the descriptor fields, the readers, and the MCP/agent integration. Cross-link from the docs
index.

### 13. tests — `tests/test_catalog.py`

loguru/sqlite, all additive (won't move the 73 baseline). Cover:
- **disabled by default**: run a small flow, assert no DB file / no rows.
- **enable + run**: `enable_catalog('sqlite:///<tmp>')`, run a 2-task DataFrame flow, assert an
  `artifacts` row per save with expected `columns`/`dtypes`/`shape_rows`/`profile`/`sample`,
  non-null `code_hash`, `params`, `generated_ts`, and `duration_s` stamped after completion.
- **lineage**: assert a `lineage` edge (downstream→upstream) for the dependent task.
- **versioning**: re-run after `reset` (or change a param) → a second `artifacts` row (history).
- **determinism cache**: two saves with identical code+schema both resolve the same
  `_cached_semantics` once semantics are present.
- **never breaks a run**: monkeypatch `_profile` to raise → run still succeeds, warning logged.
- **readers**: `catalog.list()/describe()/lineage()/export()` return expected shapes.

Use a temp dir for `set_dir` and a `sqlite:///` temp URL; `disable_catalog()` in teardown.

## Files modified

- `oryxflow/settings.py` — add `catalog_*` settings (step 1).
- `oryxflow/catalog.py` — **new**: engine, schema, capture API, run-context/lineage, readers,
  enrichment seam (steps 2–4, 7, 8).
- `oryxflow/tasks/__init__.py` — `save()` capture hook (step 5).
- `oryxflow/core.py` — `build()` run-context + duration + lineage hooks (step 6).
- `oryxflow/__init__.py` — re-export `enable_catalog`/`disable_catalog`/`catalog` (step 9).
- `setup.py` — `extras_require={'catalog': ['sqlalchemy']}` (step 9).
- `oryxflow-mcp/…` — new read-only catalog MCP tools (step 10, separate repo).
- `oryxflow-claude-plugin/skills/oryxflow/SKILL.md` (+ `reference.md`) — catalog guidance
  (step 11, separate repo).
- `docs/source/catalog.rst` (+ index) — user docs (step 12).
- `tests/test_catalog.py` — **new** test suite (step 13).
- `docs/todo/20260622-sys-catalog.md` — this plan (commit alongside the code).

## Verification

1. **Install the extra**: `pip install -e .[catalog]` (brings in `sqlalchemy`).
2. **Hold the baseline** — from repo root:
   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```
   Expect **73 passing** (benign `datatable`/sklearn warnings OK) — proves the disabled-default
   capture hooks don't regress anything.
3. **New suite**: `python -m pytest tests/test_catalog.py -q` → all green.
4. **End-to-end smoke** (manual):
   ```python
   import oryxflow, pandas as pd
   oryxflow.set_dir('data'); oryxflow.enable_catalog()
   class TaskRaw(oryxflow.tasks.TaskPqPandas):
       def run(self): self.save(pd.DataFrame({'id':[1,2],'ltv':[10.0,20.0]}))
   @oryxflow.requires(TaskRaw)
   class TaskScored(oryxflow.tasks.TaskPqPandas):
       """Scored customers."""
       def run(self):
           df = self.inputLoad(); df['score'] = df['ltv']*2; self.save(df)
   oryxflow.run(TaskScored)
   print(oryxflow.catalog.list())                 # rows for TaskRaw + TaskScored, newest first
   print(oryxflow.catalog.describe(TaskScored()))  # schema/profile/sample/provenance/code_hash
   print(oryxflow.catalog.lineage(TaskScored()))   # edge TaskScored -> TaskRaw
   ```
   Expect: a `data/.oryxflow-catalog.db` file; `list()` shows both tasks; `describe()` shows
   columns `['id','ltv','score']`, dtypes, a 2-row sample, non-null `code_hash`, `params`,
   `duration_s`; `lineage()` shows the dependency edge.
5. **No-execution agent read**: from `oryxflow-mcp`, call `list_catalog` / `describe_output`
   against the project and confirm JSON descriptors come back **without** running the flow.
6. **Disabled path**: in a fresh process *without* `enable_catalog()`, run the flow and confirm
   no DB file is created and runs behave exactly as before.

## When implemented

Per CLAUDE.md: leave this file in `docs/todo/` as the design record; if implementation diverges
(e.g. enricher wiring, `run_end` placement, JSON-on-sqlite quirks), append an
`## Implementation notes (divergences from the plan as built)` section; commit this plan in the
same commit as the code it describes.
