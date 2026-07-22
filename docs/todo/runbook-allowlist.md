# Runbook: customer reports oryxflow is blocked / quarantined

Internal support runbook (not published — `docs/todo/` is excluded from the docs build). Follow
this when a user reports that `pip install oryxflow` is blocked by their company's package
firewall. Background and rationale: `docs/todo/20260721-release-supply-chain-trust-signals.md`.

Almost always the cause is a **reputation/age policy** (new package, low download count, single
maintainer), not a security finding — oryxflow has no CVEs or advisories in any public database.
Say that early and back it with the public evidence page.

## 1. Identify the firewall

The quarantine message usually names it. The common four:

| Firewall | What to ask for |
| --- | --- |
| **Sonatype Nexus Firewall / IQ Server** | A component allowlist entry (policy waiver) for `pypi:oryxflow` — and, if their policy also flags it, a waiver for the inherited `loguru` obfuscated-code alert |
| **JFrog Curation / Xray** | A curation policy exception / approved-package entry for `oryxflow` |
| **Socket** | An org-level package approval; Socket already scores oryxflow's own code 100 on Vulnerability / Quality / Maintenance / Licence |
| **Snyk** | Usually a *health score* (popularity) gate, not a vulnerability gate — ask which rule fired before responding |

## 2. Send the evidence page

Point their reviewer at <https://docs.oryxflow.dev/docs/supply-chain/>. It carries, in a form a
security team can act on: no-known-vulnerabilities status, named publisher + MIT licence, the
five-dependency footprint, links to PyPI / OSV.dev / Snyk / deps.dev / Socket / source, and a
pre-written disposition for the one recurring alert.

## 3. If the loguru alert is what fired

The `loguru/_recattrs.py` `RecordException` nested-pickle alert (surfaces as "Obfuscated code" or
"AI-detected potential security risk") is a dependency alert, not oryxflow's code, and is a false
positive for our usage. The page has the copy-paste disposition text:

> False positive — trusted upstream dependency (loguru); no untrusted deserialization path in use.

## 4. Offer provenance as the durable justification

Releases are published via PyPI Trusted Publishing and carry PEP 740 attestations, so each file is
cryptographically tied to a CI build from `github.com/oryxintel/oryxflow`. That is what most
"no provenance / possible typosquat" rules key off. Point them at the **Attestations** section on
the PyPI file detail page for the version they are installing.

## 5. Log it

Record customer + firewall + which rule fired. Recurring asks for the same firewall are the signal
that a broader curated-feed push (Google Assured OSS inclusion) is worth prioritising over
one-off waivers.
