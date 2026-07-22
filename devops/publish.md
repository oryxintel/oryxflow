# Publishing a release to PyPI

Maintainer-facing. Releases are built and uploaded by **GitHub Actions**, not from a local
machine, using **PyPI Trusted Publishing** (OIDC) — there is no API token anywhere in the repo or
in GitHub secrets.

Docs deployment is a separate pipeline (Cloudflare Pages builds `./site` on every push to `main`);
it is not covered here.

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
web editor; then the *Publish release* button is what ships. If a deliberate "am I sure" gate is
wanted, a required reviewer on the `pypi` environment is the better one — it gates the upload
itself rather than conflating notes-editing with shipping.

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
the workflow's `environment: pypi` and the value registered above. Protection rules on that
environment (required reviewer, or *Deployment branches and tags* restricted to tags) are the
reason the workflow names an environment at all — they are what stops an unattended
`workflow_dispatch` from shipping to PyPI.

`release.yml` must exist on the **default branch**: the `release: published` trigger runs the
workflow from `main`, not from the tag.

## Verifying a release

- The run's *publish* job succeeded and its log lists each uploaded file.
- PyPI project page → the release → *Download files* → a file's detail page shows a **Trusted
  Publisher** provenance chip and its **attestations**.
- `pip install oryxflow==26.7.21` in a clean venv.

Installers do not verify attestations today, so a failure here is a provenance/trust-signal
problem, not an install problem.

Once a release has landed attested, add the provenance claim to `docs/docs/supply-chain.md` —
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
| Job stuck on "Waiting for approval" | The `pypi` environment has a required reviewer. Expected; approve it. |
| Files uploaded but no attestations | Something set `attestations: false`, or the upload did not go through Trusted Publishing (e.g. a manual `twine` upload). |

## Related files

- `.github/workflows/release.yml` — the pipeline.
- `docs/todo/20260721-release-supply-chain-trust-signals.md` — why this exists (the design record,
  including what was rejected).
- `docs/docs/supply-chain.md` — the user-facing page for teams vetting oryxflow through a package
  firewall.
- `docs/todo/runbook-allowlist.md` — what to do when a customer's firewall quarantines the package.
