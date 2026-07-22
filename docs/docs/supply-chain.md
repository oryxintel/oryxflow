# Security & supply chain

[![Socket Badge](https://socket.dev/api/badge/pypi/package/oryxflow)](https://socket.dev/pypi/package/oryxflow)
[![PyPI version](https://img.shields.io/pypi/v/oryxflow.svg)](https://pypi.org/project/oryxflow/)
[![License: MIT](https://img.shields.io/pypi/l/oryxflow.svg)](https://github.com/oryxintel/oryxflow/blob/main/LICENSE)

This page is for the security and platform teams who vet what gets past a corporate package
firewall (Sonatype Nexus, JFrog Curation, Socket, Snyk, and similar). It gathers oryxflow's
provenance, its clean-vulnerability status, and the public entries you can check yourself, so
approving oryxflow doesn't need a back-and-forth.

## At a glance

- **Signed provenance on every file.** From 26.7.21 onward, releases are built and uploaded by
  this project's GitHub Actions workflow — no maintainer's laptop, no long-lived upload token —
  and each file on PyPI carries a PyPI-recorded attestation (PEP 740 / Sigstore) tying it to the
  exact repository and workflow that produced it. See [Provenance](#provenance).
- **No known vulnerabilities.** oryxflow has no CVEs or advisories in any public database.
- **Named publisher, permissive licence.** Published by Oryx Intelligence LLC under the MIT
  licence, from a public source repository.
- **Small, tier-one dependency set.** Five direct dependencies, all mainstream and widely
  audited: `pandas`, `pyarrow`, `openpyxl`, `markdown`, `loguru`.
- **Clean own-code scores.** Socket rates oryxflow's own package 100 on Vulnerability, Quality,
  Maintenance and Licence. The only open alert is inherited from a dependency, not oryxflow's
  code — see [Known dependency alerts](#known-dependency-alerts).

## Verify it yourself

Every claim above is checkable from a public source — no need to take our word for it:

| Source | What it tells you | Link |
| --- | --- | --- |
| **PyPI** | Publisher, versions, licence, release history | [pypi.org/project/oryxflow](https://pypi.org/project/oryxflow/) |
| **OSV.dev** | Aggregated vulnerability database (GHSA, PyPA, NVD) | [osv.dev](https://osv.dev/list?ecosystem=PyPI&q=oryxflow) |
| **Snyk** | Vulnerabilities, licence, health score | [security.snyk.io/package/pip/oryxflow](https://security.snyk.io/package/pip/oryxflow) |
| **deps.dev** (Google) | Dependency graph, advisories, scorecard | [deps.dev/pypi/oryxflow](https://deps.dev/pypi/oryxflow) |
| **Socket** | Behavioural supply-chain analysis | [socket.dev/pypi/package/oryxflow](https://socket.dev/pypi/package/oryxflow) |
| **Source** | Full source, issues, history | [github.com/oryxintel/oryxflow](https://github.com/oryxintel/oryxflow) |

## Provenance

Every file published from 26.7.21 onward can be traced back to the commit and CI run that built
it, and PyPI verified that link at upload time.

On any file's detail page on PyPI you will see the publisher recorded as the GitHub repository
`oryxintel/oryxflow` and the workflow `release.yml`. Nothing is uploaded by hand, so there is no
API token that could be stolen and used to publish a file we didn't build.

To check it without a browser, ask PyPI for a file's attestation directly:

```bash
curl https://pypi.org/integrity/oryxflow/26.7.21/oryxflow-26.7.21-py3-none-any.whl/provenance
```

The response names the publisher (`repository`, `workflow`, `environment`) and carries the
Sigstore signing material behind it. Releases before 26.7.21 were uploaded manually and have no
attestation — that is expected, not a tampering signal.

## Dependencies

oryxflow keeps its dependency footprint deliberately small. The core install pulls in only:

- **pandas**, **pyarrow** — dataframes and Parquet/Arrow I/O
- **openpyxl** — Excel I/O
- **markdown** — Markdown report output
- **loguru** — logging (off by default; see [Logging](logging.md))

Everything else — cloud storage (`gcs`/`s3`), Dask, flow export — is an opt-in
[install extra](installation.md), so you only bring in what you use.

## Known dependency alerts

Behavioural scanners occasionally raise a flag on a *dependency*. For completeness, here is the
one you may see, and why it is not a concern:

!!! note "loguru — `Obfuscated code` / `AI-detected potential security risk`"

    Both alerts point at the same file in **loguru** (not oryxflow): `loguru/_recattrs.py`,
    the `RecordException` class. loguru makes log records picklable so they survive being passed
    between processes (loguru's `enqueue=True` option), and the scanner flags that nested-pickle
    pattern as a *potential* deserialization risk.

    This is a false positive for oryxflow's usage: the risk only exists if an attacker can feed
    **untrusted pickle bytes** into the deserializer, and oryxflow never deserializes untrusted
    input through loguru. The alert's own notes confirm the module "contains no explicit
    exfiltration or network behavior." loguru is one of the most-downloaded Python packages and
    is widely audited.

    If your policy engine requires a disposition, this alert can be accepted/ignored with the
    justification: *"False positive — trusted upstream dependency (loguru); no untrusted
    deserialization path in use."*

## Requesting an allowlist entry

New packages with low download history are sometimes auto-quarantined by reputation/age policies
even when their security record is clean. If oryxflow is held by such a policy in your
environment, point your reviewer at this page and the public sources above, or reach out at
[dev@oryxintel.com](mailto:dev@oryxintel.com) and we'll help provide whatever your process needs.
