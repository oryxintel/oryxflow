# Run log lineage: datasets read and written, with versions, on task events

## Context

The run log (`.oryxflow/events.jsonl`, `oryxflow/events.py`) is a custom, flat, append-only JSONL
stream. It holds more than task runs: code warnings and accepts, task log lines, export failures,
and a sibling fact library's events in the same envelope. We considered replacing it with
OpenLineage, the LF AI & Data standard for "what ran, what it read and wrote", and decided not to:

- OpenLineage models only the run lifecycle (START / COMPLETE / FAIL) with input and output
  datasets. The other event types have no counterpart, so a custom stream would remain anyway.
- OpenLineage nests everything under `run.facets` / `job.facets` / `inputs[].facets`, with
  `_producer` / `_schemaURL` on every facet. That makes `tail`, `grep` and `jq`, the agent-facing
  contract of the log, worse for no reader we have today. OpenLineage pays off only once a catalog
  (Marquez, DataHub, OpenMetadata, Atlan, Collibra, Alation all import it) ingests it.
- The log is append-only and complete, so OpenLineage can be produced later by a pure converter
  over the files, including backfilling old runs, as long as the log holds every piece of
  information OpenLineage needs.

It did not. `task_ran` recorded only the dependencies' task ids (`deps`): not the files a task
wrote or read, and not the task's own `output_id` (the per-materialization id stored in its
code-status record and folded into downstream `_dep_state`). A consumer could not answer "which
build of the producer's output fed this?", and a project folder rename that broke consumers'
imports could not be traced from the log.

### Design decisions

- **Keep the custom stream; describe datasets in OpenLineage vocabulary, kept flat.** Exporting
  is a fixed field-by-field mapping. What matters is that the information is present and
  unambiguous, and the exact OpenLineage key names make the mapping trivial. Nesting the facets
  in the log would only cost readability. Each dataset is one flat entry
  `{namespace, name, datasetVersion}`:
  - `namespace`: the project folder's name that owns the dataset (the parent of the data dir;
    for an imported task, the folder of the `tasks_export.py` it was loaded from);
  - `name`: the file path, posix, relative to that project's root (absolute if outside it).
    Root-relative, never machine paths, because synced folders mount at different places;
  - `datasetVersion`: the `output_id` of that materialization (`None` when the task's code is
    untracked, or when the record write failed, since nothing downstream folds it then).
- **`task_ran` carries `project` (the job namespace), `outputs` and `inputs`;** `task_failed`
  carries `project` and `inputs` (nothing was written).
  - There is one entry per output FILE, so a multi-`persists` task gives several, all with the
    same `datasetVersion`.
  - Inputs also carry the dependency's `task_id` and `family`, our task identity for joining to
    the producer's events.
  - A dependency without file outputs (in-memory, DB) still yields one entry with `name: None`,
    so the edge is never lost.
- **The join key:** an input's `datasetVersion` comes from `TaskData._dep_ids()`, so it equals the
  `datasetVersion` in the dependency's own `task_ran` outputs, including the producer's when the
  dependency came through `FlowImport`. For an untracked or external dependency without a record
  it is the existing `ext:<task_id>:<mtime>` form.
- **One name per thing (no backward compatibility needed; nothing read these fields yet):**
  - `deps` is removed (superseded by `inputs[].task_id`);
  - there is no top-level `output_id` (it is `outputs[].datasetVersion`);
  - code and code-status records keep calling the value `output_id`, and the log uses
    OpenLineage's name for it.
- **OpenLineage mapping, for the later converter:**
  - `type` → eventType (task_ran = COMPLETE, task_failed = FAIL; START at `ts - duration_s`);
  - job namespace = `project`, job name = `family`;
  - run id = uuid5(`run_id`, `task_id`), parent run = `run_id` (`ParentRunFacet`);
  - datasets are copied as they are, `datasetVersion` → `DatasetVersionDatasetFacet`;
  - `params` → `ExecutionParametersRunFacet`;
  - `git_sha` → `SourceCodeLocationJobFacet.version`;
  - `oryxflow_version` → `ProcessingEngineRunFacet`;
  - `error` → `ErrorMessageRunFacet`;
  - `fingerprint` / `code_version` / `source_hashes` → an `oryxflow_build` custom facet.

  Schema facets need the planned export contracts. They are not in the log.
- **Never fail a run.** `_lineage` catches everything and returns `{}`. It is skipped when
  `settings.events` is False.
- **Rejected:**
  - nested OpenLineage facets in the log (unreadable for agents);
  - absolute paths (machine-specific);
  - a separate `lineage` event type (one `task_ran` per run keeps "what happened" in one line).

## Implementation

1. `oryxflow/core.py`, after `_git_info()`: add `_project_root(task)`, `_output_paths(task,
   root)`, `_datasets(task, root, version, extra=None)` and `_lineage(task, version=None,
   outputs=True)`.
2. `oryxflow/core.py` `build()`, task-complete path:
   - generate `output_id` before `put_record`, and set it to `None` if the write fails;
   - add `**_lineage(task, version=output_id)` (guarded by `settings.events`) to the `task_ran`
     payload;
   - remove `deps`.
3. `oryxflow/core.py` `build()._emit_failed`: add `**_lineage(task, outputs=False)`.
4. `oryxflow/export.py` `latest()` docstring, `docs/docs/collaborate.md`: `deps` → `inputs`.

## Files modified

- `oryxflow/core.py`: the helpers and the two payloads; `deps` removed.
- `oryxflow/export.py`: a docstring.
- `tests/test_runlog_lineage.py`: new tests:
  - the same-project version join;
  - multi-output deps;
  - untracked → `None`;
  - the `FlowImport` join to the producer;
  - a failed task has inputs but no outputs;
  - a lineage failure never fails the build.
- `CHANGELOG.md`: an Unreleased "Added" bullet (the unreleased `deps` bullet removed).
- `docs/docs/managing-workflows.md`, `docs/docs/collaborate.md`.

## Verification

```bash
python -m pytest tests/test_runlog_lineage.py -q
python -m pytest tests -q
jq 'select(.type=="task_ran") | {family, outputs, inputs}' .oryxflow/events.jsonl
```

## Implementation notes (divergences from the first version of this plan)

The first version (same branch, never committed) logged a top-level `output_id`, `outputs` as a
list of paths, and inputs as `{task_id, family, project, paths, output_id}`, next to `deps`.
Before committing it was reworked to the flat OpenLineage dataset shape above. One name per
thing: there is a version per output file, and `deps` was dropped.
