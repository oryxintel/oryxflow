# Publishing a release to PyPI

Maintainer-facing. Releases are built and uploaded by **GitHub Actions**, not from a local
machine, using **PyPI Trusted Publishing** (OIDC) — there is no API token anywhere in the repo or
in GitHub secrets.

The docs site is a separate repo, `../oryxlib-docs` (Cloudflare Pages builds it on every push to
its `main`); the release step for it is the last item of the checklist below.

## quickstart

```bash
git push github main
git tag v26.11.6 && git push github v26.11.6
gh release create v26.11.6 --generate-notes   # run ONCE; this is what uploads to PyPI
gh run watch                                  # the Release run; done when publish is green
```

 Then approve the deployment: Actions → the Release run → Review deployments → Approve


## Release checklist

1. **Bump the version** in `setup.py` (`version=`). Calver, `YY.M.D` — e.g. `26.7.21`.
   `oryxflow.__version__` reads installed package metadata, so it is not the source of truth
   until you reinstall; `setup.py` is.
2. **Update `CHANGELOG.md`**: move `## [Unreleased]` bullets into a new
   `## [26.7.21] - 2026-07-21` section. `python scripts/check_changelog.py` lints the format
   (dated headings, `BREAKING:` bullets carrying a `Migration:` clause).
3. **Commit, tag, push**:
   ```bash
   git commit -am "release: 26.7.21"
   git tag v26.7.21
   git push github main --tags
   ```
4. **Publish a GitHub Release** on that tag — this is what triggers the upload:
   ```bash
   gh release create v26.7.21 --generate-notes
   ```
   Publish it outright; see *Draft, published, pre-release* below for why.
5. **Verify** (below), then delete the tag and yank the version if something is wrong — PyPI never
   allows re-uploading the same version number.
6. **Update the docs site.** After the PyPI release lands, run
   `cd ../oryxlib-docs && devops/sync-release.sh --push` (Windows:
   `devops\sync-release.ps1 -Push`). It syncs both changelogs and the oryxflow pin, builds
   with the doc tests, commits and pushes (the push redeploys docs.oryxflow.dev via
   Cloudflare). Details in `../oryxlib-docs/MAINTAINING.md`.

## Draft, published, pre-release

A GitHub Release is a page attached to a tag — notes, plus optional attached files. Its state is
what decides whether anything ships:

- **Draft** — saved, visible only to people with write access, emits **no** `release` event. No
  workflow runs, nothing reaches PyPI. It is a scratchpad for the notes.
- **Published** — public, fires `release: types: [published]`, uploads to PyPI. `gh release
  create` publishes immediately; `--draft` does not.

**Publish directly.** The release notes are already curated in `CHANGELOG.md` before the tag
exists, so a draft only buys a chance to hand-edit GitHub's auto-generated commit list — which is
not the source of truth anyway. Use `--draft` only when writing substantial notes by hand in the
web editor; then the *Publish release* button is what ships. Publishing the release IS the
"am I sure" step: there is no second approval. (A required reviewer on the `pypi` environment was
tried and removed: an unapproved run waits silently and GitHub cancels it after 30 days, which
lost 26.10.5 and 26.10.8 without anyone noticing.)

If `gh release create` fails with `Release.tag_name already exists`, the release was already
created by an earlier attempt: do not make another one. Re-run the failed run instead
(`gh run rerun <run-id> --failed`); the build artifact is kept, so only the upload repeats.

**"Set as a pre-release" is not a dry run.** A pre-release is still published, still fires the
event, still uploads to PyPI for real; it only gets a badge and is excluded from "latest". This
workflow has no safe-test mode — TestPyPI would be that, and it is not wired up.

## What CI does

`.github/workflows/release.yml`, two jobs:

- **build** — checkout, Python 3.12, `python -m build`, upload `dist/` as an artifact.
- **publish** — downloads that artifact and runs `pypa/gh-action-pypi-publish`. It has
  `permissions: id-token: write` and `environment: pypi`, and passes **no** `password:`.

GitHub mints a short-lived OIDC token; PyPI verifies its claims against the registered publisher
and exchanges it for an upload token that expires in minutes. The same OIDC identity is what lets
PyPI record **PEP 740 attestations** (Sigstore) for each uploaded file — cryptographic proof the
file was built from this repository by this workflow. The action mints attestations by default;
do not set `attestations: false`.

The split into two jobs is deliberate: only the tiny publish job holds `id-token: write`, so the
build step — which runs project code — never sees a credential-minting permission.

## One-time setup (already done)

Recorded here so it can be reproduced if the repo or project is ever recreated.

**On PyPI** — project `oryxflow` → *Manage* → *Publishing* → add a GitHub publisher:

| Field | Value |
| --- | --- |
| Owner | `oryxintel` |
| Repository | `oryxflow` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

**On GitHub** — *Settings* → *Environments* → an environment named exactly `pypi`. It must match
the workflow's `environment: pypi` and the value registered above. Its one protection rule is
*Deployment branches and tags* → *Selected branches and tags* → a **Tag** rule `v*` (a *Branch*
rule `v*` rejects every release). That rule is the reason the workflow names an environment at
all: it stops a `workflow_dispatch` from a branch shipping to PyPI. **No required reviewers** (see
*Draft, published, pre-release*). Changing the environment's rules while a run is waiting on it
fails that run; re-run it with `gh run rerun <run-id> --failed`.

`release.yml` must exist on the **default branch**: the `release: published` trigger runs the
workflow from `main`, not from the tag.

## Verifying a release

- The run's *publish* job succeeded and its log lists each uploaded file.
- PyPI project page → the release → *Download files* → a file's detail page shows a **Trusted
  Publisher** provenance chip and its **attestations**.
- `pip install oryxflow==26.7.21` in a clean venv.

Installers do not verify attestations today, so a failure here is a provenance/trust-signal
problem, not an install problem.

Once a release has landed attested, add the provenance claim to `../oryxlib-docs/docs/docs/supply-chain.md` —
that page deliberately does not claim it until it is true.

## Emergency fallback: manual upload

Only when CI is broken or the Trusted Publisher registration is gone. Needs a PyPI API token, and
**produces no attestations**, so the release silently loses its provenance signal — prefer fixing
CI and re-cutting the release.

```bash
pip install build twine
python -m build
python -m twine upload --skip-existing dist/*
```

The same commands are kept commented out in `setup.py`'s trailing notes and `tests/publish.sh`.
`python -m build` on its own is also the way to inspect artifacts locally before releasing; it
uploads nothing.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Publish job fails at the OIDC exchange | Publisher not registered, or a field mismatch — owner, repo, workflow **filename** (`release.yml`, not a path), or environment name. A blank environment on PyPI accepts any; a *different* one rejects. |
| Workflow never runs | Release was saved as a draft, or `release.yml` is not on `main`. |
| `File already exists` | That version was already uploaded. PyPI has no overwrite — bump and re-release. |
| Job stuck on "Waiting for approval" | Someone re-added a required reviewer to the `pypi` environment. Remove it (see *One-time setup*), then `gh run rerun <run-id> --failed`. Left alone, GitHub cancels the run after 30 days. |
| Publish fails: "The deployment was rejected or didn't satisfy other protection rules" | The tag does not match the environment's tag rule (e.g. a *Branch* rule instead of a *Tag* rule), or the rules changed while the run waited. Fix the rule, then `gh run rerun <run-id> --failed`. |
| `gh release create`: `Release.tag_name already exists` | The release exists from an earlier attempt. Don't create another; re-run the failed run. |
| Files uploaded but no attestations | Something set `attestations: false`, or the upload did not go through Trusted Publishing (e.g. a manual `twine` upload). |

## Related files

- `.github/workflows/release.yml` — the pipeline.
- `docs/todo/20260721-release-supply-chain-trust-signals.md` — why this exists (the design record,
  including what was rejected).
- `../oryxlib-docs/docs/docs/supply-chain.md` — the user-facing page for teams vetting oryxflow through a package
  firewall.
- `docs/todo/runbook-allowlist.md` — what to do when a customer's firewall quarantines the package.
