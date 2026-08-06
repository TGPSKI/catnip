# Contributing

## Setup

```bash
git clone git@github.com:TGPSKI/catnip.git && cd catnip
make check      # should be green on a fresh clone, with no token
```

There is nothing to install. catnip is stdlib Python 3.10+; `gh` is
needed only to collect real data, not to run the tests. `ruff` is the one
dev-time tool (`pip install ruff`) and must never become an import.

## Commands

| Command | What it does |
|---|---|
| `make check` | Byte-compile, run all tests, `bash -n` both shell entry points. This is what CI gates on. |
| `make test` | unittest discovery with verbose output |
| `make lint` | `ruff check .` — the per-file ignores in `ruff.toml` are deliberate |
| `make smoke` | Drive the real curses TUI in a pty at 60x16, 120x40, 200x50 |
| `make vendor-check` | Prove `src/catnip/tui/` is byte-identical to a pane checkout (`PANE=../pane`) |
| `make doctor` | Run catnip's health checks against your own account |

Every test is offline. `tests/fixtures.py` writes the same file layout
`fetch.sh` produces, so the analyze/history/totals code under test is the
real thing while nothing touches the network.

## Before you change anything

Read [AGENTS.md](AGENTS.md). It states the five invariants that follow
from GitHub's 14-day traffic window — ingest-before-delete, the prune
guard, pure-rebuild totals, max-merge, and `Persistent=true`. A change
that violates one of them is wrong even with green tests, because the
damage (permanently lost traffic days) is invisible until someone opens
the `all` timeframe months later.

## Project-specific constraints

- **Never edit `src/catnip/tui/`.** It is vendored byte-identically from
  [pane](https://github.com/TGPSKI/pane). Fix it there, re-vendor with
  pane's `tools/vendor.sh`, and note the new commit in the PR. `make
  vendor-check` makes this checkable.
- **`config.py` owns every path.** If you need a directory, add a
  property to `Config` rather than composing a path locally. Two places
  computing the same path is how the timer and your shell end up reading
  different data directories.
- **One configuration parser.** `fetch.sh` `eval`s
  `python3 -m catnip.config --shell`. Do not add shell-side defaults or
  a flag only one language understands.
- **New knob, four places.** `config.DEFAULTS`, `catnip.conf.example`,
  `init.py`'s template, and a test. Unknown keys are a hard error, so a
  key that exists in only some of those is a bug for anyone who copies
  the example.
- **New collected endpoint, four places.** `fetch.sh` (guarded by a
  `CATNIP_FETCH_*` flag if it costs a call per repo), `analyze.py`,
  `tests/fixtures.py`, and `doctor.TUI_CSVS` if a view reads it.
- **Rate limit is a shared budget.** Anything that adds a per-repo API
  call needs a flag to turn it off and a line in the config example
  saying what it costs.

## Kinds of contribution

**New TUI view.** Views read analysis CSVs, never raw JSON. Add the CSV
to `doctor.TUI_CSVS`, add the view to `VIEWS` in `ui.py`, and extend
`tests/test_tui_smoke.py` so the pty smoke visits it — a view that
crashes only on an account with no anomalies is exactly what that test
exists to catch.

**New metric.** State which endpoint it comes from and whether it needs
scopes beyond `repo`. Metrics that require a token permission most people
lack belong behind a `CATNIP_FETCH_*` flag, defaulted off.

**Diagnostics.** `doctor.py` is where "why is this broken" is answered.
A new check needs a fix line that names the exact command to run — a
check that reports a problem without a remedy just moves the confusion.

**Skills.** `.agents/skills/` follows the directed-workflow and
abductive-triage formats. Phases must be independently verifiable and
resumable; a phase that cannot report whether it already succeeded
cannot be re-entered safely.

## Pull requests

- `make check` and `make lint` green.
- `CHANGELOG.md` updated under `## [Unreleased]`.
- If you renamed a CI job, update `.github/rulesets/ruleset-main.json` in
  the same commit — the job names are the required status checks.
- Behavior changes want a test that would have failed before. For the
  data-safety invariants, that test belongs in `tests/test_prune.py` or
  `tests/test_pipeline.py`, where the existing ones state the property in
  their names.

Bug reports: run `catnip doctor --json` and include the output, redacted.
It reports which config is in effect, which account the token belongs to,
whether traffic is reachable, and the state of the data — most of a
diagnosis before anyone asks a question.
