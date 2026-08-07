# catnip

[changelog](CHANGELOG.md) | [metrics & data layout](docs/metrics.md) | [configuration](docs/configuration.md) | [automation](docs/automation.md) | [pate.sh](https://pate.sh)

**GitHub keeps fourteen days of your traffic data. catnip keeps all of it.**

Clones, views, unique cloners, popular paths, referrers, stars and forks with
timestamps, releases, pull requests, languages, commit activity — for every
repository you own or administer, collected on a timer into a store that is
never pruned, and read in your terminal.

No service to sign up for. No build step. No data leaving your machine.

```bash
git clone git@github.com:TGPSKI/catnip.git && cd catnip && make install
gh auth login --scopes repo     # traffic endpoints need push access

catnip init                     # write a config
catnip run                      # fetch -> analyze -> history -> totals -> verify
catnip tui                      # read it
```

## See it run

<img src="docs/media/demo.gif" alt="Animated GIF of a terminal running 'catnip tui' over a 43-repository account. The traffic view opens on daily views and clones as bar charts, 8.9k views in the last 14 days. The audience view classifies each repo as audience, mixed, crawler or low-signal from its clones-per-unique-visitor ratio, and pressing ? opens a derivation overlay giving the formula, weights, thresholds and withheld components behind that score. The attribution view lists 103 findings of what moved and the release or push that plausibly caused it, tiered direct, coupled, account, dip, unexplained and no-effect; space opens one in full - a no-effect finding where five commits shipped and nothing followed, with the daily series, the median, MAD, mean absolute deviation and z-score the tier rests on, and what else was true that day. The deltas view shows signed windowed change and per-day rate, and space opens momentum: the daily level, the day-over-day derivative around a zero line, and a fitted slope. The anomaly view is a repo-by-day heatmap above a sortable list of account events, and tab then space opens one - the 31st of July, nine repos departed from their own normal and two of them shipped something that day. The funnel view shades each repo by its own busiest content category with a depth column, and space opens that repo's actual pages with views and uniques. The demo closes on the repo table and a repo drilldown of a crawler: 318 clones in one day against a baseline near 15, 158 unique cloners against 5 unique visitors, and a CONFLICT flag where the clone-intent score reads developer while the audience classification reads crawler."/>

*`catnip tui` over a synthetic account — the audience view separating people
from fetcher fleets, `[?]` explaining exactly how that score was computed, a
movement attributed to its cause and the statistics behind that call, momentum,
the anomaly heatmap with one account event opened in full, the content funnel,
and `[space]` opening a repo's drilldown.*

*The data is generated, not collected: the shapes are designed so the demo
always contains a crawler fleet, an extreme anomaly with a visible cause, a
release that did nothing and a coupled pair. Every number on screen is still
computed by the shipping pipeline — `analyze`, `history`, `totals` and
`derive.py` all run for real; only the API responses underneath are synthetic.
Point catnip at your own account and you get the same screens.*

## The pipeline, and the agent on top of it

Two layers, and the seam between them is the point.

**`catnip report` is the deterministic floor.** It reads the durable store and
writes markdown to `<data>/reports/<UTC stamp>/report.md`: headline totals and
change, biggest movers with their trend, what moved and what caused it, account
events, audience classification, clone intent, coupled repos, content depth —
and an explicit section on what it **cannot** tell you. The same store and
timeframe produce byte-identical output, and every figure is tagged `measured`.
It refuses to write again until the store's newest day advances, because catnip
collects daily and two reports over identical data are one finding printed
twice; `--force` overrides.

**`catnip-prowl` is the inferential pass.** The report answers the questions
someone thought to ask in advance; the skill hunts for the ones they did not —
ratios the report prints separately, a repo behaving unlike its class, causes
that produced nothing, movement with no explanation, gaps in the store itself.
It states each hypothesis, tests it against the raw data, and reports the
refutations as well as the findings.

Its output is a separate `prowl.md` beside the reproducible `report.md`, and
every claim carries its provenance:

| tag | means | the test |
|---|---|---|
| `measured` | straight from `catnip.derive`, reproducible | it appears in `catnip report` |
| `inferred` | a hypothesis that survived checking | the test and its result are stated |
| `speculative` | a pattern worth watching, unsupported | what would confirm or kill it is stated |

Separate files because one is reproducible and one is not: you can diff
consecutive `report.md`s without an agent's prose shifting underneath them, and
a reader never has to guess whether a sentence is arithmetic or an idea. That
discipline is the same one the TUI enforces with `[?]` — a score you cannot
explain from inside the tool is a defect — and the same one that makes absence
legible everywhere else: withheld audience components are dropped and the
weights renormalized rather than scored zero, a repo too thin to classify is
`low-signal` rather than `mixed`, and `unexplained` is a first-class
attribution tier, because most traffic has no visible cause and a tool that
always names one is fitting noise.

`.agents/skills/` ships three skills, usable by any agent that reads `SKILL.md`
files:

| skill | for |
|---|---|
| [**`catnip-prowl`**](.agents/skills/catnip-prowl/SKILL.md) | the analysis pass above — read the report, then hunt, tagging every claim by provenance |
| [**`catnip-onboarding`**](.agents/skills/catnip-onboarding/SKILL.md) | taking a new user from "nothing installed" to "collecting on a timer", one verified phase at a time, resumable |
| [**`catnip-triage`**](.agents/skills/catnip-triage/SKILL.md) | abductive diagnosis when something is wrong: resolve coordinates first, build a timeline, generate competing hypotheses, find the discriminating evidence |

The skills live in `.agents/skills/` and are edited there. Claude Code finds
them already; for any other harness, link them in:

```bash
make link-agents                          # .claude, .cursor, .opencode
make link-agents HARNESSES=".aider"       # or wherever yours looks
```

Symlinks rather than copies, one skill at a time — a copied skill drifts from
the commands it documents, and claiming the whole `skills/` directory would
bury whatever else lives there. `make unlink-agents` removes them.
`catnip doctor --json` exists so an agent can read the whole environment in one
call.

## Install

There is nothing to compile and no dependencies to resolve: Python 3.10+
(stdlib only) and the [GitHub CLI](https://cli.github.com).

```bash
git clone git@github.com:TGPSKI/catnip.git && cd catnip
make install                    # symlinks bin/catnip into ~/.local/bin
```

The install is a symlink, not a copy, so `git pull` updates the installed
command and there is nothing to rebuild. Choose somewhere else with
`make install BINDIR=/usr/local/bin`; undo it with `make uninstall`. Every
command below also works as `./bin/catnip …` straight from the checkout, so
installing is a convenience rather than a step.

```bash
gh auth login --scopes repo     # traffic endpoints need push access

catnip init                     # write a config (or --owner X --no-input)
catnip doctor                   # check tooling, token, access, data
catnip run                      # the whole pipeline
catnip tui                      # read it
catnip timer install            # daily, from now on
```

## Configuration

One file of `KEY=value` lines. Resolution order, first match wins: environment →
`$CATNIP_CONFIG` → `./catnip.conf` → `~/.config/catnip/catnip.conf` → built-in
defaults. `catnip config` prints what is actually in effect, including every
path it implies.

```ini
CATNIP_OWNER=octocat            # blank = the authenticated gh account
CATNIP_EXCLUDE=dotfiles *-private
CATNIP_INCLUDE_FORKS=false
CATNIP_RETAIN_DAYS=30
CATNIP_TIMER_ONCALENDAR=daily
```

See [`catnip.conf.example`](catnip.conf.example) for every key with commentary,
and [docs/configuration.md](docs/configuration.md) for the selection rules and
rate-limit levers. An unknown key is an error, not a shrug — a misspelled
`CATNIP_EXCLUDE` that silently fetches 400 repositories is a worse outcome than
a failed command.

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

Everything lands under `CATNIP_DATA_DIR` (default `~/.local/share/catnip`):

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
```

`/traffic/*` returns a rolling ~14-day window, and days that fall off the end
are not archived anywhere and cannot be requested again — miss a fortnight and
that fortnight is simply gone. Four consequences, and they are the reason the
layout looks like this: the timer defaults to **daily**, with `Persistent=true`
so a machine that was asleep runs on wake; ingest into the history store happens
on every run, before anything is ever deleted; `catnip prune` **refuses** to
delete a run whose days are not already in that store, regardless of age; and
totals are a **pure rebuild**, never an accumulator, because summing overlapping
14-day windows inflates clone counts severalfold.

## The TUI

`catnip tui` opens ten views. Jump with `1`–`0`, cycle with `v`/`V`, window with
`t`/`T` (1d / 1w / 2w / all / epoch), filter with `/`, sort with `s`, and quit
with `q`.

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

**`[space]`** opens the detail for whatever is under the cursor, and closes it
again. What that means depends on the view, because the interesting thing
differs:

| view | `[space]` opens |
|---|---|
| most repo rows | the repo **drilldown** — dual daily chart with anomaly markers and release rules under the days that caused them, uniques track, funnel mix, coupled repos, activity |
| `5` attribution | the **finding** — the statistics the tier rests on, the borrowed cause if any, and what else was true that day |
| `6` deltas | **momentum** — level, day-over-day derivative around a zero line, fitted slope, rate vs the previous window |
| `7` anomaly (`tab`) | the **account event** — every repo that moved that day, its Z, value, median and cause |
| `9` correlation | the **pair** — both daily series, both residual series, and what residualizing changed |
| `0` funnel | that repo's **actual pages**, with views, uniques and paths |

`j`/`k` walks to the next item without leaving; `esc` backs out one layer at a
time; `[enter]` always opens the plain repo drilldown. Other keys: `s` picks a
sort column and `S` flips its direction; `o` cycles the repo scope (owned / all
/ forks); `f` filters; `z` on deltas un-collapses unchanged rows; `l` shows
repos with too little traffic to classify.

Every windowed number is computed from the durable daily store, never from
GitHub's rolling 14-day totals — those lose their oldest day nightly, so
differencing them reports window artifacts as change. The one exception is the
funnel, whose path data GitHub exposes only as a rolling snapshot; that view
says so, and `[?]` explains why.

Everything is available headlessly: `catnip view deltas`, `catnip view
audience`, and so on read the same functions the screens do, and `catnip view
why --timeframe audience` prints a derivation.

The viewer also runs with **no run directory at all** — after `catnip prune` has
swept every run, the store still answers. Point it anywhere with
`--history-file` / `--stats-file`, or force it with `--store-only`.

The drawing layer is [pane](https://github.com/TGPSKI/pane), vendored
byte-identically into `src/catnip/tui/`. It knows what a terminal is;
`src/catnip/derive.py` knows what a number means; `src/catnip/ui.py` knows what
a repository is.

## Automation

```bash
catnip timer install     # systemd --user timer, daily, Persistent=true
catnip timer status      # next run, last result
catnip timer logs        # journalctl for the service
```

User timer, not a system one: `gh` reads credentials from your keyring, so a
root-owned unit would find no authentication and fail every night. The installer
checks whether user lingering is enabled and tells you the one command to fix it
if not. No systemd? `catnip timer cron` prints an equivalent crontab line. See
[docs/automation.md](docs/automation.md) for macOS/launchd and for what to do
when the timer silently stops firing.

## Go deeper

| you want to… | start here | then |
|---|---|---|
| **collect** reliably, on a schedule | [docs/automation.md](docs/automation.md) — timers, launchd, and the silent-failure modes | [docs/configuration.md](docs/configuration.md) (selection rules, rate-limit levers) · `catnip doctor` |
| **understand** a number on the screen | `[?]` on that view — formula, thresholds, inputs, withheld components | [docs/metrics.md](docs/metrics.md) (every derivation and the store schema) · `src/catnip/derive.py` |
| **read** the data without the TUI | `catnip report --stdout` — deterministic markdown, every figure tagged `measured` | `catnip view <name>` · [`catnip-prowl`](.agents/skills/catnip-prowl/SKILL.md) for the inferential pass |
| **contribute** (human or agent) | [AGENTS.md](AGENTS.md) — the routing table | [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md) · [LINEAGE.md](LINEAGE.md) |

## Prior art and lineage

catnip is the open-source form of a private single-account analytics pipeline
that ran for months against one GitHub org. Its drawing layer was extracted into
[pane](https://github.com/TGPSKI/pane); the practice of building watchers on top
of that layer is written down in
[run-watcher](https://github.com/TGPSKI/run-watcher). What is new here is
everything that makes it *yours*: configuration, repo selection, owner
auto-detection, scheduling, retention with a data-loss guard, and the
diagnostics. See [LINEAGE.md](LINEAGE.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: `make check` green,
stdlib only at run time, and never edit `src/catnip/tui/` in place — fix it
upstream in pane and re-vendor.

## License

GPL-3.0 — see [LICENSE](LICENSE).
