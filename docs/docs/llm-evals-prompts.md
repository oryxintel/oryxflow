---
title: Prompt files
description: Keep each prompt in its own markdown file with YAML front matter, and let git hold the versions. How to lift a prompt out of a Python string, which arm reads which copy, and the re-run that proves the prompt that shipped is the prompt that won.
---

# Prompt files

Your prompt is a string constant inside `myapp/extract.py`, and you have just found out that
three things you wanted are not available to you: there is no file for `code_version()` to hash,
no directory for a baseline arm to check out of git, and no way to look at the prompt without
importing the application.

All three go away when the prompt is a file. This page is how to get it there, how versions work
once it is, and the one step people skip after a candidate wins.

## The file

One prompt, one file. YAML front matter carries the metadata; the markdown body *is* the prompt:

```markdown title="prompts/extract.md"
---
name: extract-sections
description: Split an unstructured document into ordered, verbatim sections.
model: <the pinned model id this was written against>
notes: Written for long meeting transcripts. Short inputs were never tested.
---

Split the document below into sections.

Rules:

- Each section's text must be copied verbatim from the document.
- Sections must be in the order they appear.
- Do not overlap sections.

Return one JSON object per section, with `heading` and `text`.
```

This is the [`.prompty` file format](https://prompty.ai/core-concepts/file-format/) — front
matter plus a markdown body — so if you later want its editor tooling or its runtime, your files
already fit. Nothing here depends on that; it is a plain text file you can read, diff and grep.

Load it with one line. [`python-frontmatter`](https://pypi.org/project/python-frontmatter/)
splits the two halves for you:

```python
import frontmatter

PROMPT = frontmatter.load('prompts/extract.md').content
```

If you would rather not add a dependency, split on the closing `---` yourself — just make sure
whatever you write strips the front matter, because a prompt that ships its own metadata as
instructions is a real and confusing bug.

### Why not put the prompt inside the YAML

The obvious alternative is one YAML document with the prompt as a value. Don't: the prompt then
depends on whitespace you cannot see.

```yaml title="what you wrote"
prompt: |
  Extract every section.
  Return them in order.
```

```yaml title="what someone's editor did to it"
prompt: |
  Extract every section.
    Return them in order.
```

The second one is still valid YAML, still loads, still runs. The prompt is now
`'Extract every section.\n  Return them in order.\n'` — two spaces the model sees and you do not.
Change the `|` to `>` and YAML folds the newlines into spaces instead, so your two instructions
become one line. Write `|-` and the trailing newline disappears. Each of those is a single
character, sometimes thirty lines above the text it changes, and none of them is an error.

Diffs make it worse rather than better. A change inside a long block scalar shows up as an
indented fragment with no surrounding prose, so the reviewer approving your prompt change cannot
actually read the prompt. Front matter keeps the metadata in YAML, where it is short and
structured, and keeps the prose out of it, where it reviews like prose.

## Moving a prompt out of Python

Four steps, and the third is the one that matters.

**1. Cut the string into a file.** Straight across, no tidying — resist the urge to fix the
indentation or reflow a line while you are in there.

```python title="before — myapp/extract.py"
EXTRACT_PROMPT = """Split the document below into sections.

Rules:

- Each section's text must be copied verbatim from the document.
"""
```

```python title="after — myapp/extract.py"
import frontmatter
from pathlib import Path

PROMPTS = Path(__file__).parent / 'prompts'
EXTRACT_PROMPT = frontmatter.load(PROMPTS / 'extract.md').content
```

**2. Load it at import**, as above, so callers see the same name they always did and nothing
downstream changes.

**3. Confirm the rendered text is byte-identical.** Before and after, print
`repr(EXTRACT_PROMPT)` and compare. Front matter stripping, a trailing newline and an editor that
trims whitespace on save each change the bytes the model sees, and a prompt that changed during a
move that was supposed to change nothing will show up later as an unexplained score movement.

**4. Point the cache key at the files.** In the function form that is `watch=`:

```python
r = ev.sweep(run_case, cases=cases, watch='myapp/prompts/*.md',
             prompt_version=['live', 'candidate'],
             metric=ev.Metric('sections verbatim', 'all_verbatim'))
```

and in the class form it is `code_version()` returning `oryxflow.hash_files('myapp/prompts/*.md',
root=ROOT)`. Either way, editing a prompt now re-runs the arms that read it and nothing else.

### Until you extract it

You do not have to extract before you can run anything. While the prompt is still a string inside
the module, hash the **module**:

```python
watch=lambda: oryxflow.hash_files('myapp/extract.py', root=ROOT)
```

That is correct — the prompt cannot change without the file changing — and it is honest about
what it costs: every unrelated edit to `extract.py` also invalidates the cell, so you pay for the
whole arm again because you renamed a variable. It also leaves the baseline arm out of reach,
because there is no prompt directory for git to hand back. Treat it as the state you run one
sweep from, not the state you stay in.

## Versions are commits, not filenames

Once the prompt is a file, the temptation is `extract-v1.md`, `extract-v2.md`, `extract-v3.md`
side by side. Resist it. You already have a version store, and an arm already names a version by
ref:

```python
ARMS = {
    'live':     ev.PromptArm(globs=['myapp/prompts/*.md'], root=ROOT, subdir='myapp/prompts'),
    'baseline': ev.PromptArm(ref='v2.1', paths=['myapp/prompts'], root=ROOT,
                             subdir='myapp/prompts'),
}

# where the arm reads:     ARMS[arm].dir()
# what it is cached on:    ARMS[arm].code_version()
```

`ev.PromptArm` is the directory the arm reads **and** the cache key that matches it, in one
object, so the two cannot drift apart. A working-tree arm takes `globs=` — the files whose content
is its key. A `ref=` arm takes `paths=` — what to materialize from that commit — and is keyed on
the resolved commit sha, which never moves, so its cell stays valid for good. Both hand back the
same layout under `subdir`, so your loader never has to know which arm it got.

Numbered files give you a second version system that can disagree with the first. `v2.md` is
whatever someone last saved into it, which is not necessarily what ran when you wrote the number
down; git knows exactly what ran. And nobody ever deletes `v1.md`, so a year later the directory
is a graveyard where the live prompt is the one you have to guess at.

## Which copy each arm reads

The rule you have already met is **production code, never a copy**: a second copy of the
instructions inside `evals/` drifts from the shipping one the first time either is edited, and
then the eval measures the copy. That rule holds. What it is really about is *direction*:

| Direction | Verdict |
| --- | --- |
| Production → eval (a copy of the shipping prompt, kept in the eval) | The drift bug. Never. |
| Eval → production (a candidate developed in the eval, promoted after it wins) | The point of running an eval. |

So the **baseline arm always reads production** — the working tree, or a commit of it. A
**candidate that has not shipped anywhere** has no production copy to read, and may live in the
eval until it wins:

```python
HERE = pathlib.Path(__file__).resolve().parent          # the eval directory
ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repo

ARMS = {
    'live':      ev.PromptArm(globs=['myapp/prompts/*.md'], root=ROOT, subdir='myapp/prompts'),
    'candidate': ev.PromptArm(globs=['prompts/*.md'], root=HERE, subdir='prompts'),
}
```

Both arms return a directory with the same layout, so the entry point takes a `prompt_root`
argument and is none the wiser. The candidate's key is the content of its own files, so
editing it between two sweeps re-runs that arm and leaves the live arm's cached cell alone.

## Promoting the winner

A candidate wins. Someone copies it into production and tidies it on the way in — re-wraps a long
line, fixes the indentation of a bullet, drops the trailing newline. The prompt that shipped is
now not the prompt that won, and every number you have is about a prompt that exists nowhere.

That failure is invisible and it is also cheap to rule out. After a candidate wins:

1. Copy the file into production **byte-exact**. No reformatting, no rewrapping, no "while I'm
   here".
2. Repoint that arm at production — `globs=['myapp/prompts/*.md'], root=ROOT`.
3. Re-run that one cell.
4. Confirm the number reproduces.

Step 3 is a real re-run — the arm reads a different path now, so it is not served from cache —
but it is *one cell*, not the matrix. That is the whole proof, and it is the cheapest part of the
entire eval. If the file went across byte-exact the number lands where it did, inside the band
you already measured. If it was tidied on the way in, you find that out today instead of in a
month, when the shipped prompt quietly under-performs the one you signed off.

Then record the promotion where it will be seen — the eval's `README.md`, under
`Judgement calls`. A candidate that won and was never promoted is the other half of this failure,
and it is quieter.

## Generator prompts stay in the eval

A prompt that **builds or harvests cases** — one that writes synthetic inputs, or pulls candidate
examples out of a log — is not production behaviour and is never promoted, however well it works.
It belongs to the eval permanently, alongside the case file it produced. Keep it in the eval
directory, name it so nobody mistakes it for a candidate (`prompts/generate_cases.md`), and leave
it out of the arms entirely.

The reason to be explicit: the promotion rule above is easy to apply to every prompt in the eval
directory, and then a case-generation prompt ends up shipped as application behaviour.

## Read next

- **[LLM evals](llm-evals.md)** — the full guide: arms and baselines, what caching is keyed on,
  and reading the verdict.
- **[Classes and functions](llm-evals-api.md)** — `PromptArm`, `git_tree`, `git_sha` and
  everything else `oryxflow.evals` exports.
- **[What an eval plan must contain](llm-evals-checklist.md)** — the six sections a plan needs
  before anyone can run it.
- **[Scaffold an LLM eval](claude-plugin/evals.md)** — the plugin commands that write the arms
  and the loader for you.
