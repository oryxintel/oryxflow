# Make the eval flow work inside a real project

## Context

The eval harness (`oryxflow.evals`) and the four plugin commands (`/oryxflow:eval-plan`,
`eval-init`, `eval-cases`, `eval-run`) were built and documented against a clean case: a prompt
kept in `prompts/*.md`, an entry point that is one async call, and a metric that is one boolean
per case. Two rounds of real use broke that shape.

**First failure — a plan that could not be executed.** An agent was handed an eval plan written by
a planner with no knowledge of this library, and stalled repeatedly. Every blocker was the same
class: a number in the gate table that no column produced, or an input with no file behind it. The
plan named a `human='strength_human'` column for judge validation, but the judged unit was an item
*inside* the output, so the column could not exist; it named four gate numbers when the verdict
renders three; its guardrail was an algebraic restatement of its metric, so nothing could ever fail
it; and its rule "mark machine-written fixtures synthetic" applied to every fixture, which would
have left the headline averaging over an empty frame. That produced
`docs/docs/llm-evals-checklist.md` and the six-question rewrite of `/oryxflow:eval-plan`, both
already shipped.

**Second failure — the scaffold assumes a project it did not create.** Walking through two
realistic evals in a consumer project (a document extractor that turns unstructured text into
ordered sections, and a step that turns meeting notes into structured todo items) surfaced four
defects in the scaffold itself. Both evals must call the project's own code, both have prompts
living as Python string constants, and neither has an obvious scalar score. None of that is exotic
— it is the ordinary case.

The four defects:

1. **The eval template mutates global state at import time.** `resources/template-eval/eval.py`
   calls `oryxflow.set_dir(HERE / 'data')` at module scope. `set_dir` writes
   `oryxflow.settings.dirpath`, a process global, so importing the eval module from anywhere — a
   test, a notebook, another script — silently repoints the whole process's data directory at the
   eval's cache. It also diverges from the library for no stated reason: every other oryxflow flow
   uses the default `data/` relative to the working directory, and `docs/CLAUDE.md` explicitly says
   not to write `set_dir` when the value is the default.

2. **The scaffolded project is not importable.** `resources/template-minimal/` is flat modules at
   the repo root (`tasks.py`, `flow.py`, `cfg.py`, `run.py`) with no `pyproject.toml` and no
   install step, and `docs/docs/claude-plugin/project-structure.md` teaches that layout. But
   `evals/<name>/agent.py` imports the production entry point, and `python run_eval.py` puts the
   *eval* directory on `sys.path`, not the repo root. So in the project we ourselves scaffold, the
   eval's import fails — for a reason that has nothing to do with evals, and that equally breaks
   pytest from a subdirectory and notebooks under `eda/`.

3. **Prompts stored as Python string constants have nothing to cache on.** Every example ships
   `code_version()` hashing `prompts/*.md`. When the prompt is a string inside `myapp/extract.py`,
   there is no file to hash, no directory for `ev.git_tree` to check out as a baseline arm, and no
   way to render the prompt without importing the application. The current rule — *production code,
   never a copy* — also forbids the one legitimate case: a candidate prompt that does not exist in
   production yet, because it is being developed in the eval.

4. **Structured output has no worked scoring example.** Both real evals produce a *list of items*
   (sections, todos) with no scalar quality score, and the docs have no example of scoring that
   shape. `ev.Metric('label', 'column')` averages a boolean column and raises
   `TypeError: Need to pass bool-like values` on a count or a fraction, which is what a per-item
   rate looks like before it is pooled.

### Design decisions

Confirmed by the maintainer during the discussion that produced this plan:

- **The eval uses cwd-relative paths, exactly like the rest of the library.** No `set_dir`, no
  `__file__` anchoring of `cases.csv`. Rejected: keeping the anchors to protect against paying
  twice for cached cells when the eval is launched from two different directories. That scenario is
  self-inflicted — the `__file__` anchor on `cases.csv` is what makes it possible to run from two
  directories in the first place. With a relative `cases.csv`, launching from the wrong directory
  fails immediately with `FileNotFoundError` before anything is billed, and `run_eval.py`'s own
  `from eval import PromptEval` already pins the process to that directory. Asked directly whether
  the re-billing risk justifies an eval-relative cache, the maintainer's answer was "it does not".
  The general protection against paying twice belongs in the engine and covers non-LLM pipelines
  too — see `docs/todo/20260829-engine-second-cache-warning.md`.
- **`ROOT` in `agent.py` stays `__file__`-anchored.** It points *out* of the eval at the repo for
  `ev.git_tree`, is not about the eval's own files, and mutates nothing global.
- **The scaffolded project becomes installable.** Rejected: a `sys.path` bootstrap inside
  `agent.py`. It papers over an unpackaged project and creates two ways for an import to resolve.
  The fix belongs where the problem is — the project template gains a `pyproject.toml` and
  `/oryxflow:init-project` installs it editable.
- **Prompts are files: YAML front matter, markdown body, one file per prompt.** Rejected: a YAML
  document containing the prompt text (GitHub's `.prompt.yml` shape). YAML block scalars are
  indentation-sensitive and chomping-sensitive, so a whitespace slip silently changes the prompt,
  and diffs of a long block scalar are unreviewable. Front matter puts the metadata in YAML and
  keeps the prose out of it. This is the `.prompty` shape, and the dominant convention in the wild
  (markdown in roughly three quarters of surveyed prompt repositories).
- **Versions are git commits, not filenames.** Rejected: `v1.md`, `v2.md`, `v3.md` beside each
  other. The library already addresses a version as a ref (`ev.git_tree`, `ev.git_sha`,
  `ev.PromptArm`), so numbered files add a second version system that can disagree with the first,
  plus dead files nobody deletes.
- **The "never a copy" rule is refined, not dropped.** The baseline arm always reads production. A
  *candidate* arm may own its prompt inside the eval until it wins, and is then promoted
  byte-exact. Direction of travel is what matters: production -> eval is the drift bug, eval ->
  production is the point of running an eval at all.
- **`synthetic` tracks the provenance of the content, not whether a model touched it.** An LLM that
  selects, segments or reformats real material produces real cases (`synthetic=0`); an LLM that
  writes the material produces synthetic ones (`synthetic=1`).
- **Pairwise judging is documented as a pattern, not added as a feature.** Presentation order
  becomes an arm axis (`order=['ab', 'ba']`), so the existing sweep machinery gives both orderings
  and the gap between them measures position bias directly.

## Validation: the checklist against a second, independent plan

A second eval plan was written for a different feature by a different planner, again with no
knowledge of this library: a writing product where a user supplies a prose style guide, an LLM
reviews a document against it, and each finding carries a quoted passage, a suggested replacement
and a `must`/`should`/`guide` strength label. It is a markedly better plan than the first — it
states the question for someone with no context, separates deterministic metrics from judged ones,
and leads with an honest note that no ground-truth set exists.

Running the six sections of `docs/docs/llm-evals-checklist.md` over it found eight defects, which is
the evidence that the checklist earns its place. Six of them the plan does not know it has:

| # | Defect | Caught by |
| --- | --- | --- |
| 1 | The headline metric is the judged one, which the plan's own status note says is **blocked** on a human answer key that nobody has written. The two runnable numbers are relegated to report-only, so `verdict()` would print a headline it cannot compute. | Measure — budget the slots |
| 2 | The guardrail is "quotes that do not locate <= 15%" beside a metric threshold of "quotes locate >= 85%" — the same gate twice, satisfiable by exactly the arms that satisfy the metric | Measure — the degenerate strategy |
| 3 | The guardrail reads a column `quote_missing` that nothing in the plan produces; the recorded columns are `n_findings`, `quote_verify_rate`, `fix_dropin_rate`, `fix_offer_rate` | Measure — state the unit, list the columns |
| 4 | Those recorded columns are **fractions per case**, so passing one as a metric column name raises `TypeError: Need to pass bool-like values`. They need a callable, or a per-case boolean | Measure — boolean column or callable |
| 5 | `human='strength_human'` cannot work: strength is a per-*finding* label and the frame is one row per case, so the column has nowhere to live | Measure — human labels need a per-case column |
| 6 | Every starter fixture is marked `synthetic=true` by the plan's own rule, so the headline averages over an empty frame and reads `n/a`. The plan is aware there is no ground truth; it is not aware of the arithmetic that follows | Data — keep real cases in the set |
| 7 | Models are named only as "two model families" — no import path, no model ids, nothing that puts the module on the path | Models |
| 8 | Thresholds are set (85%, 80%, 75%) with no statement of what difference the case set can resolve; the starter set is roughly six fixtures | Measure — the hypothesis test |

Three things that plan does **better** than our command currently asks for, which should be folded
into `/oryxflow:eval-plan` as part of this work:

- **Mark each gate number runnable-now or blocked-on-a-key.** Its status note splits the metrics
  into those checkable against the document itself and those needing a human-labelled answer key,
  and says which half can run today. That is more useful than a flat gate table, and it is exactly
  the information someone needs to decide whether to start. Add a column to the plan template's
  `The table` section.
- **The metric must match production's own definition.** Its quote-verification normalizes
  whitespace and case, with a comment that this *must* match the tolerance the shipping client
  applies — otherwise the measured rate is a fiction in both directions. Generalise that into a
  stated rule: when production applies a tolerance, a threshold or a normalisation before acting on
  a field, the metric applies the identical one, and the plan says where that definition lives.
- **A reading order for the verdict.** Dead-flag warning first, then coverage, then the rate with
  its `n`, then the delta and the noise judgement. The library already emits the first of those
  (`EvalResult._dead_flags` — a metric identically 0% or 100% in every arm usually measures the
  harness rather than the model), so this is a docs gap, not a feature gap. It belongs in
  `docs/docs/llm-evals.md` under "Reading the verdict".

## Implementation

### 1. Stop the eval template mutating global state

In the plugin repo, `resources/template-eval/eval.py`:

- Delete the `HERE = pathlib.Path(__file__).resolve().parent` assignment and the
  `oryxflow.set_dir(HERE / 'data')` call, plus the comment above it.
- Change `CASES = ev.load_cases(HERE / 'cases.csv', inputs=agent.Inputs)` to
  `CASES = ev.load_cases('cases.csv', inputs=agent.Inputs)`.
- Drop the now-unused `import pathlib` if nothing else in the file uses it.
- Add one line to the module docstring: *"Run this from its own directory. Paths are relative, so a
  run from anywhere else fails immediately on cases.csv rather than quietly building a second
  cache."*

Leave `resources/template-eval/agent.py` alone except as described in step 3 — its
`ROOT = pathlib.Path(__file__).resolve().parents[2]` is correct and stays.

### 2. Make the scaffolded project installable

In the plugin repo:

- Add `resources/template-minimal/pyproject.toml` — minimal, setuptools, project name derived from
  the directory, `requires-python`, and `oryxflow` as the one dependency. Flat-layout module
  discovery so the existing `tasks.py` / `flow.py` / `cfg.py` are importable as top-level modules
  with no restructuring.
- `commands/init-project.md`: after copying the template, run `pip install -e .` in the project's
  interpreter, and report the command it ran. If the install fails, print it and stop — do not
  continue to a scaffold whose imports will not resolve.
- `commands/eval-init.md`, step 4 (currently "Check `oryxflow[evals]` is installed"): extend it to
  also probe the entry point named in the plan, `python -c "import <module>"`. On failure, print
  `pip install -e .` for this project's environment and STOP before the smoke run, with the
  explanation that the eval imports production code and the project is not currently importable.
- `docs/docs/claude-plugin/project-structure.md` in this repo: add `pyproject.toml` to the shown
  tree, and one short paragraph on why the project is a package — the imports resolve the same from
  a notebook, a test, a subdirectory and an eval, rather than only from the repo root. Keep the
  page's voice: what the reader gets, not how it is computed.

### 3. Prompts as files, versions as commits

New page, `docs/docs/llm-evals-prompts.md`, and a matching branch in the plan command.

The page covers:

- **The file shape.** One file per prompt, YAML front matter for metadata (name, description, the
  model it was written for, notes), markdown body for the prose. Show a complete short example.
  Name the `.prompty` convention as the thing this follows, so a reader can pick up its tooling if
  they want it.
- **Why not a YAML document containing the prompt** — indentation and chomping change the prompt
  silently, and long block scalars do not review as diffs.
- **Versions are commits.** An arm names a ref, not a filename; `ev.PromptArm(ref=..., globs=...)`
  is where the arm reads and the cache key that matches it, in one object. Say plainly that
  numbered files are the thing to avoid, and why.
- **Extracting a prompt out of Python.** The mechanical steps: move the string into
  `prompts/<name>.md`, load it at import in the production module, confirm the rendered text is
  byte-identical before and after, then point `code_version()` at `hash_files('prompts/*.md')`
  instead of the module. Note that until that extraction happens the arm must hash the *module*
  (`oryxflow.hash_files('myapp/extract.py')`), which works but recomputes on every unrelated edit
  to that file.
- **Which side of the line a prompt starts on.** The baseline arm always reads production. A
  candidate that has not shipped may live in the eval (`prompt_root` returns a directory, so an arm
  can return an eval-local path). Show both arms side by side.
- **Promotion.** After a candidate wins: copy it into production byte-exact, repoint the arm at
  production, re-run that one cell, and confirm the number reproduces. That re-run is what proves
  the prompt that shipped is the prompt that won — reformatting on the way in is the failure it
  catches.
- **Generator prompts are eval-owned forever.** A prompt used to build or harvest cases is not
  production behaviour and is never promoted.

Then in the plugin repo, `commands/eval-plan.md` Q3 (currently "What are the prompts - current,
proposed, and the function under test?"): add a branch for a prompt embedded in Python. Propose the
extraction as part of the eval work, state which side of the line each arm's prompt starts on, and
record the promotion step under `Judgement calls` so it is not forgotten after a win.

### 4. Scoring structured output

New section in `docs/docs/llm-evals.md`, after "When the thing you score is inside the output"
(which already introduces pooled callable metrics). It covers the case where the output is a *list
of items* and there is no scalar quality score:

- **Decompose into binary checks; do not invent a 1-5 scale.** This is current judge practice
  (binary checklists beat scales), and it is also what `ev.Metric` is shaped for.
- **Score the deterministic checks first, because they are free.** For a sectioning task: every
  section's text is a verbatim span of the source, the sections do not overlap, they are in order,
  and the fraction of the document assigned to some section. For an extraction task: the field
  parses, the assignee is one of the attendees, no duplicate items.
- **Then match, then judge what is left.** Where a reference exists, align predicted items to
  reference items and compute recall and precision over the matches, with field accuracy scored
  *conditional on a match*. Where it does not, a judge answers one binary question per item.
- **State plainly that recall is unmeasurable without a reference.** A judge can say whether an
  extracted item is supported by the source; it cannot say what was missed, because it never sees
  the missing thing. That decides whether hand-labelling is in scope, and it belongs in the plan's
  Data section.
- **Two worked metric blocks**, one per shape: a pooled per-item rate as a callable, and a
  recall/precision pair where the guardrail is the fabrication rate (because "emit everything" wins
  recall and dies on the guardrail).
- **The degenerate strategy for a sectioning eval**, spelled out because it is unusually
  instructive: one giant section is verbatim, covers 100%, does not overlap and is in order — it
  passes every deterministic check. The guardrail has to be granularity.

Add a second short section on **pairwise judging**, since it is the recommended method when there
is no ground truth and the library does not support it directly:

```python
r = ev.sweep(compare, cases=cases, order=['ab', 'ba'],
             metric=ev.Metric('candidate preferred', 'prefers_candidate'))
```

Explain that a candidate winning at *both* orders is a real preference, and the gap between the two
orders is the judge's position bias, measured rather than assumed.

### 5. Case provenance

In `docs/docs/llm-evals.md`, extend the `synthetic` bullet under "Three column names mean
something": the flag tracks where the *content* came from, not whether a model was involved. An LLM
that selects or reformats real material yields real cases; an LLM that writes the material yields
synthetic ones. Mirror one sentence of this into `commands/eval-cases.md` in the plugin repo, where
the harvesting instructions live.

## Files modified

**This repo**

| File | Change |
| --- | --- |
| `docs/docs/llm-evals-prompts.md` | NEW — prompt files, git as the version store, extraction, promotion |
| `docs/docs/llm-evals.md` | Scoring structured output; pairwise via an arm axis; `synthetic` provenance |
| `docs/docs/claude-plugin/project-structure.md` | `pyproject.toml` in the tree; why the project is a package |
| `mkdocs.yml` | New page in `nav` AND in the `llmstxt` sections list — both, or it is missing from `llms.txt` |

**Plugin repo (edit only; the maintainer commits it)**

| File | Change |
| --- | --- |
| `resources/template-eval/eval.py` | Remove `set_dir` and the `HERE` anchors; relative `cases.csv` |
| `resources/template-minimal/pyproject.toml` | NEW — makes the scaffolded project installable |
| `commands/init-project.md` | Run `pip install -e .` after copying; stop on failure |
| `commands/eval-init.md` | Probe that the plan's entry point imports; stop with the fix if not |
| `commands/eval-plan.md` | Q3 branch for a prompt embedded in Python, plus the promotion step |
| `commands/eval-cases.md` | One sentence on content provenance vs. model involvement |

## Verification

1. **Template no longer touches global state.**
   `grep -rn "set_dir" resources/template-eval/` returns nothing.
2. **A fresh project is importable.** Scaffold with `/oryxflow:init-project` into an empty
   directory, then from a *subdirectory* run `python -c "import tasks"`. It must succeed. Before
   this change it raises `ModuleNotFoundError`.
3. **An eval launched from the wrong directory fails loudly and free.** From the repo root, run
   `python evals/<name>/run_eval.py`. Expect `FileNotFoundError` naming `cases.csv`, before any
   confirmation prompt and before any call is billed.
4. **An eval launched correctly still works.** `cd evals/<name> && python run_eval.py --check`
   prints `<TaskName>: preflight OK.` and exits 0, and a second run of the same arm reports
   `cached N · new 0`.
5. **`eval-init` catches an uninstallable project.** In a project without `pip install -e .`, run
   `/oryxflow:eval-init` against a plan naming a project entry point. It must stop at step 4 with
   the install command, and must NOT reach the smoke run.
6. **Docs build clean.** `python scripts/build_docs.py --check` — the site builds, front matter
   validates, and the generated doc tests are unchanged. The only `--strict` warnings should remain
   the pre-existing griffe `**kwargs` ones in `oryxflow/targets/__init__.py`.
7. **Test baseline holds.** `python -m pytest tests/ -q` — currently 114 core plus 207
   evals/code_version. This plan changes no library code, so both numbers must be unchanged.
