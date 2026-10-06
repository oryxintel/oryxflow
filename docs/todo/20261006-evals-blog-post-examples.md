# Blog posts: worked examples of LLM evals with oryxflow.evals

## Context

The three published eval posts (`cheap-durable-llm-evals`, `llm-eval-results-retention`,
`llm-evals-are-a-parameter-sweep`) argue the WHY and show the older hand-written pattern
(`EvalRun(TaskPqPandas)`, `pip install oryxflow pydantic-evals`), each with a note that it "now
ships as `oryxflow.evals`". None shows the shipped API end to end, and none is an example a reader
can copy for their own surface.

Since then a downstream consumer project (a writing product with an intent router, a document
reviewer, an outline writer and follow-up suggestions) has produced five hand-built evals and ~25
throwaway probe scripts. Together they are a catalogue of what people actually try to measure,
and of the mistakes they make doing it. Each post below is one of those shapes, sanitized: keep
the SHAPE of the case (the arms, the metric, the failure it exhibits), drop the identity. Never
name the product, its users, or quote customer content.

Prerequisite for all of them: `oryxflow.evals` released to PyPI and the `LLM evals` docs section
deployed (both are currently unreleased; the docs pages 404 on docs.oryxflow.dev). Each post links
the quickstart and the plugin's `/oryxflow:eval-plan` flow.

## Candidate posts

Ordered by how often the shape came up and how much the post teaches.

1. **From a throwaway probe to an eval you can re-run.** The commonest artifact was a gitignored
   `tmp/probe_*.py`: inline cases, an A/B arm, a pass bar, printed output, deleted after the
   decision - so nothing re-checked the surface when it changed again two weeks later. Show the same
   probe as a one-file `ev.sweep` under `evals/<name>/`: same length, cached, results committed.
   Hook: "your probe answered the question once; the next prompt change asks it again."
2. **Restore the old prompt from git, do not string-patch it.** Arms built by `.replace()` /
   regex-strip / appended clauses on the live render, versus `ev.git_tree(ref, paths)`. Story: two
   of four template changes were deletions, and the replacement baseline silently reconstructed
   nothing. When a string patch IS right: an unshipped probe arm (`ev.Variant`, raises when its
   anchor is gone).
3. **"Did it act?" scores 100% for a prompt that always acts.** The guardrail and the coverage
   metric, with the follow-up-suggestions numbers shape: quality 31% -> 100% only meant something
   because yield moved 1.62 -> 2.42 in the same direction, and control false-actions stayed flat.
4. **Your bar was wrong, not your prompt.** Absolute pass bars (">= 9/12") set before any run,
   missed, then quietly rewritten. The lesson the consumer wrote down: a bar unreachable in the
   CONTROL arm measures the model, not the change - gate on "B >= A" and read the paired interval.
   At n = 3-4 several "misses" were noise; show the bootstrap interval spanning zero.
5. **A classifier flag needs a confusion matrix, not a judge.** Router/intent flags with an
   asymmetric gate ("zero false negatives; accuracy is informational"). Closed label set ->
   `EqualsExpected`, per-class rates, held-out cases whose text is embedded as few-shot examples.
   Follow-on: adding one field to a router schema shifts the OTHER flags - replay the previous
   boundary sets as a regression arm.
6. **An unvalidated LLM judge shrinks the effect you are measuring.** Judge from a different model
   family, binary questions over a 1-5 scale, ~100 human labels, kappa not raw agreement, the
   Rogan-Gladen correction (and when it refuses).
7. **Planted defects and the model-authored-label trap.** Review-style evals plant cues in a
   fixture and score recall by keyword. Metrics keyed on labels the MODEL writes (rule names,
   strengths) measure the model's naming, not its findings - match on the quoted span instead.
   Plus: "a fixture that names a state must actually be in that state", learned twice.
8. **Prompt x model is one sweep.** The same arms on two providers, a model id pinned and folded
   into `code_version()`, per-model results without a second harness.
9. **Multi-turn replay where arms diverge.** Each turn is sent the state the previous turn
   returned, so arms drift apart - per-turn and per-episode metrics. (Check library support before
   writing; this may need a feature first.)
10. **Read the outputs, not just the number.** Error analysis before the metric (open-code 20-50
    outputs, count the modes), and a side-by-side A/B output file per case for the human pass.
    Several probes answered their question "by eye"; show how that sits beside a verdict instead of
    replacing it.

## Also

- Update the three existing posts' code to the shipped API (or point their notes at the new posts)
  once `oryxflow.evals` is on PyPI.
- Every post's code must run against the released version; paste the real verdict output, not an
  illustration.
