# catnip

GitHub serves you fourteen days of traffic data and then forgets. catnip
collects it on a timer, keeps it forever, and draws it in your terminal.

Clones, views, unique cloners, popular paths, referrers, stars and forks
with timestamps, releases, pull requests, languages, commit activity —
for every repository you own or administer. Stdlib Python and `gh`, no
install step, no service to sign up for, no data leaving your machine.

<img src="docs/media/demo.gif" alt="Animated GIF of a terminal running 'catnip tui' over 98 repositories: the traffic view opens on daily views and clones as bar charts with top-views and top-clones lists beneath; the audience view classifies each repo as audience, mixed, crawler or low-signal from its clones-per-unique-visitor ratio, showing one repo cloned 38 times per unique visitor labelled crawler; pressing ? opens a derivation overlay giving the formula, weights, thresholds and withheld components behind that score; the deltas view shows signed windowed change and a per-day rate per repo; the anomaly view is a repo-by-day heatmap above a sortable list of account events, and pressing tab then space opens one of them - the 30th of July, five repos, leather spiking to a modified z-score of 81.6 on 308 clones against a median of 6, attributed to 8 commits that day; the repo table lists momentum, audience and depth columns; and pressing space on a repo row opens its drilldown, with daily views and clones charts annotated underneath by release, push and anomaly markers on the days that caused them, unique cloners against unique visitors, and a CONFLICT flag where the clone-intent score reads developer while the audience classification reads crawler"/>

*`catnip tui` over one real account's 98 public repositories — the
audience view separating people from fetcher fleets, `[?]` explaining
exactly how that score was computed, windowed deltas, the anomaly
heatmap with its account-event list, one event opened in full, and
`[space]` opening a repo's drilldown. Everything here is on disk after
one `catnip run`.*

## Why it exists

GitHub's `/traffic/*` endpoints return a rolling ~14-day window. Days
that fall off the end are not archived anywhere and cannot be requested
again — miss a fortnight and that fortnight is simply gone. Every part of
catnip's design follows from that one fact:

- the timer defaults to **daily**, with `Persistent=true` so a machine
  that was asleep runs on wake;
- ingest into a permanent **history store** happens on every run, before
  anything is ever deleted;
- `catnip prune` **refuses** to delete a run whose days are not already
  in that store, regardless of age;
- totals are a **pure rebuild**, never an accumulator, because summing
  overlapping 14-day windows inflates clone counts severalfold.

## Quick start

```bash
gh auth login --scopes repo     # traffic endpoints need push access
git clone git@github.com:TGPSKI/catnip.git && cd catnip

./bin/catnip init               # write a config (or --owner X --no-input)
./bin/catnip doctor             # check tooling, token, access, data
./bin/catnip run                # fetch -> analyze -> history -> totals -> verify
./bin/catnip tui                # read it
./bin/catnip timer install      # daily, from now on
```

There is nothing to install and no dependencies to resolve: Python 3.10+
(stdlib only) and the [GitHub CLI](https://cli.github.com). Put `bin/` on
your `PATH` if you want `catnip` without the `./bin/` prefix.

## Configuration

One file of `KEY=value` lines. Resolution order, first match wins:
environment → `$CATNIP_CONFIG` → `./catnip.conf` →
`~/.config/catnip/catnip.conf` → built-in defaults. `catnip config`
prints what is actually in effect, including every path it implies.

```ini
CATNIP_OWNER=octocat            # blank = the authenticated gh account
CATNIP_EXCLUDE=dotfiles *-private
CATNIP_INCLUDE_FORKS=false
CATNIP_RETAIN_DAYS=30
CATNIP_TIMER_ONCALENDAR=daily
```

See [`catnip.conf.example`](catnip.conf.example) for every key with
commentary, and [docs/configuration.md](docs/configuration.md) for the
selection rules and rate-limit levers. An unknown key is an error, not a
shrug — a misspelled `CATNIP_EXCLUDE` that silently fetches 400
repositories is a worse outcome than a failed command.

## Commands

| | |
|---|---|
| `catnip init` | Write a config file (interactive, or fully from flags) |
| `catnip doctor` | Tooling, credentials, scopes, traffic access, data. `--json` for agents |
| `catnip run` | The whole pipeline; what the timer runs |
| `catnip fetch` | Collect a new run (`--dry-run` previews the repo selection) |
| `catnip analyze` | Re-derive analysis CSVs for a run |
| `catnip history` | Ingest runs into the permanent store |
| `catnip totals` | Rebuild account-wide totals |
| `catnip verify` | Assert every artifact the TUI reads exists |
| `catnip tui` / `view` | Interactive UI / one view as text |
| `catnip report` | Deterministic markdown analysis of the store (`--stdout`, `--force`) |
| `catnip summary` | The newest run's `summary.md` |
| `catnip prune` | Retention, dry-run by default |
| `catnip timer` | `install` · `status` · `logs` · `uninstall` · `print` · `cron` |

## What gets collected

| Data | Source |
|---|---|
| Stars, forks, watchers, languages, topics, license | `/repos/{owner}/{repo}` |
| Clone counts + 14 daily buckets, unique cloners | `/traffic/clones` |
| View counts + 14 daily buckets, unique visitors | `/traffic/views` |
| Popular paths, referrers | `/traffic/popular/*` |
| Star and fork events with timestamps | `/stargazers`, `/forks` |
| Commit activity, code frequency, contributor stats | `/stats/*` |
| Pull requests, issues, releases and asset downloads | `/pulls`, `/issues`, `/releases` |

Everything lands under `CATNIP_DATA_DIR` (default
`~/.local/share/catnip`):

```
runs/20260805T031722Z/
  raw/          one JSON body per endpoint per repo, as returned
  analysis/     17 CSVs + summary.md — the schema everything else reads
  reports/      which repos were skipped, and which denied traffic
  manifest.json owner, counts, rate-limit spend, duration
stats/
  totals.json           account-wide rollup, rebuilt from scratch each run
  history/
    traffic_daily.json  the permanent series — never pruned. Daily clones
                        and views per repo (count and uniques), plus dated
                        referrer observations and a release/push event log
    snapshots.jsonl     append-only per-run, per-repo snapshots
```

## The TUI

`catnip tui` opens ten views. Jump with `1`–`0`, cycle with `v`/`V`,
window with `t`/`T` (1d / 1w / 2w / all / epoch), filter with `/`, sort
with `s`, and quit with `q`.

| Key | View | Answers |
|---|---|---|
| `1` | traffic | daily clones and views, account-wide; at `epoch`, the whole store plus stars/month |
| `2` | audience | is this repo's traffic people or fetchers? |
| `3` | table | every repo, sortable by momentum, audience, depth, stars-per-visitor |
| `4` | lang | bytes by language |
| `5` | attribution | what moved, and the release or push that plausibly caused it |
| `6` | deltas | what changed this window versus last, and the per-day rate |
| `7` | anomaly | repo × day heatmap, with simultaneous spikes folded into one account event |
| `8` | profile | clone intent — how many of the people who looked, cloned |
| `9` | correlation | repos coupled after the account-wide release wave is removed |
| `0` | funnel | content mix per repo, and how far past the front door traffic got |

Two keys carry most of the design:

- **`[space]`** opens the detail for whatever is under the cursor, and
  closes it again. What that means depends on the view, because the
  interesting thing differs:

  | view | `[space]` opens |
  |---|---|
  | most repo rows | the repo **drilldown** — dual daily chart with anomaly markers and release rules under the days that caused them, uniques track, funnel mix, coupled repos, activity |
  | `5` attribution | the **finding** — the statistics the tier rests on, the borrowed cause if any, and what else was true that day |
  | `6` deltas | **momentum** — level, day-over-day derivative around a zero line, fitted slope, rate vs the previous window |
  | `7` anomaly (`tab`) | the **account event** — every repo that moved that day, its Z, value, median and cause |
  | `9` correlation | the **pair** — both daily series, both residual series, and what residualizing changed |
  | `0` funnel | that repo's **actual pages**, with views, uniques and paths |

  `j`/`k` walks to the next item without leaving; `esc` backs out.
  `[enter]` always opens the plain repo drilldown.

- **`[?]`** opens the **derivation overlay**: the formula, thresholds and
  inputs behind whatever is on screen, including whether the numbers are
  raw counts or uniques and which components were withheld for want of
  evidence. A score you cannot explain from inside the TUI is a defect.

Other keys: `s` picks a sort column and `S` flips its direction; `o`
cycles the repo scope (owned / all / forks); `f` filters; `z` on deltas
un-collapses unchanged rows; `l` shows repos with too little traffic to
classify.

Every windowed number is computed from the durable daily store, never
from GitHub's rolling 14-day totals — those lose their oldest day nightly,
so differencing them reports window artifacts as change. The one
exception is the funnel, whose path data GitHub exposes only as a rolling
snapshot; that view says so, and `[?]` explains why.

Everything is available headlessly: `catnip view deltas`,
`catnip view audience`, and so on read the same functions the screens do,
and `catnip view why --timeframe audience` prints a derivation.

The viewer also runs with **no run directory at all** — after `catnip
prune` has swept every run, the store still answers. Point it anywhere
with `--history-file` / `--stats-file`, or force it with `--store-only`.

The drawing layer is [pane](https://github.com/TGPSKI/pane), vendored
byte-identically into `src/catnip/tui/`. It knows what a terminal is;
`src/catnip/derive.py` knows what a number means; `src/catnip/ui.py`
knows what a repository is.

## Analysis

`catnip report` writes a markdown analysis of the durable store to
`<data>/reports/<UTC stamp>/report.md`: headline totals and change,
biggest movers with their trend, what moved and what caused it, account
events, audience classification, clone intent, coupled repos, content
depth — and an explicit section on what it **cannot** tell you.

It is deterministic. The same store and timeframe produce byte-identical
markdown, and every figure is tagged `measured`. It refuses to write
again until the store's newest day advances, because catnip collects
daily and two reports over identical data are one finding printed twice;
`--force` overrides.

The [`catnip-prowl`](.agents/skills/catnip-prowl/SKILL.md) agent skill
builds on it — running the deterministic report first, then forming and
testing its own hypotheses to find what strict arithmetic gates out. Its
findings are tagged `inferred` or `speculative` and written to a separate
file, so you can always tell an agent's idea from the arithmetic.

## Automation

```bash
catnip timer install     # systemd --user timer, daily, Persistent=true
catnip timer status      # next run, last result
catnip timer logs        # journalctl for the service
```

User timer, not a system one: `gh` reads credentials from your keyring,
so a root-owned unit would find no authentication and fail every night.
The installer checks whether user lingering is enabled and tells you the
one command to fix it if not. No systemd? `catnip timer cron` prints an
equivalent crontab line. See [docs/automation.md](docs/automation.md) for
macOS/launchd and for what to do when the timer silently stops firing.

## For agents

`.agents/skills/` ships two skills, usable by any agent that reads
`SKILL.md` files:

- **`catnip-onboarding`** — a directed workflow that takes a new user
  from "nothing installed" to "collecting on a timer", one verified
  phase at a time, resumable from wherever it left off.
- **`catnip-triage`** — abductive diagnosis for when something is wrong:
  resolve coordinates first (which config, which account, which run),
  build a timeline, generate competing hypotheses, and identify the one
  piece of evidence that discriminates between them.

`catnip doctor --json` exists so an agent can read the whole environment
in one call. [AGENTS.md](AGENTS.md) is the router for working on catnip
itself.

## Prior art and lineage

catnip is the open-source form of a private single-account analytics
pipeline that ran for months against one GitHub org. Its drawing layer
was extracted into [pane](https://github.com/TGPSKI/pane); the practice
of building watchers on top of that layer is written down in
[run-watcher](https://github.com/TGPSKI/run-watcher). What is new here is
everything that makes it *yours*: configuration, repo selection, owner
auto-detection, scheduling, retention with a data-loss guard, and the
diagnostics. See [LINEAGE.md](LINEAGE.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: `make check`
green, stdlib only at run time, and never edit `src/catnip/tui/` in place
— fix it upstream in pane and re-vendor.

## License

GPL-3.0 — see [LICENSE](LICENSE).
