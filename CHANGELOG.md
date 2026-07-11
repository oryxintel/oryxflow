# Changelog

All notable changes to **oryxflow** are recorded here. This file is read by humans *and* by AI
coding agents diagnosing regressions after an upgrade, so the format is load-bearing:

- Newest first. One `## [version] - YYYY-MM-DD` heading per release; version is calver `YY.M.D`
  matching `setup.py` / `oryxflow.__version__`. Unreleased work goes under `## [Unreleased]`.
- Group bullets under `### Added` / `### Changed` / `### Deprecated` / `### Removed` /
  `### Fixed` / `### Security` (Keep a Changelog: https://keepachangelog.com/).
- **Every breaking change is a bullet that STARTS with the literal token `BREAKING:`** and carries
  a same-bullet `Migration:` clause with the old→new fix.
- **Name the actual symbol in backticks** (`` `Task.persist` ``, `` `RunResult.summary()` ``), never
  prose. Agents grep this file for the symbol in their traceback.

## [Unreleased]

## [26.7.11] - 2026-07-11
### Changed
- Documentation rewrite and PyPI packaging updates; no API changes.

## [26.6.6] - 2026-06-06
### Changed
- BREAKING: package renamed `d6tflow` -> `oryxflow`. Migration: replace `import d6tflow` with
  `import oryxflow` (and `from d6tflow...` -> `from oryxflow...`); the public API names are
  otherwise unchanged.
