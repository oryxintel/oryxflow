# Docs: migrate Sphinx/RST → MkDocs (Material), test examples, publish to Cloudflare Pages

## Context

The documentation was a Sphinx site written in reStructuredText under `docs/source/*.rst`, built
by Read the Docs. Three problems motivated a move:

1. **Modern, markdown-native site.** We want Material for MkDocs — a fast, searchable, themeable
   site whose source is Markdown (the same format as the blog and the AI-agent-facing docs),
   instead of RST that only Sphinx understands.
2. **The code examples were untested and could rot.** The docs are full of runnable snippets;
   nothing checked that they still import, that the API still exists, or that (e.g.) a snippet
   didn't depend on `sklearn.datasets.load_boston`, which modern scikit-learn removed. We want the
   examples compiled into real unit tests — with the assertions kept *out* of the prose.
3. **AI-crawler / agent ingestion.** We want `sitemap.xml` (with accurate per-file `lastmod`
   dates), `llms.txt` (a sectioned index), and `llms-full.txt` (the whole corpus in one file) so
   developer agents can ingest the docs in one request.

Plus: host the existing `docs/blog/` posts on the same site, and publish to **Cloudflare Pages**
(direct-upload) from both a local script and GitHub Actions.

### Design decisions

- **RST → Markdown is mandatory, not optional.** MkDocs, the Material `blog` plugin, and
  `mkdocs-llmstxt` are all markdown-only. All 18 `.rst` guide pages were converted. *(User
  confirmed: full migration now.)*
- **Conversion = pandoc mechanical pass + scripted cleanup**, not hand-rewrite. `pypandoc-binary`
  (bundled pandoc 3.9, no system install) does rst→gfm. Sphinx-only constructs pandoc can't
  resolve were handled around it: `:doc:`/`:ref:` roles are rewritten to real links *before*
  pandoc using a label→(page, heading-slug) map built by scanning every `.. _label:` and the
  heading it precedes; `.. toctree::` blocks and `.. _label:` anchors are stripped; GitHub-alert
  blockquotes (pandoc's rendering of `.. note::`/`.. tip::`) are post-processed into Material
  `!!! note` / `!!! tip` admonitions.
- **Theme = Material.** The `blog` plugin the request references is a Material feature, so Material
  is required, not just preferred.
- **API docs = mkdocstrings[python]**, replacing Sphinx autodoc (`modules.rst`/`oryxflow.rst` →
  `reference.md`). *(User confirmed.)*
- **Doc testing = phmdoctest, asserts outside the docs.** phmdoctest *compiles* a Markdown page
  into a real `tests/test_docs_*.py`. Pages hold only clean runnable code (no `assert`s); each
  block becomes a test that fails if the code raises — that implicit check is the assertion, and it
  lives in the generated file. Name-sharing across a page's blocks uses the invisible
  `<!--phmdoctest-share-names-->` directive. Working-directory isolation is done in
  `tests/conftest.py` (module-scoped, name-gated to `test_docs_*`) rather than a
  `<!--phmdoctest-setup-->` block, because a setup block renders *visibly* in the page.
- **Hosting = Cloudflare Pages via wrangler direct-upload**, driven by one portable
  `scripts/build_docs.py` (used by the local scripts and CI alike). Read the Docs is kept as a
  mirror, reconfigured from `sphinx:` to `mkdocs:`. *(User chose Cloudflare Pages.)*
- **Sitemap `lastmod` = git commit date.** MkDocs' default sitemap uses the build time.
  `mkdocs-git-revision-date-localized-plugin` provides real git dates but does not populate
  `page.update_date`, so a custom `docs/overrides/sitemap.xml` reads
  `page.meta.git_revision_date_localized*` with a build-date fallback.
- **`mkdocs-llmstxt` (pawamoy), not the `-md` fork.** *(User: "use the mainstream one.")*

## Implementation

1. **Toolchain** (`docs/requirements-docs.txt`): `mkdocs-material`, `mkdocs-llmstxt`,
   `mkdocstrings[python]`, `mkdocs-git-revision-date-localized-plugin`, `phmdoctest`.
   `pypandoc-binary` is migration-only (commented).
2. **Convert guide pages**: 14 `.rst` → `docs/*.md` via the scripted pandoc pipeline above (the
   one-off migration script is not kept in-repo; the mapping is documented here). `index.md` was
   then hand-rewritten as a landing page; `installation.md` and `reference.md` are hand-written;
   `changelog.md` includes the repo-root `CHANGELOG.md` via a `pymdownx.snippets` line-range
   include (`--8<-- "CHANGELOG.md:2"`).
3. **`mkdocs.yml`** (repo root): Material theme (+ light/dark, code copy, edit uri); markdown
   extensions (admonition, superfences, snippets with `check_paths`, toc permalinks, tables);
   plugins `search`, `blog`, `git-revision-date-localized` (`type: iso_date`,
   `enable_git_follow: false`), `mkdocstrings`, `llmstxt` (with `full_output: llms-full.txt` and
   `sections`); `nav`; `validation.anchors: warn`; `exclude_docs` for everything under `docs/`
   that isn't a page (`source/`, `todo/`, `system/`, notebooks, loose `*.py` examples, build junk,
   `CLAUDE.md`); `theme.custom_dir: docs/overrides`.
4. **Custom sitemap**: `docs/overrides/sitemap.xml` emits `<lastmod>` from the git plugin's page
   meta.
5. **Blog** (Material `blog` plugin): posts moved to `docs/blog/posts/*.md` with YAML front-matter
   (`date`, `categories`, `description`); `docs/blog/index.md` landing added; the stray
   `4 reasons why bad.rst` converted to a post.
6. **Doc tests**: `<!--phmdoctest-share-names-->` inserted before runnable blocks in
   `docs/quickstart.md` and `docs/index.md`; `tests/conftest.py` isolation fixture added;
   `tests/test_docs_quickstart.py` + `tests/test_docs_index.py` generated by phmdoctest.
7. **Build/deploy**: `scripts/build_docs.py` (regenerate doc tests → run them → `mkdocs build`,
   with `--check`/`--skip-tests`/`--strict`); `scripts/deploy_docs.sh` + `scripts/deploy_docs.ps1`
   (build + `wrangler pages deploy site`); `.github/workflows/docs.yml` (build+test on PR, deploy
   on push to `main`, `fetch-depth: 0`).
8. **Retire Sphinx build**: `.readthedocs.yaml` switched from `sphinx:` to `mkdocs:`; old
   `docs/source/*.rst` + `conf.py` kept in-tree but excluded from the build (safe to delete once
   reviewed).

## Files modified

- `mkdocs.yml` — **new**, site config.
- `docs/requirements-docs.txt` — **new**, docs toolchain.
- `docs/*.md` (14 converted guide pages) — **new** (`advparam, advtasksdyn, claude-plugin,
  collaborate, functional_tasks, index, logging, managing-workflows, quickstart, run, targets,
  tasks, transition, workflow`).
- `docs/installation.md`, `docs/reference.md`, `docs/changelog.md` — **new**.
- `docs/overrides/sitemap.xml` — **new**, git-date sitemap template.
- `docs/blog/index.md`, `docs/blog/posts/*.md` (4) — **new/moved**; old `docs/blog/*.md` + the
  `.rst` removed.
- `docs/CLAUDE.md` — **new**, how to work on the docs (auto-loaded when editing under `docs/`).
- `tests/conftest.py` — **new**, doc-test isolation.
- `tests/test_docs_quickstart.py`, `tests/test_docs_index.py` — **new**, generated.
- `scripts/build_docs.py`, `scripts/deploy_docs.sh`, `scripts/deploy_docs.ps1` — **new**.
- `.github/workflows/docs.yml` — **new**.
- `.readthedocs.yaml` — sphinx → mkdocs.
- `docs/source/**`, `docs/conf.py`, `docs/requirements.txt` — superseded, excluded (removable).

## Verification

```bash
pip install -e . && pip install -r docs/requirements-docs.txt
python scripts/build_docs.py            # regenerate + run doc tests, then mkdocs build
```

Expected:
- Doc tests: `6 passed` (quickstart 5 blocks + index 1 block).
- `mkdocs build`: no ERROR; no broken-link/anchor warnings (the only warnings are third-party:
  `git-revision` "no git logs" for uncommitted files, and `mkdocstrings`/griffe docstring nags).
- `site/llms.txt`, `site/llms-full.txt`, `site/sitemap.xml` all present; sitemap entries carry
  `<lastmod>` (git commit date once files are committed; build date otherwise).
- Full library suite unaffected — still **73 passing** for
  `tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py tests/test_workflowMulti2.py`.

Deploy (needs `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and a Pages project
`oryxflow-docs`): `scripts/deploy_docs.sh` (or `./scripts/deploy_docs.ps1` on Windows); automatic
on push to `main`.

## Implementation notes (divergences from the plan as built)

- **`--strict` is not used in CI.** `mkdocstrings`/griffe emits ~20 docstring-vs-signature
  warnings from the `oryxflow` source (e.g. trailing-space `kwargs ` params in
  `targets/__init__.py`, params documented but absent from a signature in `tasks/__init__.py`).
  These are unrelated to the migration and abort `--strict`. All cross-reference links/anchors are
  strict-clean. CI builds non-strict; `--strict` remains available locally. *Follow-up: clean the
  source docstrings to make strict pass.*
- **Quickstart ML loader renamed `GetData` → `GetDiabetes`.** Running the page top-to-bottom in one
  session exposed a real footgun (not just a test artifact): the ML section's `GetData` had the
  same task family and no distinguishing params as the toy section's `GetData`, so it reused the
  toy's cached 1-column output and `sklearn.preprocessing.scale` got an empty slice. Distinct class
  names fix it. *Possible engine follow-up to investigate separately: automatic code invalidation
  did **not** rerun the redefined same-id `GetData` under `WorkflowMulti` despite the changed code
  body.*
- **Isolation via `conftest.py`, not a phmdoctest setup block** — a `<!--phmdoctest-setup-->` block
  renders visibly in the page, which we don't want; a module-scoped, name-gated fixture keeps the
  prose clean and gives all of a page's blocks one shared cache dir.
