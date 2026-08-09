# catnip

[changelog](CHANGELOG.md) | [metrics & data layout](docs/metrics.md) | [configuration](docs/configuration.md) | [automation](docs/automation.md) | [pate.sh](https://pate.sh)

**Capture, analyze, visualize, and retain GitHub repo metrics locally with stdlib-only Python.**

Clones, views, unique cloners, popular paths, referrers, stars and forks with
timestamps, releases, pull requests, languages, commit activity — every repo
you own or administer, collected daily and read in your terminal. Traffic,
popular paths, referrers and release/push events go into a permanent store
that is never pruned.

Python 3.10+ (stdlib only) and the [GitHub CLI](https://cli.github.com). No
service, no build step, no data leaving your machine.

```bash
# Prereqs
gh auth login --scopes repo     # traffic endpoints need push access
git clone git@github.com:TGPSKI/catnip.git
cd catnip
make install

# Agent quickstart
make link-agents
claude "/catnip-onboarding"
opencode run "/catnip-onboarding"

# CLI quickstart
catnip init                     # write a config (or --owner X --no-input)
catnip run                      # fetch -> analyze -> history -> totals -> verify

# Visualize + Analyze
catnip tui                      # terminal data visualization
catnip report                   # deterministic analysis

# Automate
catnip timer install            # automate collection with systemd
catnip timer cron               # automate collection with cron
```

## Agent Integrations

`catnip` comes with skills for onboarding, troubleshooting, and analysis:

| skill | for |
|---|---|
| [**`catnip-onboarding`**](.agents/skills/catnip-onboarding/SKILL.md) | guided install from fresh clone to full automation |
| [**`catnip-triage`**](.agents/skills/catnip-triage/SKILL.md) | guided troubleshooting |
| [**`catnip-prowl`**](.agents/skills/catnip-prowl/SKILL.md) | deterministic analysis and agentic inference  |

Integrate with your harness:

```bash
make link-agents                          # .claude, .cursor, .opencode
make link-agents HARNESSES=".aider"       # or wherever yours looks
```

## Deterministic reports and agentic inference

`catnip report` writes deterministic markdown from the store: totals and change, movers with trend, attribution, account events, audience, clone intent, coupling, depth, and data limitations.

Same store and timeframe produce byte-identical output, and every figure is
tagged `measured`. A second report over an unchanged store refuses to write —
catnip collects daily, so that would be one finding printed twice. `--force`
overrides it; `--stdout` prints without writing and is never gated.

A report is named for the period it covers, and its name says whether the
figures can still change:

```
reports/2026-08-05-2w.20260807T031722Z/   a day in the window can still be revised
reports/2026-08-05-2w/                    none of them can
```

GitHub keeps correcting a day for well over a day after it closes, so a
report gets recomputed under a new write stamp when the store moves under
it. The earlier copies stay. Once the period leaves GitHub's 14-day window
nothing can revise it again, and the newest recomputation is moved to the
unstamped name. `catnip report --locate` prints the current paths.

`catnip-prowl` is the inferential pass: hypotheses the report doesn't ask,
tested against the raw data, refutations reported with the findings. It
writes `prowl.md` beside `report.md`, so the reproducible file diffs clean.

| tag | means | the test |
|---|---|---|
| `measured` | straight from `catnip.derive`, reproducible | it appears in `catnip report` |
| `inferred` | a hypothesis that survived checking | the test and its result are stated |
| `speculative` | a pattern worth watching, unsupported | what would confirm or kill it is stated |

## See it run

*Visualizing data with `catnip tui` after simulated `catnip run` data collection.*

<img src="docs/media/demo.gif" alt="Animated GIF of a terminal running 'catnip tui' over a 43-repository account. The traffic view opens on daily views and clones as bar charts, 8.9k views in the last 14 days. The audience view classifies each repo as audience, mixed, crawler or low-signal from its clones-per-unique-visitor ratio, and pressing ? opens a derivation overlay giving the formula, weights, thresholds and withheld components behind that score. The attribution view lists 103 findings of what moved and the release or push that plausibly caused it, tiered direct, coupled, account, dip, unexplained and no-effect; space opens one in full - a no-effect finding where five commits shipped and nothing followed, with the daily series, the median, MAD, mean absolute deviation and z-score the tier rests on, and what else was true that day. The deltas view shows signed windowed change and per-day rate, and space opens momentum: the daily level, the day-over-day derivative around a zero line, and a fitted slope. The anomaly view is a repo-by-day heatmap above a sortable list of account events, and tab then space opens one - the 31st of July, nine repos departed from their own normal and two of them shipped something that day. The funnel view shades each repo by its own busiest content category with a depth column, and space opens that repo's actual pages with views and uniques. The demo closes on the repo table and a repo drilldown of a crawler: 318 clones in one day against a baseline near 15, 158 unique cloners against 5 unique visitors, and a CONFLICT flag where the clone-intent score reads developer while the audience classification reads crawler."/>

## TUI

### Primary

| Key | View | Answers |
|---|---|---|
| `1` | traffic | daily clones and views, account-wide; at `epoch`, the whole store plus stars/month |
| `2` | audience | is this repo's traffic people or fetchers? |
| `3` | table | every repo, sortable by momentum, audience, depth, stars-per-visitor |
| `4` | lang | bytes by language |
| `5` | attribution | what moved, and the release or push that plausibly caused it |
| `6` | deltas | what changed this window versus last, and the per-day rate |
| `7` | anomaly | repo × day heatmap; simultaneous spikes fold into one account event |
| `8` | profile | clone intent — how many of the people who looked, cloned |
| `9` | correlation | repos coupled after the account-wide release wave is removed |
| `0` | funnel | content mix per repo, and how far past the front door traffic got |

`1`–`0` jump | `v`/`V` cycle | `t`/`T` window (1d / 1w / 2w / all /
epoch) | `/` filter | `s` sort | `q` quit.

### Detail

| view | `[space]` opens |
|---|---|
| most repo rows | the repo **drilldown** — daily charts with anomaly markers and release rules, uniques track, funnel mix, coupled repos, activity |
| `5` attribution | the **finding** — the statistics the tier rests on, the borrowed cause if any, what else was true that day |
| `6` deltas | **momentum** — level, day-over-day derivative, fitted slope, rate vs previous window |
| `7` anomaly (`tab`) | the **account event** — every repo that moved that day: Z, value, median, cause |
| `9` correlation | the **pair** — both daily series, both residual series, what residualizing changed |
| `0` funnel | that repo's **actual pages** — views, uniques, paths |

`j`/`k` next item | `esc` back one layer | `[enter]` always the repo drilldown.
`S` flips sort direction; `o` cycles scope (owned / all / forks); `f` filters;
`z` un-collapses unchanged delta rows; `l` shows low-signal repos.

`[?]` shows the derivation behind the current view: formula, thresholds,
inputs, raw-vs-unique, withheld components.

### Headless

`catnip view <name>` reads the same functions the screens do;
`catnip view why <view>` prints a derivation. The viewer runs with
no run directory at all: `--store-only`, `--history-file`, `--stats-file`.

That is the state `catnip prune` eventually leaves behind, and it is the one
consequence `CATNIP_RETAIN_DAYS` has beyond disk. Every *windowed* number
survives, because `derive.py` computes it from the store: traffic, audience,
deltas, anomalies, attribution, coupling, intent, momentum.

One view goes empty: `4:lang`. Language bytes are current state and come back
on the next run, so losing them costs nothing.

Popular paths used to go with it, and that one was a real leak —
`/traffic/popular/*` serves a rolling ~14 days, so a pruned run took the only
copy of observations GitHub will not serve again. Paths are ingested as of
store schema 3, dated the day they were observed, exactly as referrers are.
The funnel now reads the store and survives pruning.

## Data, sources, storage, and retention

| Data | Source |
|---|---|
| Stars, forks, watchers, languages, topics, license | `/repos/{owner}/{repo}` |
| Clone counts + 14 daily buckets, unique cloners | `/traffic/clones` |
| View counts + 14 daily buckets, unique visitors | `/traffic/views` |
| Popular paths, referrers | `/traffic/popular/*` |
| Star and fork events with timestamps | `/stargazers`, `/forks` |
| Commit activity, code frequency, contributor stats | `/stats/*` |
| Pull requests, issues, releases and asset downloads | `/pulls`, `/issues`, `/releases` |

### Data retention

A **run** is one fetch's raw API bodies and derived CSVs. 
Runs age out because once their days are in the store they are redundant. 
Run retention is configured with `CATNIP_RETAIN_DAYS`.

The **store** is the permanent daily series.
Store path is configured with `CATNIP_DATA_DIR`. 

```
runs/20260805T031722Z/
  raw/          one JSON body per endpoint per repo, as returned
  analysis/     22 CSVs + summary.md — the schema everything else reads
  reports/      which repos were skipped, and which denied traffic
  manifest.json owner, counts, rate-limit spend, duration
stats/
  totals.json           account-wide rollup, rebuilt from scratch each run
  history/
    traffic_daily.json  the permanent series — never pruned. Daily clones
                        and views per repo (count and uniques), plus dated
                        referrer observations and a release/push event log
    snapshots.jsonl     append-only per-run, per-repo snapshots
    daily_snapshots.jsonl  what each fetch read for each still-movable day,
                        before the merge destroyed it. The one input
                        `catnip settle` has; bounded by
                        CATNIP_SETTLE_LOG_DAYS and safe to delete
```

## Configuration

`catnip config` shows active configuration

* Example: [`catnip.conf.example`](catnip.conf.example)
* Documentation: [docs/configuration.md](docs/configuration.md) 

```ini
CATNIP_OWNER=octocat
CATNIP_EXCLUDE=dotfiles *-private
CATNIP_INCLUDE_FORKS=false
CATNIP_RETAIN_DAYS=30           
CATNIP_TIMER_ONCALENDAR=daily
```

## Automation

```bash
catnip timer install     # systemd --user timer, daily, Persistent=true
catnip timer status      # next run, last result
catnip timer logs        # journalctl for the service
```

User timer, not system — `gh` reads credentials from your keyring, which root
doesn't have. The installer checks user lingering and prints the fix. No
systemd: `catnip timer cron`. macOS/launchd and the silent-failure modes:
[docs/automation.md](docs/automation.md).

### Tannery

A timer collects. [`tannery/`](tannery/) is the same schedule with agents on
it - a [leather](https://github.com/TGPSKI/leather) tannery.

```bash
cd tannery
make validate     # leather validate every agent, lifecycle and toolset
make smoke-tools  # exec the read-only tools for real
make serve        # run the scheduler
```

| agent | when | does |
|---|---|---|
| `catnip-collect` | daily 05:07 | `catnip run`, then checks it landed |
| `catnip-report` | daily 06:52 | writes the deterministic report |
| `catnip-prowl` | every 3rd day 08:22 | hunts what the report does not answer |

Each agent is multi-turn and every turn replaces its tool scope, so an agent
can only reach the tools that turn declares:

```
catnip-prowl   catnip-evidence -> catnip-file -> catnip-publish
```

It cannot file a finding during the turn it gathers evidence, and cannot
gather more once it starts filing. "Test before you file" is not an
instruction it is asked to follow - there is no turn in which it can do
otherwise. See [tannery/README.md](tannery/README.md).

## Command reference

| | |
|---|---|
| `catnip init` | Write a config file (interactive, or fully from flags) |
| `catnip doctor` | Tooling, credentials, scopes, traffic access, data. `--json` for agents |
| `catnip run` | The whole pipeline; what the timer runs |
| `catnip fetch` | Collect a new run (`--dry-run` previews the repo selection) |
| `catnip analyze` | Re-derive analysis CSVs for a run |
| `catnip history` | Ingest runs into the permanent store |
| `catnip totals` | Rebuild account-wide totals |
| `catnip verify` | Assert the analysis CSVs the TUI requires exist and are non-empty |
| `catnip tui` / `view` | Interactive UI / one view as text |
| `catnip report` | Deterministic markdown analysis of the store (`--stdout`, `--force`) |
| `catnip summary` | The newest run's `summary.md` |
| `catnip settle` | Measure how long GitHub kept revising each recent day; exits non-zero when that contradicts `CATNIP_SETTLE_HOURS` |
| `catnip prune` | Delete run directories past their retention — never one whose traffic days are missing from the store. Dry-run by default |
| `catnip timer` | `install` · `status` · `logs` · `uninstall` · `print` · `cron` |

## Go deeper

| you want to… | start here | then |
|---|---|---|
| **collect** reliably, on a schedule | [docs/automation.md](docs/automation.md) | [docs/configuration.md](docs/configuration.md) · `catnip doctor` |
| **understand** a number on the screen | `[?]` on that view | [docs/metrics.md](docs/metrics.md) · `src/catnip/derive.py` |
| **read** the data without the TUI | `catnip report --stdout` | `catnip view <name>` · [`catnip-prowl`](.agents/skills/catnip-prowl/SKILL.md) |
| **contribute** (human or agent) | [AGENTS.md](AGENTS.md) | [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) · [LINEAGE.md](LINEAGE.md) |

## License

GPL-3.0 — see [LICENSE](LICENSE).