# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Demo GIF (`docs/media/demo.gif`), embedded at the top of the README.
- `tests/test_tui_layout.py`: renders every view at six terminal sizes
  through a recording `put` and asserts nothing lands on the header or
  footer row.

### Fixed

- History view drew its stars-per-month x-axis labels onto the footer
  (`[q]uit 24-05load`), and at short heights the daily-clones chart
  overflowed too. Charts are now sized against the space that actually
  remains — reserving the axis-label row `bar_chart` needs — and are
  dropped rather than floored to a height that does not fit.

## [0.1.0] - 2026-08-05

### Added

- Initial public release, generalized from a private single-account
  analytics pipeline into a tool any GitHub user can configure.
- `catnip` CLI: `init`, `doctor`, `config`, `run`, `fetch`, `analyze`,
  `history`, `totals`, `verify`, `tui`, `view`, `summary`, `runs`,
  `prune`, `timer`.
- Configuration layer (`CATNIP_*` keys, one resolution order shared by
  the bash fetcher and the Python pipeline), with repo include/exclude
  globs and fork/archived/private toggles.
- Owner auto-detection from the authenticated `gh` account; support for
  both personal accounts and organizations.
- systemd **user** timer (`catnip timer install`) with `Persistent=true`,
  plus a cron fallback (`catnip timer cron`).
- `catnip doctor`: environment, credential, scope, traffic-access, and
  data checks with `--json` output for agents.
- `catnip prune`: retention that refuses to delete any run whose traffic
  days are not yet in the history store.
- Run manifests (`manifest.json`) recording owner, selection counts,
  rate-limit consumption, and traffic denials per run.
- Test suite: config resolution, repo selection, retention invariants,
  systemd unit rendering, an offline end-to-end pipeline over synthetic
  fixtures, and a pty smoke test that drives every TUI view at three
  terminal sizes.
- `.agents/skills/`: `catnip-onboarding` (directed configuration
  workflow) and `catnip-triage` (abductive diagnostic workflow).
- Curses TUI vendored on [pane](https://github.com/TGPSKI/pane).
