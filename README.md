# catnip

GitHub serves you fourteen days of traffic data and then forgets. catnip
collects it on a timer, keeps it forever, and draws it in your terminal.

Clones, views, unique cloners, popular paths, referrers, stars and forks
with timestamps, releases, pull requests, languages, commit activity —
for every repository you own or administer. Stdlib Python and `gh`, no
install step, no service to sign up for, no data leaving your machine.

```
catnip  [octocat]  [2w]
Repos: 24  Stars: 318  Forks: 41
Active: 19  14d clones: 1,204  14d views: 8,930  All-time: 41,882c/362,051v
```

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
  analysis/     16 CSVs + summary.md — the schema everything else reads
  reports/      which repos were skipped, and which denied traffic
  manifest.json owner, counts, rate-limit spend, duration
stats/
  totals.json           account-wide rollup, rebuilt from scratch each run
  history/
    traffic_daily.json  the permanent series — never pruned
    snapshots.jsonl     append-only per-run, per-repo snapshots
```

## The TUI

`catnip tui` opens twelve views over the newest run. Jump with `1`–`=`,
cycle with `v`/`V`, window with `t` (1d / 1w / 2w / all — `all` reads the
history store and grows past GitHub's horizon), filter with `/`, sort
with `s`, and quit with `q`.

The traffic, top-repos, table, language, code-frequency, deltas, anomaly
(MAD-based outlier detection), cloner-profile, correlation, funnel, and
history views all read the same CSVs, so anything the TUI shows is also
available headlessly: `catnip view anomaly` prints it as text.

The drawing layer is [pane](https://github.com/TGPSKI/pane), vendored
byte-identically into `src/catnip/tui/`. It knows what a terminal is;
everything that knows what a repository is lives in `src/catnip/ui.py`.

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
