# Roadmap: AI-assistant experience for oryxflow

Lightweight backlog of ideas for making oryxflow easier to work with via an AI coding
assistant (Claude Code). Unlike the dated `*-sys-*.md` plans here, these are not yet
scoped specs - just captured intent so they are not re-discovered each session.

Context: came out of an audit of the oryxflow repo + the separate `oryxflow-claude-plugin`
repo. The quick wins from that audit (idiom consistency in `docs/`, a Public API map in
`CLAUDE.md`, docs for `runLoad`/cloud storage) are already done. The items below were
deferred.

## Deferred ideas

- **Explore subagent (plugin-side).** The plugin's opt-in "explore the data" deep dive is
  read-heavy fan-out (profile sources, inspect schema, write `docs/oryxflow-data.md`). It
  pollutes the main context. A dedicated subagent that returns only the findings would keep
  the main thread clean. Lives in the plugin repo (`skills/oryxflow`), not the library.

- **Error decoder (library docs or plugin).** A single consolidated "common error -> cause ->
  fix" reference. Candidates seen repeatedly:
  - task edited but not reset -> shows under "complete ones were encountered", silently reuses
    stale output (reset before rerun).
  - `KeyError` on a column/key after a code change -> a *different* parameter variant of the
    task is still cached with the old schema (reset that variant / `reset=True`).
  - locked Excel file on Windows -> permission/sharing error; close the file, do not work around.
  - "Can not schedule non-task" -> passed a class where an instance was expected (or vice versa).
  `docs/source/run.rst` already has a "Debugging Failures" section this could extend.

## Deferred cleanup (found during the audit, low priority)

- **`docs/example.ipynb` / `docs/example-functional.ipynb`** — the stale `LuigiRunResult` /
  "Luigi Execution Summary" cell *outputs* have been cleared (the cells now carry no output
  rather than wrong output). The old `self.input().load()` idiom in `example.ipynb` has been
  updated to `self.inputLoad()`. Still open: both notebooks need a full re-execution to
  regenerate fresh (correct) outputs.

- **`docs/source/run_legacy.rst`** — RESOLVED: retired/deleted. It was an unlinked (not in the
  toctree) near-duplicate of `run.rst`, which is a conceptual superset; the one pointer to it in
  `run.rst` was removed with the deletion.
