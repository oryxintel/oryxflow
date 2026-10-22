# Exports and release gates

Status: design note. The documented pattern (below, "Now") ships with this note; the library
change ("Later") is deferred, for the reasons in Design decisions.

## Context

A producer project often releases data in three steps: `X -> XChecks -> XPublish`. The checks
task fails the run when the data is wrong, and the publish task (a) requires the checks and
(b) stamps columns onto the frames it writes (`datetime_run`, a vendor vintage), then copies
them to a shared folder. Consumers that read the shared folder only ever see checked, stamped
data.

`export = True` (26.10.22) exports a task's *own* output. Marking `X` exported lets a consumer
`FlowImport` it and read `X`'s file directly, so:

- a consumer can read an `X` whose checks failed: the checks run after `X` has saved, and a
  failing check stops the run but does not remove `X`'s output;
- the publish-time stamps are missing, so a consumer that asserts on them breaks;
- a recompute of `X` with the same parameters overwrites the file in place, so even a later
  failed check leaves the new, unchecked data where consumers read it.

Real case (sanitized): a data-store project exports its market returns and scorecard tasks; the
scorecard's checks task and the returns' one-refresh assertion live in separate gate tasks, and
the geography project's contract file is the linked table plus stamps written by its publish
task behind a check. The producer could not switch its consumers to `FlowImport` without losing
the gates.

### Design decisions

1. **Now: export the gated task, not the raw one.** The pattern needs no library change: a
   release task requires `X` and `XChecks`, adds the stamps, and saves through oryxflow (its
   output lives in `data/` like any task); it carries `export = True`. The shared-folder copy,
   if still wanted for people and tools, reads the release task's output. Because the release
   task only saves after its inputs (including the checks) succeeded, consumers never see data
   that failed the checks, and they get the stamps. Documented in `docs/docs/collaborate.md`
   ("Export the checked result").

2. **Rejected for now: `export_after = [GateTask]` filtering `built`.** `built` would list only
   value sets whose gate output exists. That hides a failed build from consumers that pick from
   `built` (`latest()`), but not from a consumer already bound to that value set: `X` is
   overwritten in place before its checks run, so the consumer reads unchecked data on its next
   run either way. Filtering `built` gives a false sense of gating.

3. **Rejected for now: consumer-side gate (the imported stub requires the gate's output).** The
   stub's `complete()` would also check the gate task's output. Same in-place overwrite problem
   for the window between `X` saving and the checks failing, plus it exports a second class per
   gate and couples the consumer to the producer's internal task graph.

4. **Later, if the pattern proves too costly: an atomic release.** The real fix for in-place
   overwrite is that `X` writes to a staging location and the gate promotes it. That is a
   storage-model change (two locations per task, promotion as a task), far beyond a flag. Revisit
   only if several producers need the raw task exported AND gated.

## Implementation

Now: documentation only (`docs/docs/collaborate.md`, "Export the checked result"). The plugin
skill's sharing section should say the same (owned by the plugin repo).

Recipe to show producers:

```python
@oryxflow.requires({'data': MarketScore, 'checks': MarketScoreChecks})
class MarketScoreRelease(oryxflow.tasks.TaskPqPandas):
    """Market scores, checked. Columns: ..., datetime_run."""
    export = True

    def run(self):
        df = self.inputLoad(task='data')
        self.save(df.assign(datetime_run=RUN_STAMP))
```

`MarketScoreChecks` raises on bad data, so `MarketScoreRelease` never saves unchecked data. A stamp
that must change per run belongs in a parameter (it is part of the output's identity), not in
`datetime.now()` inside `run()`.

## Files modified

- `docs/docs/collaborate.md` -- "Export the checked result" bullet.
- `docs/todo/20261022-engine-export-gates.md` -- this note.

## Verification

Nothing to run for the library. A producer adopting the pattern verifies: make the checks fail
on purpose, run, and confirm the release task's output did not change and `tasks_export.py`'s
`built` did not gain the value set.
