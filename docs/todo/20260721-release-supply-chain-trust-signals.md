# Supply-chain trust signals: badges, provenance, allowlisting

## Context

oryxflow is a new PyPI package (first releases July 2026, low download history). Corporate users
increasingly install packages through a **package firewall** — Sonatype Nexus Firewall, JFrog
Curation/Xray, Socket, Snyk — that sits in front of PyPI and blocks anything failing policy.
Two things get a package blocked in those environments:

1. **A real security finding** (a CVE/advisory). oryxflow has *none* today — verified clean on
   OSV.dev, Snyk, and deps.dev; Socket rates oryxflow's own code 100 on Vulnerability, Quality,
   Maintenance and Licence. The one open Socket alert is inherited from the `loguru` dependency
   (`loguru/_recattrs.py` `RecordException` nested-pickle), a false positive for our usage. This
   is already documented for reviewers in `docs/docs/supply-chain.md`.
2. **Reputation/age heuristics.** This is the live risk for oryxflow. Nexus Firewall and JFrog
   Curation ship default policies that auto-quarantine packages that are *newly released*, have
   *low popularity*, a *single maintainer*, or *no provenance* — even with a spotless security
   record. That is exactly oryxflow's current profile (Snyk "health" 27/100, dinged purely on
   popularity/community, not security).

The luigi precedent motivating this: luigi — the workflow engine oryxflow decoupled from — has
real CVEs and gets hard-blocked by CVE-gating firewalls. oryxflow won't trip a *vulnerability*
rule, but it can still be caught by the *reputation* rules above. The goal of this work is to
close that gap by publishing verifiable trust signals so a corporate reviewer can approve
oryxflow quickly, and so automated policies have provenance to key off.

The three trust signals in scope:

- **Reputation badges** (Socket / Snyk) in the README + docs — a visible, one-click trust signal.
- **Build provenance** (PyPI Trusted Publishing attestations, backed by Sigstore) — cryptographic
  proof that each release was built from this repo by CI, not uploaded by a leaked token. This is
  the single strongest signal against the "no provenance / could be typosquat" heuristic.
- **Allowlist / curated-feed inclusion** for big customers — get oryxflow into their Nexus/JFrog
  allowlist, and pursue Google Assured OSS inclusion so a Google-vetted feed carries it.

### Design decisions

- **Trusted Publishing over long-lived API tokens.** Publish to PyPI via a GitHub Actions OIDC
  Trusted Publisher, not a stored `PYPI_API_TOKEN`. This (a) removes a leakable secret, (b) is the
  prerequisite for PyPI to record **attestations** (PEP 740) automatically, and (c) is itself a
  reputation signal ("published via CI from a known repo"). Rejected: keeping the token flow and
  bolting on separate signing — more moving parts, weaker signal, still leaves a secret to leak.
- **Attestations come for free once Trusted Publishing + `gh-action-pypi-publish` are used** with
  `attestations: true` (the default in current versions). No separate Sigstore tooling to run or
  keys to manage — PyPI mints and stores the attestation. So "publish provenance" reduces to
  "move to Trusted Publishing and don't disable attestations." Keep it that simple.
- **Badges: Socket + Snyk only.** Both expose public, auto-updating badge endpoints for the
  *live* score, so they can't go stale or misrepresent. Skip a hand-maintained "no CVEs" badge
  (would rot). deps.dev has no official badge — link it in the docs table (already done) instead.
- **Allowlisting is a runbook, not code.** It's a customer-support / sales-assist process, not
  something we can automate in this repo. Capture it as a short, repeatable checklist a human runs
  per customer, plus the one thing we *can* control centrally: applying for **Google Assured OSS**
  inclusion once (a Google-vetted feed that many enterprises consume, which then covers many
  customers at once).
- **Sequence by leverage.** Provenance first (biggest automated-policy signal, one-time CI change),
  badges second (cheap, visible), Assured OSS third (external review, slow), per-customer
  allowlisting last (reactive, as deals require it).

## Implementation

### 1. Provenance: PyPI Trusted Publishing + attestations

1a. **Register the Trusted Publisher on PyPI** (manual, one-time, needs the PyPI project owner):
   PyPI → project `oryxflow` → *Manage* → *Publishing* → add a GitHub publisher with
   owner `oryxintel`, repo `oryxflow`, workflow filename `release.yml`, environment `pypi`.

1b. **Add/point the release workflow** at `.github/workflows/release.yml` (create if absent). The
   publish job must have `permissions: id-token: write` and use `pypa/gh-action-pypi-publish`
   (which mints PEP 740 attestations by default — do **not** set `attestations: false`). Sketch:

   ```yaml
   name: release
   on:
     release:
       types: [published]
   jobs:
     build:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: '3.x' }
         - run: pip install build && python -m build
         - uses: actions/upload-artifact@v4
           with: { name: dist, path: dist/ }
     publish:
       needs: build
       runs-on: ubuntu-latest
       environment: pypi
       permissions:
         id-token: write        # OIDC for Trusted Publishing + attestations
       steps:
         - uses: actions/download-artifact@v4
           with: { name: dist, path: dist/ }
         - uses: pypa/gh-action-pypi-publish@release/v1
           # attestations: true is the default — leave it on
   ```

   Remove any `password: ${{ secrets.PYPI_API_TOKEN }}` line from the existing publish step and
   delete the now-unused PyPI token secret after the first successful Trusted-Publishing release.

1c. **Verify** after the next release: the PyPI release page shows a "Trusted Publisher" chip and
   per-file "Attestations" (`pip download oryxflow --require-hashes` unaffected; provenance is on
   the file detail). Record the switch in `CHANGELOG.md` under the release that first ships it.

### 2. Reputation badges (Socket + Snyk)

2a. **README** (`README.md`, top, near any existing badges): add the auto-updating badges.

   ```markdown
   [![Socket Badge](https://socket.dev/api/badge/pypi/package/oryxflow)](https://socket.dev/pypi/package/oryxflow)
   [![Known Vulnerabilities](https://snyk.io/test/pypi/oryxflow/badge.svg)](https://security.snyk.io/package/pip/oryxflow)
   ```

   (Confirm the exact Socket badge URL from the "Badge" button on
   `https://socket.dev/pypi/package/oryxflow` — Socket generates the canonical embed snippet
   there; the form above is the current pattern.)

2b. **Docs**: add the same two badges to the top of `docs/docs/supply-chain.md`, above
   "At a glance", so the live scores sit next to the verification table already on that page.

### 3. Google Assured OSS inclusion (one-time application)

3a. Confirm oryxflow meets Assured OSS intake criteria (OSS licence — MIT ✓; on PyPI ✓; source
   public ✓; provenance — satisfied by step 1). Follow Google's current request process at
   <https://cloud.google.com/security/products/assured-open-source-software> to nominate the
   package for inclusion in the Assured OSS PyPI feed.

3b. Once accepted, note it in `docs/docs/supply-chain.md` (a line in "Verify it yourself" or a
   short "Curated feeds" note) so enterprises on Assured OSS know oryxflow is covered.

### 4. Per-customer allowlist runbook

Add a short internal runbook — either a new `docs/todo/runbook-allowlist.md` or a section in the
team's support notes (NOT a published user doc; keep it internal like other `docs/todo/` notes) —
capturing the repeatable steps when a customer reports oryxflow is quarantined:

   - Identify their firewall (Nexus Firewall / JFrog Curation / Socket / Snyk) — the quarantine
     message usually names it.
   - Point their reviewer at `https://docs.oryxflow.dev/docs/supply-chain/` (the public evidence
     page) and the specific finding's disposition text (loguru false-positive line is pre-written
     there).
   - For **Nexus Firewall**: ask them to add a component allowlist / policy waiver for
     `pypi:oryxflow` (and, if their policy flags it, the `loguru` obfuscated-code alert).
   - For **JFrog Curation**: ask them to add a curation policy exception / approved-package entry.
   - Offer provenance (attestations from step 1) as the durable justification.
   - Log which customer/firewall so recurring asks inform whether a broader curated-feed push
     (step 3) is worth prioritising.

## Files modified

- `.github/workflows/release.yml` — new/updated: OIDC Trusted Publishing + attestations; drop the
  PyPI token. (Step 1)
- `README.md` — add Socket + Snyk badges. (Step 2a)
- `docs/docs/supply-chain.md` — add the two badges at the top; add an Assured OSS line once
  accepted. (Steps 2b, 3b)
- `CHANGELOG.md` — note the move to Trusted Publishing/attestations on the first release that
  ships it. (Step 1c)
- `docs/todo/runbook-allowlist.md` (or internal support notes) — new per-customer allowlist
  runbook. (Step 4)
- PyPI project settings (external, not in-repo) — register the GitHub Trusted Publisher. (Step 1a)

## Verification

- **Provenance:** cut a test release; the PyPI file detail page shows "Trusted Publisher" and
  "Attestations"; the release workflow ran with no `PYPI_API_TOKEN` secret. `pip install oryxflow`
  still works unchanged.
- **Badges:** README and `docs/docs/supply-chain.md` render both badges and they resolve to the
  live Socket/Snyk pages (not a broken-image icon).
- **Docs build:** `mkdocs serve` (after `pip install -r docs/requirements-docs.txt`) renders
  `docs/docs/supply-chain.md` with the badges; no broken links introduced. Existing test baseline
  (73 passing) is unaffected — this work touches CI/README/docs only, no library code.
- **Assured OSS / allowlist:** tracked externally; done when oryxflow appears in the Assured OSS
  PyPI feed and the runbook exists for support to follow.

## Implementation notes (divergences from the plan as built)

Built 2026-07-21. The in-repo steps are done: `.github/workflows/release.yml` (new),
Socket/PyPI/licence badges in `README.md` and `docs/docs/supply-chain.md`, a `### Security` bullet
under `## [Unreleased]` in `CHANGELOG.md`, and `docs/todo/runbook-allowlist.md`. The steps needing
a human outside the repo (PyPI publisher registration, first attested release + token deletion,
Assured OSS application, per-customer allowlisting) are tracked in an external (non-repo) planning
note, `20260721-oryxflow-supply-chain-manual-todos.md`.

Divergences:

- **No Snyk badge (step 2a).** Snyk publishes no badge endpoint for this package:
  `snyk.io/advisor/python/oryxflow/badge.svg` and `snyk.io/test/pypi/oryxflow/badge.svg` both
  return 404. The only Snyk badge that renders is the repo-scoped
  `snyk.io/test/github/oryxintel/oryxflow/badge.svg`, whose text is "monitored" — it implies a
  Snyk repo integration that doesn't exist, so it was rejected as a misleading signal. Compounding
  it: Snyk's health score is 27/100 (popularity, not security), so a live Snyk badge would render
  a *bad* number and work against the goal. Revisit when downloads grow. The Socket badge URL from
  the plan was verified working (returns `Socket: 97`, green; a plain `curl` gets 403 — Socket
  blocks non-browser user agents, the badge renders fine in GitHub/MkDocs).
- **Two extra badges added.** `img.shields.io/pypi/v` (version) and `img.shields.io/pypi/l`
  (licence) — both auto-updating from PyPI, so they can't rot, and they answer the first two
  questions a package reviewer asks. This stays within the "no hand-maintained badges" rule.
- **`workflow_dispatch` added to `release.yml`** beyond the plan's sketch, so a publish can be
  re-run by hand if the release-triggered run fails after a partial upload.
- **No provenance claim in the docs yet.** `docs/docs/supply-chain.md` deliberately does *not*
  yet advertise attestations — the first attested release hasn't happened, and the page's value is
  that every claim on it is verifiable today. Adding that line is item 2 in the manual-todo file.
- **The release process itself changed, which the plan understated.** There was no release
  workflow before — publishing was manual from the maintainer's machine (`python -m build` +
  `twine upload`, documented in `setup.py`'s trailing notes and `tests/publish.sh`). Trusted
  Publishing means releases now go out by tagging and publishing a **GitHub Release**; both of
  those files' notes were rewritten to say so, since a stale "just twine upload it" note would
  silently produce unattested releases.
- **`environment: pypi` requires a matching GitHub environment**, which does not exist yet;
  creating it is part of the manual step 1a. Until both it and the PyPI Trusted Publisher exist,
  the release workflow will fail at the publish step — there is no token fallback, by design.
