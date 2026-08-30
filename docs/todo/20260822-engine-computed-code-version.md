# Computed `code_version`: fold non-Python inputs into code identity

## Context

oryxflow's promise is that a result can never quietly sit on stale inputs. Auto code
invalidation (`settings.code_version_auto`, on by default) delivers that for Python: it hashes
the AST of the task's own class plus the repo-local symbols it transitively references, so a
logic edit reruns the affected band automatically.

**It is blind to everything that is not Python.** A task whose result depends on bytes outside
the import graph gets no invalidation at all:

- a prompt template (`.jinja2`, `.md`, `.txt`) rendered at run time
- a `.sql` file read and executed
- a `.yaml` / `.toml` config the task loads
- a model snapshot id resolved behind a provider alias
- any artifact fetched and cached locally before the run

The task keeps serving its cached output after the file that determines the result has changed.
That is exactly the failure mode the library exists to prevent, and today the only escapes are
manual: pin an explicit `code_version` string and remember to bump it, or `reset()` by hand.

This is not hypothetical. LLM evaluation projects built on oryxflow render a prompt template
inside `run()`; the template is the thing under test, and it changes on every iteration. Such
projects converge on the same workaround independently — disable `code_version_auto`, declare
`code_version = "v3"`, and carry a README warning to pass a `--reset` flag after touching a
template. The warning is load-bearing: forget it and you read last week's numbers as this
week's, with no signal anywhere that anything is wrong.

A second, smaller problem sits next to it. `code_version_auto` raises
`RuntimeError: dictionary changed size during iteration` when the task's import graph includes a
package inserted into `sys.path` at run time. That crash is *why* those projects turned auto off
in the first place, so fixing the blind spot without fixing the crash leaves them where they are.

### Design decisions

**1. `code_version` may be a method, not only a string.** The value it returns is serialized and
hashed into the task's code identity:

```python
class RenderReport(oryxflow.tasks.TaskPqPandas):
    template = oryxflow.Parameter()

    def code_version(self):
        return render_template(self.template)     # the bytes that actually determine the result
```

This adds no new vocabulary. `code_version` already means "the token that identifies this task's
code"; the only change is that you may compute it instead of typing it. A user who knows the
constant form needs no new concept to read the method form.

**2. Rejected: an `assets = ['prompts/*.jinja2']` declaration.** A glob presumes the dependency is
a file, in a known place, that the task reads directly. It is wrong for the common case: a prompt
assembled through an include chain by a helper elsewhere in the codebase, where the templates that
matter live in several directories and which ones participate depends on the parameters. Hashing
the *rendered* result captures the include chain, the helper's own logic, and the parameter that
selected the branch, in one value. A glob captures none of that reliably, and silently reports
success when it misses.

**3. `hash_files(*globs)` ships as a helper, not as the interface.** When the dependency genuinely
is a set of files, `return oryxflow.hash_files('queries/*.sql')` inside `code_version()` is the
obvious body. Offering it as a function rather than a declaration keeps one mechanism instead of
two, and keeps the general case general.

**4. It must be cheap, and that constraint is documented, not enforced.** `_code_fingerprint` runs
on every completeness check, memoized per traversal (`fingerprint_cached`). A `code_version()` that
renders a template is microseconds; one that makes a network call would make `complete()` cost a
round trip per task. The docstring and the docs page say so explicitly, and recommend resolving
remote values once, outside the task, and passing them in as a Parameter. Enforcing this (a timeout,
a warning above N ms) is rejected as guesswork about the user's machine.

**5. Determinism is the user's contract.** A `code_version()` that returns a timestamp reruns the
task every time. That is a correct consequence of what was declared, not a bug to defend against.
Documented in one sentence beside the cheapness note.

**6. Precedence is unchanged.** An explicit `code_version` — constant or method — overrides auto for
that task, and the AST hash stays warn-only advisory. No existing task changes behavior.

## Execution

### Branch

This is the **first** of three related plans; do not start the other two until this one verifies.
The set, in order:

1. `docs/todo/20260822-engine-computed-code-version.md` — this file.
2. `docs/todo/20260822-evals-llm-eval-harness.md` — `oryxflow.evals`, depends on this one.
3. `../oryxflow-claude-plugin/docs/plans/20260822-eval-commands.md` — the plugin commands.

Work on a topic branch, matching the repo's existing style (`20260717-docs-seo`):

```bash
cd <oryxflow repo root>
git checkout main && git pull
git checkout -b 20260822-evals
```

Do not commit or push until verification passes and the user asks. When you do commit, **this plan
file goes in the same commit as the code it describes** (repo convention), along with an
`## Implementation notes (divergences from the plan as built)` section appended here if anything
changed.

### Subagent orchestration

Fan out with the Agent tool. The waves below are **file-disjoint by construction** — two agents
must never hold the same file, because there is no merge step and the second write wins silently.

Every subagent prompt must carry, verbatim: the absolute path of this plan file, the step numbers
it owns, its exclusive file list, and the instruction *"do not create or edit any file outside your
list; if you believe you need to, stop and report instead."*

**Wave 1 — four agents in parallel:**

| Agent | Steps | Exclusive files |
|---|---|---|
| A · fingerprint | 1, 3, 4 | `oryxflow/core.py`, `oryxflow/codecheck.py`, `oryxflow/state.py` |
| B · codehash | 2, 6 | `oryxflow/codehash.py`, `tests/test_codehash_syspath.py` |
| C · hash_files | 5 | `oryxflow/utils.py`, `oryxflow/__init__.py` |
| D · docs | 7 | `docs/docs/managing-workflows.md`, `CHANGELOG.md` |

A and B are split along the `codehash.py` boundary specifically because both step 2 and step 6 land
in that one file; giving them to different agents would collide. Agent B owns the file, both
changes, and the repro test.

**Wave 2 — one agent, after all four report:** write `tests/test_codeversion_computed.py` (all
seven cases in Verification), run the full baseline, and fix integration breakage. Only this agent
may touch files outside wave 1's lists, and only to fix a genuine integration defect.

**Do not delegate the end-to-end check.** Run the manual prompt-edit sequence at the bottom of
Verification yourself — it is the acceptance test for the whole plan, and a subagent reporting
"it works" is not evidence.

## Implementation

### 1. `oryxflow/core.py` — resolve a callable token

`Task._code_fingerprint_compute()` (currently `core.py:606`) reads `own = self.code_version`.
`code_version` declared as a `def` makes `self.code_version` a bound method, so `callable()`
discriminates the two forms with no ambiguity (a plain string is not callable).

Replace the single line with a resolution step:

```python
    def _code_fingerprint_compute(self):
        dep_fps = [d._code_fingerprint for d in self.deps()]
        own = _resolve_code_version(self)
        if own is None:
            ...unchanged...
```

Add the module-level helper next to it:

```python
def _resolve_code_version(task):
    """The task's own code-identity token. A `code_version` declared as a method is
    CALLED and its return value hashed, so a task can fold bytes outside the Python
    import graph -- a rendered prompt, a .sql file, a resolved model snapshot -- into
    its own identity. Runs on every completeness check (memoized per traversal), so
    the body must be local and cheap: never a network call."""
    cv = task.code_version
    if cv is None or not callable(cv):
        return cv
    payload = json.dumps(cv(), sort_keys=True, default=str)
    return 'fn:{}'.format(hashlib.md5(payload.encode('utf-8')).hexdigest()[:16])
```

`json.dumps(..., sort_keys=True, default=str)` matches the serialization the parameter hashing
already uses for dict/list values, so ordering is stable and arbitrary objects degrade to `str`
rather than raising.

### 2. `oryxflow/codehash.py` — do not double-count the method body

`_is_code_version_stmt()` (`codehash.py:159`) strips a class-body `code_version = ...` assignment
from the AST before hashing, so bumping a pin is a token change rather than a source change. The
method form needs the same treatment or editing `code_version()` moves *both* the computed token
and the advisory AST hash, producing a spurious "code changed but code_version didn't" warning on
a change that was correctly handled.

Extend the predicate to also match a `code_version` function definition:

```python
def _is_code_version_stmt(stmt):
    # a class-body `code_version = ...` pin (plain or annotated assignment), or a
    # `def code_version(self)` computing one -- both are the token, not the logic.
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return stmt.name == 'code_version'
    ...existing assignment branches unchanged...
```

Update the module docstring (`codehash.py:11-12`), which currently names only "class-body
``code_version`` pin lines".

### 3. `oryxflow/codecheck.py` — record the resolved token, not the bound method

Four sites read `task.code_version` directly and would otherwise write a `<bound method ...>` repr
into the state store or into a warning message: `codecheck.py:89` and `:130` (record construction),
`:197-248` (the warning text and the record comparison at `:220` / `:243`), and `:368`.

Route all of them through the same resolver:

```python
from oryxflow.core import _resolve_code_version
...
'code_version': _resolve_code_version(task),
```

The comparison at `:220` (`rec.get('code_version') != task.code_version`) becomes a comparison of
resolved tokens, which is what makes a template edit register as a code change against a record
written before the edit.

The user-facing message at `:199-205` ("code_version still {} -- reusing cached output. Bump
code_version to recompute") is written for the constant form. When the token resolved from a
method, say so instead: name the computed token and point at the method, since "bump it" is not
the action for a computed version.

### 4. `oryxflow/state.py` — bump the record schema version

The fingerprint formula now folds a resolved callable token. `RECORD_V` (`state.py:24`) goes to
`4`, with a comment line matching the existing style:

```
# v4: code_version may be a method; records store the RESOLVED token.
```

Records at `v3` are treated as unverifiable and silently re-stamped by `build()`'s advisory sweep
— a one-time re-baseline, never a mass rerun, exactly as the v2 to v3 transition already documents.

### 5. `oryxflow/utils.py` — `hash_files`

```python
def hash_files(*patterns, root=None):
    """Stable digest of the CONTENT of every file matching these glob patterns.

    For use inside a task's ``code_version()`` when the bytes that determine its
    result are files -- SQL, templates, config -- rather than Python. Paths are
    resolved relative to ``root`` (default: the current working directory) and
    sorted, so the digest does not depend on filesystem enumeration order.

    Raises FileNotFoundError when a pattern matches nothing: a typo'd path that
    silently hashed to "no files" would report every run as unchanged, which is
    the failure this exists to prevent.
    """
```

Implementation: `pathlib.Path(root or '.').glob(pattern)` per pattern, collect matches into a set,
sort by POSIX-normalized relative path, and feed `md5` with `relpath + '\0' + bytes + '\0'` per file
so a rename registers as a change. Re-export from `oryxflow/__init__.py`.

The empty-match raise is the important half. It is the same reasoning as an anchored-diff prompt
variant in an eval harness: a probe that silently decays into a no-op is worse than one that
fails, because it keeps reporting success.

### 6. Fix the `sys.path` crash in code hashing

Reproduce first, then fix — do not skip the reproduction, because the fix depends on which
iteration site raises.

Repro (`tests/test_codehash_syspath.py`): create a temp package directory outside the repo, insert
it into `sys.path` at run time, import a module from it inside a task's module, and call
`codehash.task_code_hash(task)` while a lazy import fires. The exception is
`RuntimeError: dictionary changed size during iteration`, from iterating `sys.modules` (or a
derived mapping) while an import mutates it.

Fix: snapshot before iterating — `for name, mod in list(sys.modules.items()):` — at every such site
in `codehash.py`. Snapshotting is correct rather than merely defensive here: the hash describes the
module set as it stood when the traversal began, which is precisely what `codehash.freeze()` already
brackets `build()` to guarantee.

### 7. Docs

- `docs/docs/managing-workflows.md` — a short section under the code-invalidation material: *"When
  your task depends on something that isn't Python."* Show the `code_version()` method with a
  `.sql` file and `hash_files`, state the cheapness constraint in one sentence, and state the
  determinism contract in one more. User-facing register: what you get and what to type, never how
  the fingerprint is computed.
- `docs/docs/reference.md` picks up `hash_files` from its docstring automatically via mkdocstrings.
- `CHANGELOG.md` under `## [Unreleased]`.

## Files modified

| File | Change |
|---|---|
| `oryxflow/core.py` | `_resolve_code_version()` helper; `_code_fingerprint_compute()` calls it |
| `oryxflow/codehash.py` | `_is_code_version_stmt()` also matches a `code_version` FunctionDef; module docstring; `list(sys.modules.items())` snapshots |
| `oryxflow/codecheck.py` | four `task.code_version` reads routed through the resolver; computed-token wording in the stale-code warning |
| `oryxflow/state.py` | `RECORD_V = 4` + comment line |
| `oryxflow/utils.py` | `hash_files()` |
| `oryxflow/__init__.py` | re-export `hash_files` |
| `docs/docs/managing-workflows.md` | "when your task depends on something that isn't Python" |
| `CHANGELOG.md` | Unreleased entry |
| `tests/test_codeversion_computed.py` | new (see Verification) |
| `tests/test_codehash_syspath.py` | new (crash repro) |

## Verification

**Baseline to hold: 114 passing** on the four-file command (the 86 in `CLAUDE.md` is stale; the suite has grown).

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

New tests in `tests/test_codeversion_computed.py`:

1. **A computed token invalidates.** Task with `def code_version(self): return _PROBE[0]`. Run,
   assert complete. Mutate `_PROBE[0]`, assert `complete()` is now False and a re-run recomputes.
2. **A stable computed token does not.** Same task, unchanged probe: `flow.run()` reports a cache
   hit and `result.did_run(task)` is False.
3. **File content drives it.** `code_version()` returning `hash_files('q/*.sql')`; edit the `.sql`
   and assert the task reruns; touch its mtime only (same bytes) and assert it does not — content,
   not mtime.
4. **`hash_files` raises on no match.** `pytest.raises(FileNotFoundError)`.
5. **Constant form is untouched.** A task with `code_version = 'v1'` behaves exactly as before,
   including the advisory warning path.
6. **Editing the `code_version` method body alone does not fire the advisory warning** (step 2's
   AST exclusion) while still changing the token when the returned value changes.
7. **Record round-trip.** After a run, the `.oryxflow-code-status.json` record's `code_version`
   field is the resolved `fn:<hash>` string, not a bound-method repr, and `RECORD_V` is 4.

In `tests/test_codehash_syspath.py`: the repro from step 6 passes rather than raising
`RuntimeError`, with `settings.code_version_auto = True`.

End-to-end check that the motivating case is closed, run by hand once:

```python
# a task that renders a template inside run(), with code_version() returning the render
flow.run()                 # runs
flow.run()                 # cache hit
# edit the template file on disk, change nothing else
flow.preview()             # the task is listed as PENDING, reason: code change
flow.run()                 # reruns, downstream cascades
```

No `--reset`, no hand-bumped version string.

## Implementation notes (divergences from the plan as built)

**Baseline was 114, not 86.** The four-file command passes 114 and the whole `tests/` directory
passes 212 before this change, 225 after. `CLAUDE.md`'s 86 was stale.

**Step 3 undercounted the `code_version` read sites: there are seven, not four, and two of the
misses were severe.** The plan enumerated `codecheck.py` only. Also reading the raw attribute:

- `oryxflow/tasks/__init__.py` `_own_code_ok()` — compared `rec.get('code_version')` against the
  raw `self.code_version`. For the method form that is a *bound method*, which never equals the
  stored `'fn:<hash>'`, while `self.code_version is not None` is trivially true — so control fell
  through to `return False`. **A task with a computed `code_version` was therefore never complete
  and re-ran on every `run()`, defeating the cache permanently** — the exact inverse of this
  plan's purpose. Found only because the verification tests were written; reading the enumerated
  call sites could not have surfaced it, since this file is not among them.
- `oryxflow/tasks/__init__.py` `_getpath()`, `keep_versions` branch — `str(<bound method>)` embeds
  a memory address, so the sanitized directory name (`v<bound method X.code_version of <X object
  at 0x...>>`) differed every process and no output was ever found again. Resolution now happens
  only when `keep_versions` is set, so `output().path` keeps its cost profile.
- `oryxflow/core.py` `build()`'s `task_ran` event payload — would have written a bound-method repr
  into the event stream. The adjacent `'auto': task.code_version is None` was deliberately left
  reading the raw attribute: a computed token is not auto, and `is None` correctly yields `False`.

The lesson for the record: an attribute that changes type needs a *grep of the whole repo*, not an
enumeration in a plan. Every raw read is a latent bug when the value may be a bound method.

**Step 6's diagnosis was wrong about the site.** The `RuntimeError: dictionary changed size during
iteration` does not come from iterating `sys.modules` — no site in `codehash.py` iterates it (all
five references are `.get()` / `in` / `[key]` lookups, which are safe). It comes from the derived
mapping `files_seen` in `task_hashes()`'s final star-import pass: a star target resolving to a
project-local file the walk never visited goes through `_add_file()`, which does
`files_seen.setdefault(...)` — inserting into the dict being iterated. The prescribed
`list(sys.modules.items())` fix had **no applicable site**; the actual fix is
`list(files_seen.items())`. The run-time `sys.path` insertion is load-bearing for the repro rather
than incidental: it is what lets a bare star target resolve to a local file at all. This is why the
plan said reproduce before fixing.

**Step 5: `Path(root).glob(pattern)` raises on an absolute pattern.** The literal form the plan
prescribed raises `NotImplementedError: Non-relative patterns are unsupported` (Python <= 3.12) for
an obviously reasonable input. `hash_files` now globs an absolute pattern from its own anchor, with
`root` not applied to it. Consequence, documented rather than fixed: such files key on their full
POSIX path, so absolute and relative spellings of the same file set give different digests. Harmless
(a `code_version()` picks one spelling and keeps it) but it argues for relative patterns plus
`root=` as the recommended form.

**Circular import materialized, as the plan anticipated.** `core.py` imports `codecheck` at top
level and `codecheck`'s docstring states the discipline, so every new `_resolve_code_version` import
in `codecheck.py` is function-local, folded into the existing `from oryxflow.core import flatten`
lines.

**`setup.py` gained `tenacity`** in the `evals` extra, not for this plan but discovered while
verifying it: `RetryConfig` lives in `pydantic_ai.retries` (package `pydantic-ai-slim`), which
pydantic-evals imports only under `TYPE_CHECKING`, and that module raises `ImportError` without
`tenacity`. See the companion evals plan.
