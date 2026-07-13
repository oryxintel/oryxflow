# Per-task (symbol-level) code hashing

## Context

Automatic code invalidation (`docs/todo/20260712-engine-code-invalidation-auto.md`) hashes at
**file** granularity: `codehash.module_hashes(task)` returns one digest per project-local file
reachable from the task's defining module via imports, and any change to any of those files
reruns the task. Field testing against a real project (84 tasks, one monolithic `tasks.py`,
12 parameterized flows) showed why that granularity is unacceptable:

> Editing one line in `SubmarketReturnBacktest.run()` recomputed every task defined in
> `tasks.py` — `ran=28`, all reason `code change (auto: tasks.py)`: the API loader
> (13 instances), plus every unrelated sibling. A cosmetic edit to a reporting task triggers a
> full re-fetch from the Benchmark API. Until this lands, auto-versioning is net-negative on a
> monolithic `tasks.py`.

This was **not a regression** — hashing was file-level from the first commit. The field agent's
round-1 run showed `ran=1` for the same edit only because the 27 sibling tasks had *no stored
records yet* (held back by the "output predates current code" mtime guard, warning instead of
rerunning). The round-2 `accept_code` fix stamped baselines for all of them, which armed the
file-level comparison across the whole module and unmasked the granularity flaw. It escaped the
test suite because no test ever asserted **sibling isolation**: every test module defines only
the task whose rerun is asserted.

Same field-test round surfaced two smaller defects, fixed here too:

- **Warning volume**: staleness warnings printed once per `task_id`, so 12 parameterized
  instances of one family printed 12 *identical* lines (the message names only the family).
  24 printed warnings for 2 families x 12 flows.
- **`accept_code` tree-walk fragility**: the instance walk aborts entirely when any node in the
  upstream tree raises (e.g. a `requires()` that needs inputs), so a bare `flow.accept_code()`
  can stall with part of the DAG never blessed while explicit per-task accepts work.

### Design decisions

0. **No library does this (researched 2026-07-12, second pass beyond the joblib/Dagster/
   findimports round).** The two workflow engines closest to oryxflow both document the
   transitive-helper gap as a known limitation rather than solving it: Hamilton's caching
   ignores docstrings/comments like us but "will not version nested function calls" (editing a
   utility silently keeps stale caches; workaround is manual RECOMPUTE), and redun "cannot hash
   the contents of a plain Python function" (helpers tracked via a manual `hash_includes` list
   — the `code_version`-era ergonomics this feature replaces). marimo's `persistent_cache` does
   transitive reference analysis but is welded to its notebook cell/dataflow runtime (and is a
   framework-sized dependency); Streamlit abandoned its bytecode-chasing `CodeHasher` in modern
   `st.cache_data`. Bytecode approaches are Python-version-fragile and none can express the
   oryxflow-specific normalization (the `code_version` pin line must be hash-neutral). Stdlib
   `symtable`/`inspect.getclosurevars` classify globals shadow-proof but drop the `mod.attr`
   chains needed for cross-module constant resolution, and AST is needed for the normalized
   digests regardless. In the code-change-detection category proper: pytest-testmon computes
   per-block AST checksums too (validating the hash unit) but maps dependencies via coverage.py
   at RUNTIME — rejected as an architecture because every task run would pay instrumentation
   overhead, dependencies are unknown until after a first run, and it's a pytest plugin, not a
   reusable API; static call-graph tools (PyCG — archived; Jarvis — research code) cover only
   the reference-closure half and are unmaintained. So this stays custom, and the symbol
   closure is the differentiator.
1. **Hash unit = top-level symbol, closure = actual references.** A task's code identity is the
   normalized AST digest of its own top-level `ClassDef` plus the transitive closure of the
   module-level symbols it references — functions, non-Task classes, constants — followed
   across project-local modules. Keys are `'<relpath>::<symbol>'`. Rejected: keeping file-level
   and telling users to split modules (contradicts the one-`tasks.py` scaffold guidance; the
   agent's report calls this out explicitly).
2. **Referenced Task subclasses are EXCLUDED from the closure** (except base classes, below).
   `@requires(TaskX)` / `self.clone(TaskX)` is dependency *wiring*: TaskX's output identity is
   already folded via `output_id`/`dep_state`, and including TaskX's body would break pin
   orthogonality (a pinned-unbumped edit to TaskX must NOT rerun downstream tasks that merely
   name it). Changing the wiring itself (TaskX -> TaskY) still reruns: the referencing class's
   own AST changes. Calling a *staticmethod/helper on* another Task class is a documented blind
   spot (inert, never a false rerun — same policy as data files/dynamic imports).
3. **Project-local base classes ARE included**, walked via `cls.__mro__` (handles aliased
   imports and dynamic bases), even when they are Task subclasses — inheritance is a code
   dependency, not wiring.
4. **Resolution is runtime-namespace-first, AST-fallback.** Names referenced by a symbol are
   looked up in the defining module's `vars(sys.modules[modname])` (modules are necessarily
   imported — the task class exists). Functions/classes resolve to their defining file via
   `__module__` (correct across re-exports); plain data values (constants, dicts) resolve via
   the module's own AST symbol table, then via its `from x import y` bindings. Unresolvable
   names (builtins, third-party, locals that shadow) are ignored — over-approximation of
   references is allowed (extra edges = at worst an extra rerun), silent *misses* only for the
   already-documented blind spots.
5. **Conservative buckets for what symbol analysis can't carve up:**
   - Module-level side-effect statements (top-level calls, `if`/`try` blocks, attribute
     assigns) hash into one `'<relpath>::<module>'` pseudo-symbol shared by every task whose
     closure touches that module. `if __name__ == '__main__':` blocks are excluded (never run
     on import).
   - Names bound *inside* compound statements (`if X: def f(): ...`) map to the whole compound
     statement's digest.
   - `from mod import *` of a project-local module, bare module references (module object
     passed around without attribute access), and symbols that can't be located in their
     defining file: whole-file digest under `'<relpath>::*'`.
   - A task class that is not a top-level `ClassDef` in its module (dynamically created,
     nested in a function — e.g. the functional API, test-local classes): fall back to the
     old file-level `module_hashes`. Worst case equals current behavior, never worse.
6. **`state.RECORD_V` bumps 2 -> 3.** Stored `source_hashes` change shape (file keys -> symbol
   keys); a v2 record must never mass-rerun a warehouse. The existing v-mismatch path already
   handles this: treated as unverifiable -> complete, silently re-stamped at grandfather trust
   with `output_id` preserved. No migration code needed.
7. **Reason strings name the changed symbol** (the agent's explicit ask):
   `code change (auto: tasks.py::SubmarketReturnBacktest)` — falls out of using the changed
   record keys, no extra plumbing.
8. **Printed-channel warning dedupe keys on the MESSAGE, not the task_id.** The message names
   only the task family, so parameterized instances produce identical text; suppress a message
   while any task's live `_code_warned` entry holds it, re-arm when those entries pop
   (rerun/accept) or the text changes. `RunResult.warnings` and the event stream still record
   every occurrence per task.
9. **`accept_code` instance walk is per-node fault-isolated**: each node's
   fingerprint/requires/output/restamp work is wrapped so one raising task skips that node
   (counted, reported in the printed summary) instead of aborting the walk.
10. `module_hashes` / `file_hash` stay (fallback path, package test); `task_hashes` becomes the
    consumer-facing map and `task_code_hash` builds on it.

## Implementation

1. **`oryxflow/codehash.py`** — rewrite module docstring (symbol-level); add:
   - `_is_main_guard(stmt)`, `_bound_names(stmt)` (names bound by def/class/Name-assigns inside
     a compound statement), `_collect_refs(node)` -> `{(rootname, attr_chain), ...}` (Attribute
     chains rooted at a `Name`, bare `Name` loads as `(name, ())`, attribute roots not
     double-counted as bare).
   - `_symbol_index(path)` cached by `(path, mtime_ns)`: parse + `_strip_docstrings` once,
     return `{'symbols': {name: digest}, 'refs': {name: frozenset(refs)}, 'imports':
     {local: (module, orig, level)}, 'stars': ((module, level), ...), 'sideeffect':
     digest|None}`. Digest = `md5(ast.dump(stmt))` of the normalized top-level statement;
     multi-target assigns share one digest; Assigns binding no plain Name go to the
     side-effect bucket.
   - `task_hashes(task_or_cls)`: worklist closure over `(file, symbol, modname)` seeded with
     the class's own `ClassDef` + MRO-local bases; resolves refs per decision 4 (Task-subclass
     skip per decision 2, module attribute chains walked while modules); emits
     `rel::symbol` / `rel::<module>` / `rel::*` keys; falls back to `module_hashes(cls)` when
     the class isn't a top-level ClassDef. Result cached per
     `(modname, clsname, root)` with the same mtime-revalidation + `freeze()` generation
     scheme as `_module_cache`.
   - `current_hash_for_key(root, key)`: recompute one stored key's current digest —
     `rel::*`/legacy bare `rel` -> `file_hash`, `rel::<module>` -> side-effect digest,
     `rel::sym` -> symbol digest (None when the symbol vanished).
2. **`oryxflow/core.py`** — `_hashes()` -> `task_hashes`; `_code_fingerprint` comment;
   `_mtime_guard_trips` derives file rels via `key.partition('::')[0]`; `_warn_code` fresh test
   becomes `msg not in _code_warned.values()`; `StalenessWarning` docstring updated.
3. **`oryxflow/tasks/__init__.py`** — both `_own_code_ok` comparisons -> `codehash.task_hashes`.
4. **`oryxflow/state.py`** — `RECORD_V = 3` (+ comment: v3 = symbol-level source_hashes).
5. **`oryxflow/__init__.py` `accept_code`** — instance walk: `task_hashes`, per-node
   try/except with `skipped` count surfaced in the printed summary; class form:
   `task_hashes(cls)`; bulk form: `codehash.current_hash_for_key` instead of `file_hash`.
6. **`tests/test_code_invalidation.py`** — rebaseline
   `test_auto_rerun_and_reason` (`'code change (auto: codemod_auto1.py::TaskAuto)'`); new
   `TestPerTaskGranularity`: sibling isolation, same-module helper closure, cross-module
   constant closure (existing `test_auto_helper_transitive` covers via `::FACTOR` key),
   pinned-upstream edit does not ripple to a referencing downstream, v2 record migrates
   silently to v3 symbol keys with no rerun, one printed warning across parameterized
   instances (family-level dedupe), accept walk survives a raising `requires()`.
7. **Docs** — `docs/source/managing-workflows.rst` granularity description; `CHANGELOG.md`
   26.7.12 bullets updated in place (version unpublished). Plugin repo: `SKILL.md`,
   `reference.md`, `docs/CHANGELOG.md` (granularity claims + field-report gotchas 2-5:
   `flow.accept_code()` is the path for parameterized flows, accepting cascades upstream,
   post-upgrade warning wave -> bless with `flow.accept_code()`).

## Files modified

- `oryxflow/codehash.py` — symbol index, reference closure, `task_hashes`,
  `current_hash_for_key`; docstring.
- `oryxflow/core.py` — consume `task_hashes`; message-level warning dedupe; mtime guard key
  parsing.
- `oryxflow/tasks/__init__.py` — `_own_code_ok` comparisons.
- `oryxflow/state.py` — `RECORD_V = 3`.
- `oryxflow/__init__.py` — `accept_code` symbol hashes, fault-isolated walk, bulk key
  recompute.
- `tests/test_code_invalidation.py` — rebaselines + `TestPerTaskGranularity`.
- `CHANGELOG.md`, `docs/source/managing-workflows.rst` — granularity + dedupe wording.
- Plugin repo (not committed from here): `skills/oryxflow/SKILL.md`,
  `skills/oryxflow/reference.md`, `docs/CHANGELOG.md`.

## Verification

- `python -m pytest tests/test_code_invalidation.py -q` — all pass including the new
  granularity class.
- Full baseline: `python -m pytest tests/test_main.py tests/test_workflow.py
  tests/test_workflowMulti.py tests/test_workflowMulti2.py tests/test_code_invalidation.py -q`
  (73 legacy + code-invalidation suite, no regressions).
- Cross-process smoke replaying the field test: module with two independent tasks + one
  helper; run; edit one task's `run()` in another process -> only that task reruns, reason
  `code change (auto: tasks.py::<TaskName>)`; edit the helper -> only the referencing task
  reruns; v2-shaped record on disk -> first run silently re-stamps, `ran == []`.
- `sphinx-build -b html docs/source <tmp>` exits 0.

## Implementation notes (divergences from the plan as built)

1. **Function-local (lazy) imports were a new blind spot** the plan missed: the old file-level
   walk scanned the whole AST for imports, but symbol resolution goes through the module runtime
   namespace, where `import lazyhelper` inside `run()` never appears. Fixed in `_symbol_index`:
   nested Import/ImportFrom statements anywhere in a symbol's body merge into the file's import
   bindings (top-level bindings win), and the resolution fallback handles plain-`import`
   bindings by narrowing to the attribute chain used (`lazyhelper.py::X`), whole-file when used
   bare. Covered by extending `test_package_reexport_and_lazy_import_walked` to symbol keys.
2. **Re-exports reached via the AST fallback** (`from mypkg import helper` where `__init__.py`
   re-exports from `impl.py`, and the runtime namespace can't resolve — e.g. lazy) follow the
   `__init__.py` import binding to the defining file instead of degrading to a whole-file hash
   of `__init__.py` (which would have MISSED edits to `impl.py` — the one non-conservative
   direction).
3. **`accept_code` fault isolation is per-ingredient, not per-node**: `_code_fingerprint` folds
   deps recursively, so one broken `requires()` poisons the fingerprint of everything downstream
   of it — skipping those nodes would have made the anchor itself unblessable. Instead
   fingerprint, `requires()` and `_dep_state()` each degrade independently (the record keeps its
   stored values for the secondary fields) and only a failing re-stamp itself lands in the
   reported `skipped` list.
4. **Invalidation policy extracted to `oryxflow/codecheck.py`** (user-requested modularization,
   same session): `StalenessWarning` + `_code_warned`, `make_record`/`code_state`/`hashes_of`/
   `mtime_guard_trips`, the per-build `Advisor` (advisory sweep + rerun reasons, previously
   closures inside `core.build()`), and `accept_code` (previously in `__init__.py`). `core` and
   `__init__` re-export the public names, so `oryxflow.core.StalenessWarning`,
   `oryxflow.core._code_warned` (same dict object) and `oryxflow.accept_code` stay valid.
5. Test rebaselines the plan predicted, as landed: the auto-rerun reason is now
   `code change (auto: codemod_auto1.py::FACTOR)` (the edit in that fixture moves the module
   constant, not the class), and `test_auto_accept_code_upstream`'s "equivalent refactor" had to
   actually touch the referenced symbol's statement (`FACTOR = 2 - 1`) — its old edit (comment +
   unused constant) no longer invalidates anything, which is the feature working.
6. **Post-review hardening pass** (same session, code-review round): (a) *rebound top-level
   names* — `_symbol_index` kept only the FIRST binding statement's digest per name
   (`setdefault`), so editing a later rebind (`helper = cache(helper)`, `X = 1` then `X = 2`)
   was a silent false-green; every binding statement now folds into the symbol's digest,
   mirroring the refs union (test `test_rebound_symbol_edit_detected`). (b) *root consistency*
   — `mtime_guard_trips` derived the project root from cwd while the stored keys are relative
   to the task module's root; new `codehash.root_for(task)` (via the extracted
   `_class_source(cls)` preamble shared by `module_hashes`/`task_hashes`) fixes it. The bulk
   `accept_code()` root stays cwd-based (records don't carry their root), but stored keys that
   no longer resolve are now counted and reported ("cannot re-key those; accept with a task
   instance") instead of silently reading as verified. (c) *cache hygiene* — the per-file
   caches (`_hash_cache`, `_imports_cache`, `_symindex_cache`) are re-keyed by path with the
   mtime inside the value, so edit-heavy notebook sessions replace entries instead of leaking
   one per save; the two multi-file walk caches share extracted `_walk_cache_get`/
   `_walk_cache_put` helpers (freeze fast-path + mtime revalidation in one place). (d)
   *advisory robustness* — `Advisor.warn` survives an app-level `error` warning filter
   (`-W error` must not abort a build; test `test_warn_survives_error_filter`), `hashes_of`
   logs its swallowed exception at DEBUG, and `_restamp` counts a task as accepted immediately
   after the record write so a reporting-channel failure can't misfile it as skipped. (e)
   `_MISSING` moved to the module top; `_collect_refs` made iterative (explicit stack) so
   pathological expression nesting can't hit the recursion limit. Baseline moved 124 -> 127.
