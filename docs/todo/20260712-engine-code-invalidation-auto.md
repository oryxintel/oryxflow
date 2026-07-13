# Auto code invalidation (AST hash drives reruns; `code_version` becomes the opt-in)

Follow-up to `20260712-engine-code-invalidation.md` (shipped in 26.7.12). Read that plan's
Context and its Implementation-notes addendum first — this plan reuses its machinery
(`codehash.py`, `state.py`, the fingerprint/record/advisory seams in `build()`) and changes
only who supplies a task's code-identity token.

## Context

The shipped design makes correctness depend on a manual ritual: every task whose logic you
want tracked must declare `code_version` and the author must remember to bump it on every
logic change. The safety net (AST-hash advisory warning) catches the forgetting, but the
primary action is still an act of memory — and for a human writing tasks, "every class
definition now has to include a `code_version` attribute" is exactly the kind of ceremony
the library is supposed to remove. User verdict (2026-07-12): *"pretty annoying… probably ok
for AI agents but annoying for humans."*

All the pieces for the fix already shipped: `codehash.module_hashes()` computes
AST-normalized (comments/docstrings/formatting-invariant), file-level, transitive hashes
over repo-local imports; the record store compares a stored fingerprint against the current
one in `complete()`; `accept_code()` exists as the "output-equivalent, don't recompute"
escape hatch. v4 deliberately kept the hash **advisory-only**; this plan promotes it to the
**default authority**:

- **Auto mode (default on):** a task with no `code_version` gets its code-identity token
  from the AST hash of its defining module and everything that module transitively imports
  inside the repo. Real logic change (in the task *or* a helper it imports) → the task and
  everything downstream rerun on the next `run()`, automatically. Comment/docstring/
  formatting edits still change nothing (normalization already guarantees that).
- **`code_version` becomes opt-in, and where present it stays the authority** *(user-
  confirmed)*: a task that declares `code_version` keeps today's exact semantics for its own
  logic — reruns only on an explicit bump; code edits without a bump produce the advisory
  warning, not a rerun. Delete the attribute → auto resumes for that task. This is the
  per-task opt-out for expensive tasks where a refactor-triggered recompute is intolerable,
  and the robustness opt-in for logic the hash can't see (dynamic dispatch, data-driven
  behavior).
- **Skips must stay observable** *(user requirement)*: auto has blind spots (data files,
  installed packages, code not reachable via repo-local imports, notebook-defined tasks).
  The contract is: after an edit, the run's story says what reran and why
  (`result.ran`/`result.reasons`, `task_ran.reason`, `events.print_status()`); an agent that
  edited code and sees the edited band **not** rerun must treat that as "auto didn't see my
  change" — check where the change actually lives, then either `reset()` or add an explicit
  `code_version` to that task. This rule ships in the skill, the docs decision table, and
  the CLAUDE.md snippet.

### Design decisions

1. **Auto is on by default** (`settings.code_version_auto = True`). The point of this
   revision is that correctness must not depend on memory *or* on per-class ceremony; an
   opt-in flag nobody sets is the same as the ritual attribute. Safe because adoption is
   invisible until a real edit: grandfathering (output exists, no record → stamp current as
   baseline) already ships, so upgrading never invalidates an existing cache — the first
   *subsequent* logic edit is what triggers a rerun, which is the correct behavior. Global
   opt-out is one line (`settings.code_version_auto = False` → byte-for-byte 26.7.12
   behavior). *(Rejected: per-class `code_version = 'auto'` marker — it re-introduces the
   per-class attribute the plan exists to remove; a settings flag plus the explicit-token
   opt-out covers both directions.)*

2. **The auto token is the transitive module hash, not just the task's own source.** The
   user accepted task-only as a floor ("even just checking the task code itself is good"),
   but `module_hashes()` already walks repo-local imports with per-file mtime-revalidated
   caching, and v4's own analysis says run() bodies are thin orchestration over helpers —
   task-only hashing would miss most real edits. Coarseness cost (editing `utils.py` reruns
   every task importing it) is accepted: that is what "auto" means, `preview()` shows the
   pending band before you commit to the compute, and `accept_code()` skips it when
   output-equivalent. *(Rejected: symbol-level closure hashing — can silently miss
   dynamically-referenced helpers, the one failure mode that must not exist; v4 decision 5
   stands.)*

3. **Auto plugs into the existing fingerprint; dep folding stays.** `_code_fingerprint`'s
   "own token" becomes: explicit `code_version` if set, else (auto on) `'auto:' +
   md5(sorted module_hashes items)`, else None. Everything downstream of the token —
   recursive folding, record comparison in `_code_ok()`, `keep_versions` pathing for
   explicit versions, the advisory sweep — is unchanged machinery. *(Rejected: dropping the
   folded dep fingerprint and relying on the `check_dependencies` cascade — the cascade only
   propagates while the upstream is incomplete in the same build; run A alone in build 1,
   then B in build 2, and the cascade sees A complete and skips B against stale input. The
   stored folded fingerprint is what captures "the state of the world when B last ran"
   across builds. Same reasoning kills a live-recursive `_code_ok` without a stored folded
   value.)*

4. **Precedence is per-task and self-healing** *(user-confirmed)*: `code_version` present →
   explicit token governs that task's own logic (bump to rerun; edits without bump → the
   existing advisory warning). `code_version` removed → the auto token takes over on the
   next fingerprint computation; the resulting fingerprint mismatch reruns the task once
   (correct: the system can no longer vouch that the cached output matches current code) and
   the new record baselines auto. Note the composition: an explicit-version task whose
   *upstream* is auto still reruns on upstream code changes via dep folding (`upstream
   rerun`) — the explicit token only pins the task's *own* logic.

5. **Two silent re-stamp (never mass-rerun) migration paths**, both at the trust level of
   grandfathering:
   - **Record schema version.** Turning auto on changes the fingerprint formula for every
     previously-unversioned task that already has a record (unversioned tasks inside a
     versioned subtree got records in 26.7.12). Without migration, upgrading the library
     would rerun those tasks once for no reason. So records gain `'v': RECORD_V` (= 2);
     `_code_ok()` treats a record with a different/missing `v` as unverifiable → complete;
     the advisory sweep re-stamps it with the current fingerprint/hashes/`v`. One-time
     re-baseline per record; a real pending edit made *across* the upgrade is masked once —
     same accepted trust level as grandfathering, documented.
   - **Python interpreter version.** `ast.dump` output changes across minors, so with auto
     on a Python upgrade would otherwise rerun *everything*. The shipped `py: PY_TAG` stamp
     already exists for the advisory; extend it to completeness: `rec['py'] != PY_TAG` →
     `_code_ok()` returns True and the sweep re-stamps.

6. **`accept_code()` must propagate in auto mode, so the instance form walks upstream.**
   In auto, accepting a change means re-stamping *fingerprints*, not just source hashes —
   and because fingerprints fold deps, accepting an edited helper must re-stamp every
   record in the affected band, which cannot be reconstructed from records alone (no
   instances, no dep graph). So: `accept_code(task_instance)` re-stamps that task **and its
   entire upstream dep tree** (each existing record → current fingerprint + hashes), the
   exact analogue of `reset_upstream` ergonomics — call it on the flow's anchor/default
   task. `Workflow.accept_code()` / `WorkflowMulti.accept_code(flow=...)` are thin wrappers
   doing `oryxflow.accept_code(self.get_task(...))`. The class form and bare `accept_code()`
   keep their current hash-restamp behavior but the docs mark the instance/Workflow form as
   the one to use under auto (the others can leave dep-folded mismatches behind, which then
   rerun — safe direction: never silently blesses, at worst recomputes).

7. **Blind-spot honesty carries over unchanged.** Where `module_hashes()` returns `{}` (task
   defined in a REPL/notebook kernel file outside the project root, or in site-packages),
   the auto token is None and the feature degrades to exactly today's behavior for that
   task — no false rerun, no false green claim. Never warn-spam about it (DEBUG log only);
   the docs list the blind spots and the agent rule (Context, third bullet) is the net.

8. **Reasons name the change.** A rerun caused by the auto token reports
   `code change (auto: utils.py, tasks.py)` (changed files, capped at 3 + `…`), computed by
   diffing the record's stored `source_hashes` against current. Keeps the field-validated
   verification pattern working: after an edit, the edited band must appear in
   `result.ran`/`events.runs()` with a `code change (auto: …)` reason; `ran=0` after an
   edit means blind spot.

## Implementation

**1. `settings.py` — the flag.** After `state_filename`:
   ```python
   # auto code invalidation: tasks without an explicit code_version derive their code
   # identity from the AST hash of their module + transitively imported repo-local files,
   # so logic edits rerun automatically. False -> only explicit code_version drives reruns
   # (pre-26.7.x behavior); a task-level code_version always overrides auto for that task.
   code_version_auto = True
   ```

**2. `codehash.py` — `task_code_hash(task_or_cls)`.** Digest of the transitive hash set:
   ```python
   def task_code_hash(task_or_cls):
       """Single md5 over the module_hashes set; None when nothing is hashable
       (module not project-local) so auto degrades to inert, never false-green."""
       hashes = module_hashes(task_or_cls)
       if not hashes:
           return None
       blob = '|'.join('{}={}'.format(k, hashes[k]) for k in sorted(hashes))
       return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]
   ```
   No extra caching needed: `module_hashes` already carries the mtime-revalidated full-walk
   cache; the md5-over-dict on top is trivial.

**3. `core.py:_code_fingerprint` — auto own-token.** Replace the body's token logic
   (keep the no-memoization stance and update the docstring: the auto hash now *gates*
   completeness for unversioned tasks; the warn-only advisory remains only for tasks with
   an explicit `code_version`):
   ```python
   dep_fps = [d._code_fingerprint for d in self.deps()]
   own = self.code_version
   if own is None:
       from oryxflow import settings, codehash
       if settings.code_version_auto:
           h = codehash.task_code_hash(self)
           own = 'auto:{}'.format(h) if h is not None else None
   if own is None and all(f is None for f in dep_fps):
       return None
   parts = [self.task_family, str(own)] + sorted(f or '' for f in dep_fps)
   return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()[:16]
   ```
   (Lazy imports match the existing cycle pattern.)

**4. `state.py` — record schema version.** Module constant `RECORD_V = 2`. Every
   `put_record` call site stamps `'v': state.RECORD_V` (there are four: the two in
   `_advise`, the post-run stamp in `_process`, and `accept_code`'s `_restamp` — grep
   `put_record` to confirm none are missed).

**5. `tasks/__init__.py:_code_ok` — unverifiable records pass.** Between the `rec is None`
   check and the fingerprint comparison:
   ```python
   if rec.get('v') != state.RECORD_V or rec.get('py') != codehash.PY_TAG:
       return True   # formula/interpreter changed -> not comparable; sweep re-stamps
   ```

**6. `core.py:_advise` — re-stamp the unverifiable, keep the advisory scoped to explicit
   versions.**
   a. New first branch when a record exists: `rec.get('v') != RECORD_V or rec.get('py') !=
      PY_TAG` → silent `put_record` with current fingerprint/hashes/`py`/`v` (the migration
      path; the existing py-only re-stamp branch folds into this one).
   b. The staleness-warning branch (`fingerprint matches, stored hashes differ`) only ever
      fires for tasks whose own token is an explicit `code_version` — under auto a hash
      change moves the fingerprint itself, so the task lands in the rerun path, never the
      warn path. No code change needed beyond the docstring; add a comment stating the
      invariant so nobody "fixes" it later.
   c. Grandfathering and the mtime guard are unchanged and now apply to every task (auto
      makes fingerprints non-None broadly), which is exactly the intended on-ramp.

**7. `core.py:_reason_for` — auto reason.** In the record-exists branch, before the
   fingerprint-mismatch fallthrough:
   ```python
   if rec.get('code_version') != task.code_version:
       return 'code change ({} -> {})'.format(rec.get('code_version'),
                                              task.code_version if task.code_version is not None else 'auto')
   if rec.get('fingerprint') != fp:
       stored, current = rec.get('source_hashes') or {}, _hashes(task)
       changed = sorted(k for k in set(stored) | set(current)
                        if stored.get(k) != current.get(k))
       if changed:
           shown = ', '.join(changed[:3]) + ('...' if len(changed) > 3 else '')
           return 'code change (auto: {})'.format(shown)
       return 'upstream rerun'
   ```
   Add `'auto': task.code_version is None and _settings.code_version_auto` to the
   `task_ran` payload (additive; readers tolerate unknown fields).

**8. `__init__.py:accept_code` — upstream walk for instances + Workflow wrappers.**
   - Instance form: instead of restamping only the task's own record, walk
     `task` + `flatten(task.requires())` recursively (dedupe by task_id, prune where
     `_code_fingerprint is None`); for each visited task whose record exists, re-stamp with
     **current fingerprint** (`t._code_fingerprint`), current `codehash.module_hashes(t)`,
     `py`, `v`, `ts`; emit `code_accepted` per re-stamped record. Docstring: under auto,
     call it on the most-downstream task you consider equivalent (typically the flow's
     default task) — it accepts the whole upstream band.
   - Class form and bare `accept_code()`: unchanged behavior, docstring notes they don't
     fix dep-folded fingerprints (leftover mismatches just recompute — safe direction).
   - `Workflow.accept_code(task=None)` → `oryxflow.accept_code(self.get_task(task))`;
     `WorkflowMulti.accept_code(task=None, flow=None)` follows the existing flow-selector
     pattern. Both return the accepted task_id list.

**9. Docs (`docs/source/managing-workflows.rst`) + CLAUDE.md snippet.**
   - Rewrite the code-invalidation intro: auto is the default ("edit code → affected tasks
     rerun; comments/formatting never do"); `code_version` is the opt-in override for (a)
     expensive tasks where refactor-driven recompute must be a deliberate bump, (b) logic
     the hash can't see; present-wins/removed-resumes precedence; `settings
     .code_version_auto = False` for the old behavior.
   - Decision table: change the "logic changed" row to "nothing — auto reruns it (bump
     `code_version` only on tasks that declare one)"; add rows: *"edited code but the run
     skipped it (`ran=0`)"* → blind spot — `reset()` or add explicit `code_version` to that
     task; *"refactor is output-equivalent, recompute too expensive"* →
     `accept_code(anchor_task)` / `flow.accept_code()` before running.
   - Blind-spot list (data files, installed packages, dynamic dispatch, notebook-defined
     tasks) + the surprise-recompute mitigation: `preview()` after edits shows the pending
     band before any compute.
   - Note `keep_versions` still keys off explicit `code_version` only (auto overwrites in
     place); note the functional API now gets code invalidation for free (ambient auto —
     closes the "no functional surface" limitation in the v4 plan).
   - Update the copy-paste CLAUDE.md snippet with the revised agent rules (below).
   - `CHANGELOG.md` entry (behavior change, migration note: first run re-stamps records,
     no reruns); calver bump on release.

**10. Plugin skill (cross-repo: `oryxflow-claude-plugin/skills/oryxflow/SKILL.md` +
   `reference.md`, gated on the new oryxflow version).** Revised agent rules:
   - Editing task/helper logic needs **no action** — auto reruns the affected band. Bump
     `code_version` only on tasks that declare one (it stays the authority there).
   - **Verify the rerun happened** (the user's core requirement): after an edit, the next
     run must show the edited band in `result.ran` / `events.runs()` with reason
     `code change (auto: <files>)`. If it *didn't* rerun (`ran=0`), auto did not see the
     change — find where the change actually lives (data file? installed package? dynamic
     call?), then `reset()` the affected task or give it an explicit `code_version`.
   - Before a run after touching a shared helper, `preview()` to see the recompute band;
     if the change is provably output-equivalent, `flow.accept_code()` /
     `accept_code(anchor)` instead of eating the recompute — only when certain; when
     unsure, let it rerun.
   - Keep: session-start `events.print_status()`, the three exits, the two-runs diff
     recipe, `self.logger` scalars.

## Files modified

- `oryxflow/settings.py` — `code_version_auto = True`.
- `oryxflow/codehash.py` — `task_code_hash()`.
- `oryxflow/core.py` — `_code_fingerprint` auto token; `_advise` unverifiable-record
  re-stamp branch (+ invariant comment); `_reason_for` auto reason; `task_ran` payload
  `auto` field; docstring updates.
- `oryxflow/state.py` — `RECORD_V = 2`.
- `oryxflow/tasks/__init__.py` — `_code_ok` v/py bypass; docstring.
- `oryxflow/__init__.py` — `accept_code` instance-form upstream walk + docstrings;
  `Workflow.accept_code` (`__init__.py:443`) / `WorkflowMulti.accept_code`
  (`__init__.py:663`) wrappers.
- `docs/source/managing-workflows.rst`, `CHANGELOG.md` — docs (decision-table rows, blind
  spots, CLAUDE.md snippet, migration note).
- `oryxflow-claude-plugin/skills/oryxflow/SKILL.md` + `reference.md` + plugin changelog —
  agent rules (separate repo).
- `tests/test_code_invalidation.py` — new auto-mode test class (below).

NOT here: symbol-level hashing (annotation-only, later), notebook-source hashing, data-file
content hashing on loader tasks (still the strongest known-limitation candidate; separate
plan), functional-API-specific surface (auto covers it ambiently).

## Verification

1. **Baseline holds with auto ON (the important gate).** Full suite:
   ```bash
   python -m pytest tests/ -q
   ```
   **117 passing** (current baseline), ids/paths byte-identical, runtime not materially
   worse (~5–6s; `module_hashes` is mtime-cached — watch for stat-storms via the
   `complete()` cascade). Existing tests never edit source files mid-run, so auto must
   cause zero behavior change there; new `.oryxflow-code-status.json` files appearing in
   test data dirs are expected — fix any test that asserts exact dir listings, or point
   the store away in `conftest`. Runtime class redefinition (common in tests) does not
   change the module file → must not trigger auto reruns (add an explicit test).

2. **New tests (`tests/test_code_invalidation.py`, auto section)** — write task modules to
   `tmp_path`, set `codehash.PROJECT_ROOT`, import as real modules; clear
   `codehash` caches and `state.clear_cache()` between phases:
   - *Auto rerun*: run → complete; rewrite the task module with changed logic (bump mtime)
     → rerun at the same path, reason `code change (auto: <file>)`.
   - *Cosmetic silence*: comment/docstring/formatting-only rewrite → no rerun, no warning.
   - *Helper transitivity*: task imports `helpers.py`; edit only `helpers.py` → task
     reruns; a second task in the same project **not** importing it stays complete.
   - *Downstream propagation, cross-build*: A→B; edit A's module; build A alone (stamps);
     fresh build of B → B reruns (`upstream rerun`) — proves folding, not cascade.
   - *Precedence*: task with `code_version='1'` → logic edit → **no rerun**, advisory
     warning fires (today's behavior); bump → rerun `code change (1 -> 2)`. Then remove
     the attribute → one rerun (`code change (1 -> auto)` via the version-diff branch),
     next build silent.
   - *Runtime redefinition inert*: redefine the class in-process without touching the file
     → no rerun.
   - *accept_code upstream walk*: A→B, edit shared helper (both mismatch) →
     `accept_code(B_instance)` → next build zero reruns; records re-stamped;
     `code_accepted` events for both. `Workflow.accept_code()` same via wrapper.
   - *Migration*: seed a v1-style record (no `'v'`, correct outputs) → build → no rerun,
     record silently re-stamped with `v=2`. Same for a wrong `py` tag.
   - *Blind-spot degrade*: task whose module resolves outside `PROJECT_ROOT` →
     fingerprint None (auto inert), behaves exactly as pre-auto.
   - *Flag off*: `settings.code_version_auto = False` → logic edit → no rerun, no record
     requirement beyond 26.7.12 behavior.
   - *`ran` visibility*: after the auto rerun, `result.reasons` and `events.runs()` carry
     the `code change (auto: …)` reason; `task_ran` payload has `auto: true`.

3. **End-to-end smoke.** README model-comparison flow in a scratch project: run (`3 ran`)
   → run (`3 complete`) → edit a comment (silent) → edit middle-task logic **without any
   code_version** → run → middle + downstream rerun with `code change (auto: …)` /
   `upstream rerun` in `events.runs()` → `preview()` after a helper edit shows the pending
   band → `flow.accept_code()` → run silent. Then add `code_version='1'` to the middle
   task, edit its logic → warning (not rerun) → bump → rerun. Confirms both modes and the
   precedence hand-off.

## Implementation notes (divergences from the plan as built)

Shipped folded into the 26.7.12 release entry (that version was unpublished, so the
explicit-only design and this plan ship as ONE release — no migration story between them;
comparative "previously/now" framing was removed from docs and changelog accordingly).
Final suite: **129 passing** (117 baseline + 12 new). Divergences, in decreasing
significance:

1. **Mode-aware, two-dimensional records replaced the plan's single-fingerprint
   comparison** (user-directed, from a project-agent design review mid-implementation).
   The plan's design rerun a task on *every* pin toggle ("one hand-off rerun" on removal,
   `code change (1 -> auto)` on add) and rippled the toggle downstream through the folded
   fingerprints. As built, every record stores both dimensions — the `code_version` token
   and the `source_hashes` as of the last materialization — and `TaskData._own_code_ok`
   compares the dimension matching the current mode: pinned → token equality; auto
   (including resume-from-pin) → stored-vs-current hashes. Consequences: pin/unpin on
   unchanged code is free ("just resumes"); an edit masked during a pinned-unbumped
   window reruns the moment the pin comes off; pinning in the same edit as a logic change
   reruns instead of blessing stale output (closing the plan's decision-5 first-add trap
   for any task that already has a record — the mtime guard remains only for records that
   don't exist yet).
2. **Dependency propagation folds output identity, not live fingerprints.** Records gain
   `output_id` (fresh per actual materialization; preserved by every re-stamp, migration,
   and `accept_code`) and `dep_state` (md5 over the direct deps' record output_ids);
   `_code_ok`'s dep dimension compares `dep_state`. Downstream reruns exactly when an
   upstream rematerialized: toggles/accepts never ripple, and a `reset()`+rerun upstream
   now propagates downstream across separate builds (previously same-build cascade only —
   a real hole this closes). Trade-off, documented: with `check_dependencies=False` a
   code-change upstream is no longer discovered mid-build via the live fold; propagation
   then lands on the build after the upstream actually reruns. The live recursive
   `_code_fingerprint` is retained for tracked-detection (`None` pruning) and events, and
   `_advise` runs **post-order** (deps first) so converge-re-stamped dep records exist
   before a task folds their output_ids — which also makes the v1→v2 record migration
   converge in one sweep with zero reruns.
3. **`_advise` gained a converge branch**: a complete task whose record disagrees with the
   current fingerprint/token (pure mode flip, schema/`py` migration) is silently
   re-stamped at grandfather trust level, `output_id` preserved. One `_make_record` helper
   is the single record shape for all four stamp sites.
4. **Per-build hash-revalidation freeze** (`codehash.freeze()`/`unfreeze()`, generation-
   counted): `module_hashes` mtime-revalidates each module's walk at most once per build.
   Without it the per-`complete()` stat storm cost ~45% suite runtime; with it the
   overhead is ~1s on the 86-test core suite.
5. **Test-plan adjustments**: `test_propagation_chain`'s "first-time pin → rerun"
   expectation inverted (free opt-in is now asserted); the three v4 tests whose premise
   was "no code_version → feature inert" (`test_transparency`, `test_grandfathering`,
   `test_grandfather_mtime_guard`) run with `code_version_auto=False` as the
   explicit-mode contract; a mode-flip/no-ripple test was added beyond the plan's list.
6. **Step 10 (plugin skill) was largely a no-op**: the skill had already been rewritten
   auto-first in a prior plugin-repo session; only the toggle semantics ("one hand-off
   rerun" → free toggles) and the `accept_code` class-form caveat needed updating.
7. **Expensive-recompute guard added** (from a second project-agent review of the built
   feature): auto is destructive-by-default (a rerun overwrites the old output) and an
   output-equivalent refactor of a shared helper could silently burn a 40-minute run.
   Records now stamp `duration_s`; an auto task whose last materialization exceeded
   `settings.code_version_auto_expensive_s` (default 600) is held complete on a code
   change (`_own_code_ok`) and `_advise` fires a dedicated warning naming the exits
   (reset / accept_code / pin) — "auto-pin by cost", reusing the pin-warning machinery.
   The same review's other asks: **`code_version` polarity rename** (pin reads like
   opt-in-to-safety) was *rejected* — a wrongly-added pin does not silently drop
   protection, it converts auto-rerun into the loud three-channel StalenessWarning, and
   the token's value semantics (`keep_versions` paths, diffable bumps) fit the name;
   **blind-spot advisory** ("module mtime moved but fingerprint didn't → warn") was
   *rejected* because that predicate is exactly a cosmetic edit — it would resurrect the
   false-alarm class the AST normalization exists to kill; the honest fix for data-file
   blind spots remains loader content-hashing (known-limitations follow-up). Adopted from
   the same review: CRLF-determinism and import-walk-completeness tests
   (`TestCodehash`: `__init__` re-exports, function-local imports), and the scaffold
   `cfg.py` shipping the auto knobs as commented lines (plugin repo).
8. **Field-test round (project-agent test on a real backtest flow, post-ship)** — four
   defects found and fixed:
   - *Opt-in cost a recompute* (`code change (auto -> 1)` on an "edit-free" pin add): the
     opt-in is itself a file edit, so the stored hashes could never match. Fixed at the
     root: `codehash._strip_docstrings` also strips class-body `code_version` assignments
     (plain and annotated) from the AST, making the pin line hash-neutral — add/remove/
     bump is purely a token change. Cross-process verified: pin add → cached, bump →
     `code change (1 -> 2)`, pin drop → cached.
   - *`accept_code` couldn't clear the `output predates current code` warning it names*:
     that state is exactly "output exists, record missing", but the instance walk skipped
     record-less tasks and the class/bulk forms iterate existing records — all three were
     structural no-ops there. The instance walk now stamps a fresh baseline record
     (new `output_id`) when outputs exist and no record does; the warning text now points
     at the instance/flow form (the class form can't create records, documented).
   - *Warning flood* (416 warnings for 84 tasks; one build per flow re-advises shared
     upstreams): the printed/logged channels (`warnings.warn` + loguru) now dedupe per
     process on `(task_id, message)` via `core._code_warned`, re-armed when the message
     changes or the task reruns / is accepted. `RunResult.warnings` and the
     `code_warning` event stream still record every occurrence (the durable/query
     channels are not deduped).
   - *Silent accept*: `accept_code` prints a one-line summary (re-stamped ids, or
     "nothing accepted" with a pointer to the instance/flow form) — visible without
     `enable_logging()`, like the execution summary.
   Regression tests: `test_auto_opt_in_pin_line_is_free`,
   `test_accept_code_clears_predates_guard`, `test_accept_code_empty_reports`,
   `test_warning_dedupe_per_process`, `TestCodehash::test_pin_line_hash_neutral`.

9. **Field-test round 3 (same day): file-level hashing replaced by symbol-level.** The round-2
   `accept_code` fix armed the file-level comparison across a whole monolithic `tasks.py` and
   exposed that editing one task recomputed all 28 tasks in the module (including API loaders).
   Superseded by `docs/todo/20260712-engine-code-hash-per-task.md`: `codehash.task_hashes`
   (per-symbol reference closure) replaces `module_hashes` as the hash consumers' map,
   `state.RECORD_V` bumped to 3, warning dedupe re-keyed on the message, `accept_code` walk
   fault-isolated, and the invalidation policy layer extracted to `oryxflow/codecheck.py`.

10. **Field-test round 4 (WorkflowMulti backtest, 7 finals x 12 flows): bulk accept coverage +
    result-warnings inflation.** Two defects:
    - *Bare `flow.accept_code()` silently missed (task, flow) pairs*: it walked only
      `flow.get_task()` — the single configured default task — so tasks reachable only from
      OTHER finals passed to `flow.run([finals...])` (and their grandfathered record-less
      outputs) never got baseline records; a fresh process kept warning `output predates
      current code` for that stable subset. Fix: `Workflow` records every task instance it
      runs (`_run_roots`), a bare `accept_code()` walks the union of the default task and all
      run roots, and both `Workflow.accept_code`/`oryxflow.accept_code` accept a LIST of roots
      (one shared `seen` walk, overlapping upstream trees stamped once). Invariant satisfied:
      after `flow.accept_code()` a brand-new process running the full finals set warns zero
      and recomputes nothing.
    - *`RunResult.warnings` inflated* (94 entries while the deduped print/log channels emitted
      8): it recorded every `warn()` occurrence — per instance, per flow build — so
      `len(result.warnings)` didn't answer "how many pending conditions". This supersedes the
      round-2 decision that RunResult records every occurrence: `Advisor.warned` now dedupes
      on the message per build, and `MultiRunResult.warnings` dedupes across flows. The event
      stream remains the only every-occurrence channel.
    Regression tests: `test_flow_accept_covers_all_run_finals` (delete state file + bump
    source mtime to simulate the post-upgrade state, assert persisted records + silent fresh
    run), `test_multi_result_warnings_deduped`; `test_warning_dedupe_family_level` rebased to
    the deduped semantics. Baseline moved 127 -> 129.

11. **Field-test round 5: the round-4 bulk-accept fix didn't survive a fresh process.** The
    consumer's one-shot bless script constructs the flow and calls `flow.accept_code()`
    WITHOUT running first, so the round-4 `_run_roots` (in-process run history) was empty and
    the walk degraded to the single configured default task again — 309 records stamped, all
    in one subtree; 6 of 7 finals still warning. (The round-4 regression test masked this by
    running the finals in the same process before accepting.) Fix: the no-arg
    `Workflow.accept_code()` now sweeps every imported task class
    (`core.Task.__subclasses__()`, recursive, skipping `oryxflow.*` template classes),
    instantiates each with the flow's params via `get_task`, and walks the union (plus the
    default task and any run roots). Classes that don't instantiate under the flow's params
    (DAG-internal params, abstract bases) are skipped; tasks whose outputs don't exist are
    no-ops in the walk — anything missed simply recomputes (the safe direction). This is the
    consumer's option 2 ("do internally what my bless_finals probe does") generalized: no
    finals list needed, works from a fresh process, per-flow on `WorkflowMulti` via the
    existing delegation. `test_flow_accept_covers_all_run_finals` rewritten to bless on a
    FRESH `Workflow` object with no run history. Baseline stays 129.
