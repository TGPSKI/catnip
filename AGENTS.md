# AGENTS.md

catnip collects GitHub repository and traffic analytics for a configured
account, on a timer, into a store it never deletes. It is stdlib Python
plus `gh`, with no install step and no runtime dependencies.

Read this before changing anything; the constraints below are not style
preferences, they are the reasons the design looks the way it does.

## The constraint everything follows from

**GitHub's `/traffic/*` endpoints serve a rolling ~14-day window and
nothing older. Days that fall off cannot be re-requested.**

Consequences you must preserve:

| Rule | Why |
|---|---|
| `history.py` runs before anything deletes a run | The store is the only copy of days past the window |
| `prune.py` refuses to delete an un-ingested run, at any age | A retention bug here is unrecoverable, and invisible until someone opens the `all` timeframe |
| `totals.py` is a pure rebuild, never an accumulator | Every run re-snapshots the same 14 days; summing them inflates clones severalfold |
| Traffic merges are element-wise **max**, never sum | GitHub revises recent days upward as its pipeline settles |
| The timer is `Persistent=true` | A machine asleep at the scheduled time must run on wake |
| Derived views read `stats/history/`, never the rolling totals | The rolling window loses its left edge nightly; differencing two snapshots of it reports "what aged out" as "what changed" |
| Anything from a rolling endpoint gets ingested, not just traffic | `/traffic/popular/*` rolls exactly like `/traffic/clones`. Paths lived only in runs until schema 3, so `prune` destroyed them and the ingest guard — which only knows traffic days — did not object |
| The settling wait is measured, so it is re-measured | 36h is one account's reading of an undocumented pipeline. `daily_snapshots.jsonl` keeps each fetch's reading of each still-movable day so `catnip settle` can prove the constant still holds; nothing else in catnip can, because max-merge destroys the revision history on ingest |

If a change makes any of those five statements false, it is wrong even if
the tests pass.

## Layout

```
bin/catnip            the interface: a bash dispatcher, no logic beyond routing
src/catnip/
  config.py           THE source of truth for every path and knob; also a CLI
                      (`--shell` for bash, `--select` for repo filtering)
  fetch.sh            GitHub API -> runs/<id>/raw/*.json  (+ manifest.json)
  analyze.py          raw JSON -> runs/<id>/analysis/*.csv — the schema boundary
  traffic_anomaly.py  MAD outlier detection      \
  traffic_profile.py  clone-intent profiles       | each a separate process,
  traffic_funnel.py   path taxonomy               | run by analyze.py in order
  traffic_correlation.py  cross-repo Pearson      | (cluster consumes profile
  traffic_cluster.py  cosine clustering          /   and funnel output)
  derive.py           EVERY windowed number the TUI shows, computed from
                      the durable daily store alone. Pure, offline-testable,
                      and the home of DERIVATIONS — the [?] overlay text
                      lives beside the formula it describes
  history.py          analysis CSVs -> stats/history/  (never pruned). Also
                      logs what each fetch READ for each still-movable day,
                      before the max-merge destroys the revision history
  settle.py           `catnip settle` — reads that log back and says whether
                      GitHub still finishes inside CATNIP_SETTLE_HOURS
  totals.py           runs + history -> stats/totals.json
  ui.py               the curses app: every view, every keybinding. Draws;
                      does not decide what a number means (that is derive.py)
  report.py           `catnip report` — deterministic markdown from derive.py
                      alone. Same store + timeframe = byte-identical output;
                      the floor the catnip-prowl skill stands on. Owns the
                      report naming rule: `<period>.<stamp>/` while the
                      window can still be revised, `<period>/` once it
                      cannot. Use `--locate`, never a glob
  doctor.py           preflight + health checks; `--json` is an agent surface
  prune.py            retention with the data-loss guard
  timer.py            renders and installs the systemd user units
  init.py             writes a config file
  tui/                VENDORED from pane — do not edit (see below)
systemd/*.in          unit templates; timer.py substitutes @PLACEHOLDERS@
tests/                stdlib unittest; fixtures.py builds synthetic runs
.agents/skills/       catnip-onboarding (setup), catnip-triage (diagnosis)
```

## Working principles

- **Stdlib only at run time.** ruff is the single dev-time tool and must
  never become an import. There is no `pyproject.toml` on purpose:
  catnip is cloned and run, not installed.
- **`config.py` decides paths. Nothing else does.** A module that builds
  its own `~/.local/share/...` string is a coordinate mismatch waiting
  to happen — the timer and your shell will silently disagree about
  which data directory is real. Add a property to `Config`, use it.
- **bash and Python must never disagree.** `fetch.sh` gets its whole
  configuration from `python3 -m catnip.config --shell` and `eval`s it.
  Do not add a second parser, a default in shell, or a flag that only
  one side understands.
- **Never edit `src/catnip/tui/`.** It is vendored byte-identically from
  [pane](https://github.com/TGPSKI/pane); `make vendor-check` proves it.
  Fix bugs upstream and re-vendor with pane's `tools/vendor.sh`. A local
  "small fix" silently forks the drawing layer for the whole portfolio.
- **Failures must be attributable.** Every repo that is not collected
  gets a written reason (`reports/skipped.tsv`,
  `reports/traffic-denied.tsv`); every run gets a `manifest.json`. "0
  repos fetched" with no explanation is the failure mode this project
  works hardest to avoid.
- **Traffic needs push access.** A token without it fetches metadata
  perfectly and returns no traffic at all — a partial success that looks
  like a working setup. `doctor.py` probes it explicitly; keep that
  check honest.

## Development workflow

```bash
make quick         # everything except the pty smoke (~4s) — iterate on this
make check         # compile + full suite + shell syntax — what CI gates on
make lint          # ruff; the analyze.py exemption is deliberate, see ruff.toml
make smoke         # drive the real TUI in a pty at three terminal sizes
make vendor-check  # byte-identity of src/catnip/tui against PANE=../pane
make doctor        # run catnip's own health checks against your account
```

The pty smoke is ~90% of `make check`'s wall clock: it spawns real
terminals and sleeps 0.35s per keypress so curses can settle. What only a
terminal can prove is that curses does not raise; everything about
*layout* is asserted offline against a character grid, which is why
`test_tui_layout` sweeps every view at every timeframe and the pty suite
deliberately does not. Iterate on `make quick`, gate on `make check`.

Every test is offline: `tests/fixtures.py` writes the same file layout
`fetch.sh` produces, so CI needs no token and cannot be rate-limited. If
you change what `fetch.sh` writes, change `fixtures.py` in the same
commit or the pipeline tests are testing a shape that no longer exists.

## Key internals

- **Slugs.** `config.slug_for` and `analyze.slug_for` must stay
  identical — a test asserts it. The doubled `-` is load-bearing:
  without it `a-b` and `a_b` collide and one repo's raw JSON overwrites
  the other's.
- **Provenance is a contract, not a style.** The attribution view tiers
  each row (`direct` observed, `coupled` inferred, `speculative` never
  silently promoted); the audience composite drops components it cannot
  compute instead of scoring them zero; the report has a section for what
  it cannot tell you. A number whose confidence is not stated is worse
  than a missing number, because it will be acted on.
- **The 17 TUI CSVs** are listed in `doctor.TUI_CSVS`. A deep-traffic
  stage that fails inside `analyze.py` is only a warning, so that list
  is what turns "one silently empty panel three days later" into a
  failed `catnip verify` now. Adding a view means adding its CSV there.
- **`analyze.py` runs the traffic stages as subprocesses** with an
  explicit `PYTHONPATH`, so a crash costs one stage's output rather than
  the run. Order matters: cluster consumes profile and funnel.
- **`stats_warmup` in `fetch.sh`** fires each repo's `stats/*` endpoints
  before the detail loop and collects them after. GitHub computes those
  asynchronously and returns HTTP 202 with an empty body on first
  contact; this is why code-frequency data appears on the second run and
  why collection is a separate pass. Do not inline it back.
- **The run id is a UTC stamp** (`20260805T031722Z`), so lexical sort is
  chronological everywhere. Nothing may depend on mtime — a backup or an
  rsync rewrites those.

## CI

Two required checks (`Validate (3.10)`, `Validate (3.14)`) plus `Lint`;
the names are wired into `.github/rulesets/ruleset-main.json`, so
renaming a job means editing that file in the same commit. 3.10 is the
floor because pane targets it. A `full-test` label adds linux/arm64 and
macos/arm64.

## Contributing summary

- `make check` and `make lint` green; `CHANGELOG.md` updated under
  `## [Unreleased]`.
- New knob → `config.DEFAULTS` + `catnip.conf.example` + `init.py`'s
  template + a test. A key that exists in only some of those is worse
  than no key.
- New collected endpoint → `fetch.sh` + `analyze.py` + `fixtures.py` +
  `doctor.TUI_CSVS` if the TUI reads it.
