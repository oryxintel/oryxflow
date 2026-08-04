# Lint declared dependencies whose data `run()` never reads (`check_inputs`)

## Context

A task declares its dependencies with a decorator and receives their data through one call, in
decorator order:

```python
@oryxflow.requires(ModelTrain, FeaturesTransform, SlowBacktest)
class PredictRecent(oryxflow.tasks.TaskPqPandas):
    def run(self):
        (df_a, df_ax, df_ay), (df_b, df_bx, df_by), df_slow = self.inputLoad()
        # df_slow is never mentioned again
```

`df_slow` is loaded and dropped. **The declaration is still honoured**: oryxflow must PRODUCE
`SlowBacktest` before `PredictRecent` can run. In the motivating case that upstream task was an
expanding-window walk-forward — roughly 130 sequential model refits per sector — and every
consumer of `PredictRecent`, including a quarterly report with nothing to do with backtesting,
paid for those refits on every cold build to obtain a discarded frame. It had been that way for
months.

**No dependency-graph query can find this**, which is why it is a separate item from
`20260803-engine-dependency-queries.md` (where it was explicitly deferred). The edge is *real*:
`@requires` declares it, the scheduler honours it, `find_deps` reports it, `reset_downstream`
correctly invalidates through it. Every graph query answers "does the report depend on
`SlowBacktest`?" with a correct *yes*. The graph is not wrong; the code is. The only place the
truth lives is the body of `run()`, in the fact that a bound name has no second occurrence.

How it was actually found: read `run()`, then count references to `df_slow` over the method's line
range — one occurrence, the unpack itself. That is a mechanical check, and it should not depend on
a human happening to read the right function.

### Why these accumulate silently

- **The declaration and the use are far apart** — decorator at the top, unpack in the body, uses
  scattered below. Deleting the last use of a frame leaves the decorator argument behind and
  nothing complains.
- **Caching hides the cost.** After the first build the dead dependency is a cache hit and costs
  nothing visible. The bill arrives only on a cold build, a parameter change, or a reset — by
  which time nobody connects the wait to a stray decorator argument.
- **It reads as intentional.** `(df_recent, _)` and `df_slow` look equally deliberate. There is no
  syntactic difference between "discarded on purpose" and "forgot to remove this".

Evidence that they accumulate: a throwaway prototype of this check, covering only the simplest
unpack pattern, found a **second** instance in the same project immediately — a task binding its
second dependency to a bare top-level `_`. Two for two, in a codebase nobody suspected.

### Design decisions

- **The default path is ZERO caller code: the check fires inside `preview()` and `run()`.** This is
  the single most important decision. A `check_inputs()` function that must be remembered has the
  same fate as an ad-hoc probe — it runs when someone thinks of it, which is how the motivating
  bug survived months. `preview()` and `build()` already walk the full graph and already hold the
  task classes; the check costs one AST parse per family. It surfaces as a warning in output the
  user is already reading, at exactly the moment a dead dependency is about to be paid for.
- **Reuse the existing advisory plumbing, do not invent a second one.** `codecheck.Advisor`
  already implements the four-channel warn used by the code-staleness advisory
  (`warnings.warn` + loguru + event + `RunResult.warnings`), with per-message dedupe so
  parameterized instances of one family do not inflate the count. A new `UnusedInputWarning`
  alongside `StalenessWarning`, emitted the same way, gets correct behaviour for free — including
  the `-W error` guard that keeps an advisory from aborting a build.
- **Reuse `codehash._class_source(cls)` for source retrieval.** The task classes are already
  imported by the time a `Workflow` exists, so per-class source + `ast.parse` is both simpler and
  more correct than scanning module files by path (the prototype needed a hardcoded module list
  and could not find tasks defined elsewhere).
- **Outer unpack elements are DEPENDENCIES; inner elements are that dependency's `persist`
  outputs. Only outer-level discards are findings.** The unpack is two levels deep and the levels
  mean different things. A nested `_` means "I don't need one of this dependency's frames" —
  entirely normal, and common. A *top-level* `_` means the whole dependency is dead. Conflating
  them buries true positives under roughly 5x their number in noise, and a lint people learn to
  ignore is worse than no lint.
- **Three verdicts — `unused` / `clean` / `unanalyzed` — never two.** The prototype's first
  version silently skipped the shapes it could not parse and printed a tidy two-line clean report
  from a scan covering barely half the in-scope tasks. A report that looks complete but is not
  converts "we did not check" into "we checked and it is fine". Every in-scope task appears with a
  verdict and, when unanalyzed, a reason.
- **Tasks with no `run()` are omitted, not counted as analysis failures.** Pure aggregator tasks
  exist to pull a band together, have no body and no data to discard. Counting them as misses
  reported 56% coverage in the prototype when the true figure was 64% (9 of 14) — whoever tunes
  the lint would optimize against a denominator including tasks it should never touch.
- **No false positives, at the cost of recall.** Where usage cannot be proven — aliasing, dynamic
  access, shadowing, `**kwargs` — report `unanalyzed`, never `unused`. Recall is cheap to
  sacrifice here: the simplest possible rule already caught 2 of 2 real instances.
- **Advisory by default, with a suppression comment.** A deliberately unused dependency is
  legitimate (declared for ordering, or a side effect). Without an escape hatch the first false
  alarm gets the whole check disabled.
- **Filtering and formatting belong INSIDE the call.** Every caller would otherwise write the same
  three lines — select `verdict == 'unused'`, format `file:line - task loads dep and discards it`,
  count. `check_inputs(raise_on_unused=True)` raises with the formatted message; only the
  verdict/reason taxonomy is exposed, because that is the part where callers genuinely differ.

### Coverage measured on the motivating project

A prototype implementing only the positional rule, over 42 task classes:

| bucket | count |
|---|---|
| tasks with `@requires` | 42 |
| ... with 2+ deps (in scope) | 16 |
| ... omitted, no `run()` (aggregators) | 2 |
| ... analyzed | 9 (64% of the corrected 14) |
| ... `unanalyzed` | 5 |
| findings (`unused`) | 1 |

The five unanalyzed cases are **not a long tail** — they are exactly three forms:

| form | count |
|---|---|
| `data = self.inputLoad()` then subscripting | 2 |
| `self.inputLoad(task='X')` — keyed selection | 2 |
| `self.inputLoadConcat(task='X')` | 1 |

Counterintuitively the keyed forms (3 of 5) are the *easier* case: the dependency is named as a
string literal in the call, so "which declared deps are never named in any load" is a plain set
difference with no positional inference. Implement those before the bare-bind case, which needs
subscript tracking for a smaller payoff.

### Explicitly deferred (not in this change)

- **Cross-`run()` dataflow** — a name passed to a helper that ignores it. Out of scope; the check
  is deliberately intra-method.
- **Auto-fix / codemod** to strip the dead decorator argument. The remedy is always the same two
  deletions, but applying it automatically to a `@requires` line is not worth the risk.
- **`unanalyzed` shown by default.** Collected and returned, but only `unused` is warned about;
  most users want the finding, not the coverage report.

## Implementation

### 1. New module `oryxflow/inputcheck.py`

Self-contained AST analysis. No imports of user modules beyond what is already loaded, no
instantiation, no cache reads, no filesystem walk.

```python
class UnusedInputWarning(UserWarning):
    """A task declares a dependency whose loaded data run() never references."""


class InputFinding:
    """One (task, dependency) pair and what static analysis concluded about it."""
    __slots__ = ('task_family', 'dep_family', 'dep_index', 'binding',
                 'verdict', 'reason', 'source')
    # verdict : 'unused' | 'clean' | 'unanalyzed'
    # reason  : set only when verdict == 'unanalyzed'
    # binding : the unpack name, '_', or None
    # source  : 'tasks_model.py:1039' - the DECORATOR line, i.e. the line to edit

    def message(self):
        return ('{}: {} loads {} and never uses it - remove the dependency '
                'or add "# oryxflow: input-unused"').format(
                    self.source, self.task_family, self.dep_family)


def check_class(cls):
    """Analyze one task class. Returns list[InputFinding], one per declared dependency.
    Returns [] for classes with no @requires decorators or no run() method."""
```

`check_class` steps:

1. `src = codehash._class_source(cls)`; `tree = ast.parse(textwrap.dedent(src))`.
2. Collect declared dependencies from the `@oryxflow.requires` / `@requires_each` decorators,
   outermost first: positional `ast.Name` args in order; `ast.Dict` values are recorded as
   **keyed** (they resolve by name, not position — see step 5).
3. Locate `run()`. Absent -> return `[]` (omitted, not a failure).
4. Find the load call. Dispatch on shape per the table below.
5. Emit one `InputFinding` per declared dependency.

Dispatch table — this is the behavioural contract:

| detected shape | verdict per dep | reason |
|---|---|---|
| no `run()` method | *omit entirely* | aggregator, no data to discard |
| `a, b, c = self.inputLoad()`, N elements == N deps | analyze each, below | — |
| top-level element is `_` | `unused` | whole dependency discarded |
| top-level element is a `Name` with 0 references in `run()` (excluding the load assignment itself) | `unused` | — |
| top-level element is a nested tuple/list and **all** its names have 0 references | `unused` | inner `_` alone is NOT a finding — that is one `persist` key |
| top-level element is a nested tuple/list, any name referenced | `clean` | — |
| N elements != N deps | `unanalyzed` | `'unpack arity {n} vs {d} deps'` |
| every load is `inputLoad(task='X')` / `inputLoadConcat(task='X')` | dep named in some call -> `clean`; not named -> `unused` | set difference on string literals |
| `@requires({'k': T})` + `inputLoad(flatten=False)['k']` | key present -> `clean`; absent -> `unused` | keyed, same as above |
| `data = self.inputLoad()` then subscripting | `unanalyzed` | `'bare bind, subscript not tracked'` |
| anything else | `unanalyzed` | `'unrecognized load form'` |

Suppression: a `# oryxflow: input-unused` comment on the decorator line forces `clean`. Comments are
not in the AST — read them from the source lines captured in step 1, keyed by `lineno`.

### 2. `codecheck.Advisor` — a second advisory channel

Add `Advisor.warn_input(task, finding)` mirroring `warn()` (`codecheck.py:106`): append to
`self.warned`, dedupe per message so parameterized instances of one family do not inflate
`RunResult.warnings`, `_stdwarnings.warn(msg, UnusedInputWarning)` inside the same `try/except`
that keeps `-W error` from aborting a build, and `self.emit('input_warning', {...})`.

Check each family **once per build** — reuse the `self.advised` set pattern, keyed on
`task_family` rather than `task_id`, since the result is identical for every parameterization.

### 3. `core.build()` — run the check during the sweep

Where the code advisory is already consulted per task, add the input check for families not yet
seen this build. Findings with `verdict == 'unused'` go to `Advisor.warn_input`. Cost is one
`ast.parse` per family per process; memoize on `cls` in `inputcheck`.

### 4. `preview()` — same warnings, before anything runs

`preview()` (`__init__.py:82`) currently renders a tree and returns/prints it. Append an
`UNUSED INPUTS` block listing `finding.message()` for every `unused` finding in the previewed DAG.
This is the placement that matters most: `preview()` is what a user runs *before* committing to a
cold build, which is exactly when a dead heavy dependency is about to be paid for.

### 5. `Workflow.check_inputs()` — the explicit form

```python
    def check_inputs(self, tasks=None, raise_on_unused=False, include_clean=False):
        """Static check: which declared dependencies does run() load and never use?

        A dead dependency is still HONOURED by the scheduler - its upstream band is computed on
        every cold build to produce a frame that is discarded. No dependency-graph query finds
        this (the edge is real; only the data is dead), so it is checked by AST.

        Args:
            tasks (class, list): roots to sweep (default: the flow's default task).
            raise_on_unused (bool): raise ValueError, with every finding formatted, instead of
                returning. For CI - so the caller does not re-implement the filter and message.
            include_clean (bool): also return 'clean' and 'unanalyzed' records (coverage report).

        Returns: list[InputFinding], 'unused' only unless include_clean.
        """
```

Prints a rendered summary when called interactively and returns the list — same dual behaviour as
`RunResult`, which both summarizes and exposes `.ran`.

### 6. Docs

- Library `CLAUDE.md`: one line under the Workflow method list, next to `dependents` /
  `dependencies` from the companion spec.
- Plugin skill docs: see the next section — placement is not incidental here, it is most of
  whether the check ever gets used.

## Where an agent would look, and what it needs to be told

The companion spec's whole finding was that the capability existed and was never reached, because
it was not visible at the moment of asking. The same failure is available to this check, so
placement is specified rather than left to the implementer.

### Where I would look for it (plugin `skills/oryxflow/`, in priority order)

| file / section | why here | what it says |
|---|---|---|
| `reference.md` §"Loading Data from Upstream Tasks" (~L249) | **The primary site.** This is the section describing `inputLoad()` and the unpack shapes — the exact construct the defect lives in. An agent reading how to bind inputs should learn in the same breath that an unbound one costs real compute. | "Every declared dependency must actually be READ. A frame that is unpacked and discarded still forces its whole upstream band to be computed on every cold build — the scheduler honours the declaration, not the usage. `flow.check_inputs()` finds these; `preview()` warns about them." |
| `SKILL.md` §"Modify an existing task (the common iterate loop)" (~L629) | **The site where the defect is CREATED.** Removing the last use of a frame is precisely the edit that strands a decorator argument. Guidance is worth more at creation than at diagnosis. | "When you delete the last use of an input, delete the matching `@requires` argument AND its unpack binding. Leaving the declaration keeps the upstream band in the build." |
| `SKILL.md` §"Debug workflow issues" (~L698) and `reference.md` §"Debugging oryxflow Workflows" (~L929) | The symptom-driven entry point — where an agent goes with "this cold build is slower than the work justifies". | Add that symptom explicitly, mapped to `check_inputs()`. |
| `reference.md` §"Quick Reference" (~L971), `SKILL.md` §"Quick Reference" (~L867) | Where an agent scans for an API it half-remembers. | One line: `flow.check_inputs()` - declared deps whose data `run()` never reads. |

`reference.md` §"Dependencies" (~L90) is deliberately **not** the primary site: it teaches how to
declare edges, and the defect is not a declaration error — the declaration is valid. Putting it
there invites the conflation the next subsection warns against.

### Guidance an agent needs to use it effectively

Placement alone is not enough; five things must be stated or the check gets misused.

1. **The symptom, not just the API.** An agent does not wake up wanting a lint. It arrives with
   *"why is this cold build so slow"* or *"why does this report drag a backtest in"*. Both entries
   must name the symptom first and the call second. Without symptom framing the API line is
   unreachable — it is not searched for, because there is no name for the problem yet.

2. **This check and the dependency queries answer DIFFERENT questions — say so explicitly, in
   both docs.** They are easy to conflate and were conflated during the motivating investigation:

   - `flow.dependents(X)` — *does anything depend on X, and by what route?* Reports **declared**
     edges. Correctly reports a dead edge as a real edge.
   - `flow.check_inputs()` — *is a declared edge actually USED?* Reports **dead data**.

   Neither substitutes for the other. `dependents()` will confirm a heavy task is reachable and
   never tell you the reachability is pointless.

3. **What to do with a finding, including the cache consequence.** The remedy is two deletions —
   the decorator argument and the unpack binding — and the non-obvious part must be stated:
   **no reset is needed.** Task identity is class plus parameters, not dependencies, so removing a
   dead dependency does not change the dependent task's identity and its cached output stays
   valid. The output is provably unchanged because the input was never read. Without this line an
   agent will either reset unnecessarily (expensive, and in a manual-reset project a real risk) or
   hesitate to make a safe edit.

4. **`unanalyzed` is not `clean`.** A coverage figure will be well under 100% on any real project
   (64% on the motivating one). Docs must say that a silent task is unchecked, not blessed —
   otherwise "check_inputs found nothing" gets reported as "there are no dead dependencies".

5. **Where the warning appears without being asked for.** State that `preview()` and `run()` emit
   it, so an agent reading run output recognises the line as actionable rather than noise. In a
   project that already surfaces `RunResult.warnings`, note that these join that list.

### What would NOT have worked

More prose asserting that the decorators are the DAG. The motivating project's `CLAUDE.md` already
said exactly that, and the graph walk still got hand-rolled with `grep`. Guidance changes behaviour
only when it sits on the path already being walked — the `inputLoad()` section, the modify-a-task
loop, the debugging symptom list — not in a concepts section read once at session start.

## Files modified

- `oryxflow/inputcheck.py` — **new**: `UnusedInputWarning`, `InputFinding`, `check_class`, per-class
  memo.
- `oryxflow/codecheck.py` — `Advisor.warn_input`, family-keyed advised set.
- `oryxflow/core.py` — call the check in `build()`'s per-task sweep.
- `oryxflow/__init__.py` — `UNUSED INPUTS` block in `preview()`; `Workflow.check_inputs`;
  re-export `UnusedInputWarning`, `InputFinding`.
- `tests/test_inputcheck.py` — **new**, see below.
- `CLAUDE.md` — one-line method mention.
- (plugin repo) `skills/oryxflow/reference.md` — §"Loading Data from Upstream Tasks" (primary),
  §"Debugging oryxflow Workflows", §"Quick Reference".
- (plugin repo) `skills/oryxflow/SKILL.md` — §"Modify an existing task", §"Debug workflow issues",
  §"Quick Reference". Per the placement table above.

## Verification

```bash
python -m pytest tests/test_inputcheck.py tests/test_main.py tests/test_workflow.py -q
```

New tests assert, on purpose-built fixture tasks:

- **The motivating shape.** Three deps, third bound to a name never referenced -> exactly one
  `unused` finding naming that dep, `binding` set to the name.
- **Top-level `_`.** Two deps, second bound to `_` -> one `unused` finding, `binding == '_'`.
- **Nested `_` is NOT a finding.** `(df_a, _), df_b = self.inputLoad()` with both `df_a` and
  `df_b` used -> zero findings. This is the false-positive guard that decides whether the lint is
  usable.
- **Arity mismatch is `unanalyzed`, not `clean` and not `unused`** — and appears in
  `include_clean=True` output with a reason.
- **No `run()`** -> zero records, and the task is absent from the coverage list entirely.
- **Keyed loads.** `inputLoad(task='X')` naming only one of two deps -> the unnamed dep is
  `unused`; naming both -> `clean`.
- **Suppression.** `# oryxflow: input-unused` on the decorator line turns `unused` into `clean`.
- **`raise_on_unused=True`** raises `ValueError` whose message contains the `file:line` and both
  family names.
- **Warning plumbing.** A build over a task with a dead dependency populates
  `RunResult.warnings`, emits `UnusedInputWarning` once per family (not once per
  parameterization), and does not abort under `-W error::UserWarning`.
- **`preview()`** output contains the `UNUSED INPUTS` block for the same DAG.

Baseline to hold: the existing suite passes unchanged — this check must not alter any task's
completeness, identity, or run order. It is advisory only.

## Implementation notes (divergences from the plan as built)

- **Source retrieval uses `inspect.getsource(cls)` / `inspect.getsourcelines(cls)`, not
  `codehash._class_source(cls)`.** `_class_source` returns `(abspath, root, modname)` — a *path*,
  not the source *text* — so it can't feed `ast.parse`. `inspect.getsource` is simpler and
  correct for already-imported classes; on a notebook/dynamically-built class with no retrievable
  source it raises `OSError`/`TypeError`, which `_analyze` catches and returns `[]` (feature inert
  for that class — the no-false-positives direction).
- **Suppression is whole-class, not strictly per-decorator-line.** If `# oryxflow: input-unused`
  appears anywhere in the class source, all `unused` verdicts for that class become `clean`. In
  the dominant single-`@requires` shape this is identical to per-line; it is coarser only for a
  class carrying several requires decorators, which is rare and errs toward silence (safe).
- **Extra false-positive guard not in the dispatch table:** if `run()` calls `self.input(` (the
  raw target accessor), a positional element that looks unused is downgraded to `unanalyzed`
  (`'self.input() used, positional use not tracked'`) rather than `unused` — a dep can be consumed
  through `self.input()[i]` without its unpack name ever being read. Recall cost, zero
  false positives.
- **Keyed set-difference also fires when a *positional* `@requires` is loaded via `task='X'`**
  (not only when the *declaration* is keyed). The plan's table lists the keyed-load row; the built
  dispatch reaches it whenever a `task=`-selecting load is present and there is no positional
  full-unpack to analyze instead, so `@requires(A, B)` + `inputLoad(task='A')` correctly reports
  `B` unused.
- **Superseded — the lint is now `preview()`-only; `run()`/`build()` do not lint.** As first
  built, the check was called at the top of `build()._process` and warned through the four-channel
  advisory (`warnings.warn` + loguru + event + `RunResult.warnings`), via `Advisor.check_inputs`/
  `warn_input` with a per-family `Advisor.input_advised` dedupe. That was later removed: `run()` is
  the hot iteration path, and although the AST parse is memoized per class in `inputcheck`
  (`_check_cache`) so it is only a one-time-per-process cost, the design decision was to keep the
  execution path free of any advisory work and surface dead dependencies only where a user is
  deliberately inspecting. So the automatic lint now fires **only in `preview()`** (its own
  `_scan_unused_inputs` helper, `__init__.py:109`, rendering the `UNUSED INPUTS` block) and
  explicitly via `Workflow.check_inputs()` (for CI). Removed with this change: the
  `_advisor.check_inputs(task)` call in `core.build()._process`, and the now-dead
  `Advisor.check_inputs`/`warn_input` methods and `input_advised` set in `codecheck.py`.
  Consequences: an unused-input finding no longer appears in `RunResult.warnings`, emits no
  `input_warning` event, and raises no `UnusedInputWarning` on the automatic path (that warning
  category is still defined and re-exported, but is now only meaningful to a caller who filters on
  it around an explicit scan). `tests/test_inputcheck.py::test_run_does_not_lint` pins the new
  contract (was `test_build_populates_warnings_once`).
- **`Workflow.check_inputs` gained a `print_it` kwarg** (prints a rendered summary by default,
  like `RunResult`) — additive to the planned signature.
