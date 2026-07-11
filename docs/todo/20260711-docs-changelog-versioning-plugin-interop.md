# Documentation: changelog, version introspection, and Claude-plugin interop

## Context

oryxflow is now being published as human docs on Read the Docs (Sphinx, `docs/source`,
`.readthedocs.yaml` added 2026-07-11) **and** is driven by an AI coding agent through a
separate Claude Code plugin (`oryxflow-claude-plugin`, its own repo). That makes the docs
"AI-native": every doc surface has two readers — a human and a coding agent — and the goal
is that the *same* artifact serves both.

Three consumers, concretely:

1. **Human using the library directly** — reads RTD.
2. **AI agent working with the library** — after `pip install -U oryxflow` hits an
   `AttributeError`/`ImportError`/`TypeError` and needs to tell "did the library change under
   me?" from "is this my bug?". Its stated workflow (from a real consuming-agent review): *grep
   the changelog for the failing symbol, read entries from the installed version forward,
   prioritize breaking ones, confirm the running version with `oryxflow.__version__`.*
3. **AI agent working through the plugin** — same as (2) plus it needs to know where the
   library ends and the plugin begins.

**Gaps this exposed (all in THIS repo):**

- **No changelog at all.** No `CHANGELOG.md`, no git tags. An agent diagnosing a regression
  after an upgrade has literally nothing to read. This is the priority gap.
- **`oryxflow.__version__` does not resolve.** It is not defined in `oryxflow/__init__.py`; only
  `importlib.metadata.version("oryxflow")` returns `26.6.6`. So the instruction "read the range
  between installed and target version" is unexecutable — the agent can't read the installed
  version by the obvious means.
- **No `project_urls` in `setup.py`.** PyPI and RTD therefore expose no "Changelog" /
  "Documentation" link, so neither a human nor an agent starting from the package can find the
  changelog.
- **RTD has no page connecting the library to the plugin.** A human arriving at RTD is shown only
  the write-it-yourself path; there is no signpost to the faster AI-native path (the plugin), and
  no orientation for the plugin's own agent about where the library sits.

The version literal `26.6.6` currently lives only in `setup.py`.

### Design decisions

Confirmed with the user and a consuming-agent review; do not relitigate:

- **One `CHANGELOG.md` per repo, no second machine format.** Keep-a-Changelog base
  (reverse-chronological, `## [Unreleased]` on top, calver `YY.M.D` headings). A parallel
  JSON/YAML changelog would drift out of sync with the prose the day someone edits one and not the
  other. **Structure is the machine-readability** — the single file is greppable *because* of the
  conventions below, not because of a second format.

- **Three load-bearing tokens, enforced, not decorative:**
  - **`BREAKING:`** — a literal token beginning any breaking bullet, so an agent can grep for it.
    "Prioritize breaking changes" only works if "breaking" is a grep target, not a vibe.
  - **`Migration:`** — every `BREAKING:` bullet carries a same-entry old→new fix
    (`import d6tflow` → `import oryxflow`), so the agent can auto-remediate.
  - **Backticked symbols** — name the actual API symbol (`` `RunResult.summary_text` -> `summary()` ``),
    never prose like "reworked the run API". Agents diagnose from a symbol in a traceback; they
    grep the changelog for that symbol and must land on the entry.
  These rot back to human-only prose exactly at the entries that matter most, so a CI check keeps
  them honest (Implementation step 6).

- **Authority split (stated in both repos, decided here):** the **library `CHANGELOG.md` is the
  source of truth for API/behavior**; the plugin changelog covers skill/guidance changes and the
  compatibility contract. **When they disagree about behavior, the library wins.** This one
  sentence stops the plugin's agent from debugging a phantom when the plugin has simply run ahead
  of the library (it has now: plugin `26.7.3`, library `26.6.6`).

- **Single-source the version via `importlib.metadata`.** Expose `oryxflow.__version__` computed
  from installed metadata (which derives from `setup.py`), so there is still exactly one literal.
  Rejected: hardcoding the version a second time in `__init__.py` (immediate drift).

- **Cross-repo links use `raw.githubusercontent.com` for agents, `blob` for humans.** An agent must
  fetch (files are never auto-in-context); the raw URL returns clean markdown, the blob URL returns
  HTML chrome. Pointers aimed at the agent use raw; human-facing prose uses blob.

- **The RTD `claude-plugin` page is a thin front door, not a re-teach.** It advertises the plugin
  and links out; it does **not** re-explain the task model. Re-teaching would create a third place
  (after the core RTD pages and the plugin skill files) that explains `requires`/params and would
  drift. Library facts live in the core RTD pages; project-building craft lives once in the plugin.

- **Render the single `CHANGELOG.md` on RTD via MyST include** (not a hand-copied page), so RTD is
  a *view* of the one file, never a fork of it.

## Implementation

### 1. Create `CHANGELOG.md` at the repo root

Seed with the convention header (which doubles as the contract for future editors) plus an
`Unreleased` section and the historical package rename as the first real `BREAKING:` entry (the
`d6tflow` → `oryxflow` rename is genuinely breaking and downstream projects still `import
d6tflow`). Exact starting content:

```markdown
# Changelog

All notable changes to **oryxflow** are recorded here. This file is read by humans *and* by AI
coding agents diagnosing regressions after an upgrade, so the format is load-bearing:

- Newest first. One `## [version] - YYYY-MM-DD` heading per release; version is calver `YY.M.D`
  matching `setup.py` / `oryxflow.__version__`. Unreleased work goes under `## [Unreleased]`.
- Group bullets under `### Added` / `### Changed` / `### Deprecated` / `### Removed` /
  `### Fixed` / `### Security` (Keep a Changelog: https://keepachangelog.com/).
- **Every breaking change is a bullet that STARTS with the literal token `BREAKING:`** and carries
  a same-bullet `Migration:` clause with the old→new fix.
- **Name the actual symbol in backticks** (`` `Task.persist` ``, `` `RunResult.summary()` ``), never
  prose. Agents grep this file for the symbol in their traceback.

## [Unreleased]

## [26.6.6] - 2026-06-06
### Changed
- BREAKING: package renamed `d6tflow` -> `oryxflow`. Migration: replace `import d6tflow` with
  `import oryxflow` (and `from d6tflow...` -> `from oryxflow...`); the public API names are
  otherwise unchanged.
```

(Backfill any other known-behavioral changes since the last public `d6tflow` release under
`[26.6.6]` before committing; going forward, every PR that touches a public symbol adds a bullet
to `[Unreleased]`.)

### 2. Expose `oryxflow.__version__`

At the top of `oryxflow/__init__.py`, before the public re-exports:

```python
from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PkgNotFound
try:
    __version__ = _pkg_version("oryxflow")
except _PkgNotFound:          # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
```

This keeps the single version literal in `setup.py` (metadata derives from it) while making
`oryxflow.__version__` resolve for any installed copy, including `pip install -e .`.

### 3. `setup.py` — add `project_urls` and ship the changelog

Add to the `setup(...)` call:

```python
    project_urls={
        'Documentation': 'https://oryxflow.readthedocs.io/',
        'Changelog': 'https://oryxflow.readthedocs.io/en/stable/changelog.html',
        'Source': 'https://github.com/oryxintel/oryxflow',
    },
```

Create `MANIFEST.in` at the repo root so the changelog ships in the sdist:

```
include CHANGELOG.md
include README.md
```

(Note the honest limitation: a file at the wheel root is not reliably readable by import path, so
the *canonical* agent fetch target is the RTD URL / the raw GitHub URL, not "the installed package
dir". For an editable checkout the on-disk `CHANGELOG.md` + `git log` are the live equivalent. The
plugin pointer line, defined in the plugin plan, names both.)

### 4. Render the changelog on RTD (single-source via MyST)

- Add `myst-parser` to `docs/requirements.txt`.
- In `docs/source/conf.py`: add `'myst_parser'` to `extensions`, and broaden the suffix map:
  ```python
  extensions = [ ..., 'myst_parser']
  source_suffix = {'.rst': 'restructuredtext', '.md': 'markdown'}
  ```
- Create `docs/source/changelog.md` that includes the root file (no copy):
  ````markdown
  # Changelog

  ```{include} ../../CHANGELOG.md
  :start-line: 1
  ```
  ````
  (`:start-line: 1` drops the duplicate top `# Changelog` heading from the included file; verify
  the rendered page has a single H1.)
- Add `changelog` to the `index.rst` toctree (end of the User Guide list is fine).

### 5. Add the thin `claude-plugin` front-door page

Create `docs/source/claude-plugin.rst`:

```rst
Using oryxflow with Claude Code
===============================================

Prefer to build oryxflow projects with an AI coding assistant instead of writing the task
wiring yourself? There is an official **Claude Code plugin**, ``oryxflow``, that scaffolds
projects, wires tasks with ``@oryxflow.requires``, and follows the house conventions
automatically.

Install
-----------------------------------------------------------

.. code-block:: text

    /plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
    /plugin install oryxflow@oryxflow

Commands
-----------------------------------------------------------

- ``/oryxflow:init-project`` - scaffold a runnable project in an empty directory.
- ``/oryxflow:init-gitlfs`` - put ``data/`` under Git LFS.
- ``/oryxflow:update-project`` - update an older project's scaffold floor.
- ``/oryxflow:check-standards`` - check names, style, and docstrings against the house standards.

Once installed, the ``oryxflow`` skill auto-activates whenever you work in a oryxflow project
(editing ``tasks.py`` / ``flow.py`` / ``run.py`` / ``cfg.py`` / ``flow_params.py``).

Learn more
-----------------------------------------------------------

- Plugin repository and issues: https://github.com/oryxintel/oryxflow-claude-plugin
- House conventions the plugin follows:
  https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/skills/oryxflow/conventions.md
- Plugin changelog:
  https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/docs/CHANGELOG.md

This library is the engine the plugin drives; the full API is documented throughout the rest of
these docs.
```

Add `claude-plugin` to the `index.rst` toctree **right after `quickstart`** so evaluating readers
see the fast path early.

### 6. CI check to keep the changelog honest (recommended)

Add `scripts/check_changelog.py` — a lightweight, dependency-free check run in CI and/or as a
pre-commit hook. It should assert, over `CHANGELOG.md`:

- every non-Unreleased `## [x.y.z]` heading has a ` - YYYY-MM-DD` date;
- every bullet beginning `BREAKING:` also contains `Migration:` (same bullet/entry);
- the top `## [Unreleased]` (or newest version) is non-empty on a PR that changes `oryxflow/**.py`
  public surface (heuristic: fail if `oryxflow/` changed but `CHANGELOG.md` did not).

Wire it into the existing test/CI invocation. Mark advisory at first if you want a soft rollout,
then make it blocking.

## Files modified

- `CHANGELOG.md` — **new**; seeded with convention header + `[Unreleased]` + `[26.6.6]` rename entry.
- `MANIFEST.in` — **new**; ship `CHANGELOG.md`/`README.md` in the sdist.
- `oryxflow/__init__.py` — add `__version__` via `importlib.metadata`.
- `setup.py` — add `project_urls` (Documentation / Changelog / Source).
- `docs/requirements.txt` — add `myst-parser`.
- `docs/source/conf.py` — register `myst_parser`; broaden `source_suffix` to `.rst` + `.md`.
- `docs/source/changelog.md` — **new**; MyST `{include}` of root `CHANGELOG.md`.
- `docs/source/claude-plugin.rst` — **new**; thin front-door page.
- `docs/source/index.rst` — toctree: add `claude-plugin` (after quickstart) and `changelog`.
- `scripts/check_changelog.py` — **new** (recommended); CI/pre-commit lint of the changelog format.

## Verification

- `python -c "import oryxflow; print(oryxflow.__version__)"` prints `26.6.6` (installed / editable),
  not an `AttributeError`.
- `grep -n "BREAKING:" CHANGELOG.md` returns the rename entry; the same line contains `Migration:`.
- `python -m sphinx -b html docs/source docs/_build/html` → `build succeeded`, no new project
  warnings (baseline: build already clean as of 2026-07-11), and the built site has a **Changelog**
  page (single H1) and a **Using oryxflow with Claude Code** page.
- `python scripts/check_changelog.py` exits 0 on the seeded file; flips to non-zero if you delete
  the `Migration:` clause from the rename entry (proves the check bites).
- After a build/install, `pip show oryxflow` (or the PyPI page) lists the Changelog/Documentation
  URLs.
- Engine test baseline unchanged — these are docs/packaging changes only:
  ```
  python -m pytest tests/test_main.py tests/test_workflow.py \
      tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
  ```
  expect **73 passing**.
```

## Implementation notes (divergences from the plan as built)

Built 2026-07-11. All ten files landed as specified, with these deltas:

- **`MANIFEST.in` already existed** (it shipped `README.md` + `LICENSE`). Rather than overwrite
  it with the plan's two-line version, `include CHANGELOG.md` was prepended and the existing
  `LICENSE` line kept. Final file: `CHANGELOG.md`, `README.md`, `LICENSE`.

- **Sphinx pin bumped past the plan.** The plan left `docs/requirements.txt` at `sphinx>=7,<9`,
  but the current `myst-parser` (5.1.0) requires Sphinx ≥ 8, so installing it pulled Sphinx 9.1.0.
  At the user's direction ("move to the latest version, we originally published a long time ago")
  the pins were modernized to `sphinx>=9` / `sphinx_rtd_theme>=3` / `myst-parser>=5`. The HTML
  build succeeds clean on Sphinx 9.1.0 + sphinx_rtd_theme 3.1.0 (no new warnings).

- **Engine test baseline is now 86 passing, not 73.** The suite grew since the plan was written;
  the four-file invocation reports **86 passed** (3 benign warnings). Docs/packaging changes here
  don't touch it. Treat 86 as the new baseline.

- `scripts/check_changelog.py` was built as specified for the two hard checks (date on every
  released heading; `Migration:` on every `BREAKING:` bullet). The third, "PR changed
  `oryxflow/**` but not `CHANGELOG.md`" heuristic is scaffolded as an opt-in `--require-entry`
  note in the docstring but not wired to git diff — left for whoever wires the CI step, since it
  needs the CI's changed-file list, not the working tree.
