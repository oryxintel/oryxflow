# System docs (for coding agents)

Internal architecture notes for working *on* oryxflow (not user-facing). These complement
`CLAUDE.md` ("Architecture notes that bite") and the executable design specs in `docs/todo/`.
The split:

- **`docs/todo/<date>-<area>-<topic>.md`** — design *decisions* and the plan that produced a
  change (the WHY, decisions rejected, ordered implementation steps). Written before the work.
- **`docs/system/*.md`** (here) — the *resulting* mechanism as it stands now, so a fresh session
  can understand a subsystem without replaying its plan. Reference files + line numbers; never
  paste code (it drifts — point at the source of truth).

## Index

- [targets.md](targets.md) — task identity, the completion check, on-disk path layout, and
  **luigi on-disk compatibility** (are old outputs still seen as complete?).
- [workflow.md](workflow.md) — the **completion cascade** (a task is incomplete if anything
  upstream is), what `reset` / `reset_downstream` actually invalidate, and how `WorkflowMulti`
  fans those out across flows.

## Conventions for these docs

- Cite `path:line` and the enclosing function name (line numbers drift; names survive).
- State the contract, not the implementation. If behavior is load-bearing for a downstream
  repo (e.g. d6tflow2) or for backward compat, say so explicitly.
- When a mechanism changes, update the relevant file here in the same commit.
