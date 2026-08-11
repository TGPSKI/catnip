# Automation

catnip is worth automating for one reason: GitHub's traffic API serves a
rolling ~14-day window and nothing older. Anything you do not collect
inside that window is gone permanently. Daily is the floor; the point of
the schedule is not freshness, it is not losing days.

Every mechanism below defaults to **every six hours**. GitHub keeps adding
counts to a day for `CATNIP_SETTLE_HOURS` (36) after it closes and can
correct one until it leaves the 14-day window, so four reads a day place a
late arrival to within six hours instead of within a day, recompute the
unsettled report against corrected figures four times over, and turn one
failed collection into six lost hours rather than a lost day. It does not
make a day settle sooner — the wait is GitHub's pipeline, not a sampling
rate.

Pick one of these. They are alternatives, not layers — two schedulers
means two fetches against one rate budget, racing over one data
directory.

| Mechanism | Runs | Also gives you |
|---|---|---|
| [systemd](#systemd-linux) | `catnip run`, every 6h | Catch-up after sleep, jitter |
| [cron](#cron-anything-posix) | `catnip run`, every 6h | Works anywhere |
| [launchd](#launchd-macos) | `catnip run`, every 6h | Catch-up after sleep |
| [leather tannery](#leather-tannery) | `catnip run`, every 6h | `report.md` when the data lands, `prowl.md` when a report settles |

The first three are the same job on different schedulers. The tannery is
a different proposition: it needs `leather` and an OpenAI-compatible
endpoint, and it replaces the timer rather than joining it.

## systemd (Linux)

```bash
catnip timer install     # writes both units, enables the timer, starts it
catnip timer status      # when it next fires, how the last run went
catnip timer logs -f     # journalctl for the service
catnip timer uninstall   # removes the units; never touches collected data
catnip timer print       # see the units without installing anything
```

The units land in `~/.config/systemd/user/`, rendered from
`systemd/*.in` in this repository.

### Why a user timer

`gh` reads its credentials from your keyring or `~/.config/gh`. A
system-level unit runs as root, finds no authentication, and fails every
night — quietly, because nobody reads a timer's journal until they notice
missing data. catnip installs a `--user` unit for that reason.

The cost of that choice: **user units stop when your last session ends**
unless lingering is enabled. `catnip timer install` checks and tells you:

```bash
sudo loginctl enable-linger $USER
```

Without it, the timer only runs while you happen to be logged in, which
presents as "catnip randomly stops collecting".

### What the units do

```ini
# catnip.service
Type=oneshot
ExecStart=/path/to/bin/catnip run --quiet
Environment=CATNIP_CONFIG=/home/you/.config/catnip/catnip.conf
TimeoutStartSec=2h
Nice=10
IOSchedulingClass=idle
```

The config path is **pinned into the unit** at install time. A timer that
resolved its config from a working directory would silently read a
different file than the one you edit by hand — and both would appear to
work.

```ini
# catnip.timer
OnCalendar=*-*-* 00/6:00:00
RandomizedDelaySec=40m
Persistent=true
```

`Persistent=true` is the load-bearing line: if the machine was asleep or
off at the scheduled time, systemd runs the job on wake. Cron has no
equivalent, and a laptop that misses ten days loses ten days.

systemd's jitter is **additive**: `OnCalendar` is the earliest possible
start and `RandomizedDelaySec` is the width of the window after it. The
default fires uniformly between 00:00 and 00:40 and again every six hours
— the ±20 minutes of variability, measured from the middle of that
spread. Verify a cadence before installing it:

```bash
systemd-analyze calendar '*-*-* 00/6:00:00'
```

Change it in your config, then re-install:

```ini
CATNIP_TIMER_ONCALENDAR=*-*-* 03,15:00:00   # twice a day
CATNIP_TIMER_RANDOM_DELAY=30m
```

```bash
catnip timer install    # re-renders and reloads
```

## cron (anything POSIX)

```bash
catnip timer cron       # prints a crontab line with real paths
crontab -e              # paste it
```

```cron
0 */6 * * *  sleep $(awk 'BEGIN{srand();print int(rand()*2400)}') && \
             CATNIP_CONFIG=/home/you/.config/catnip/catnip.conf \
             /home/you/catnip/bin/catnip run --quiet >> \
             /home/you/.local/share/catnip/logs/cron.log 2>&1
```

Three things to know. Cron has no `RandomizedDelaySec`, so the line sleeps
a random part of 40 minutes first; `awk` rather than `$RANDOM`, because
cron runs the command under `/bin/sh` where that bashism expands to
nothing and the sleep becomes a syntax error every six hours. Cron cannot
catch up a missed run — if the machine is off at 06:00 you lose that read,
though at this cadence the next one four to six hours later covers for it.
And cron's `PATH` is minimal: if `gh` lives somewhere unusual, set `PATH=`
at the top of your crontab.

## launchd (macOS)

There is no `catnip timer` support for launchd yet. Write
`~/Library/LaunchAgents/com.github.tgpski.catnip.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.github.tgpski.catnip</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/catnip/bin/catnip</string>
    <string>run</string>
    <string>--quiet</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CATNIP_CONFIG</key>
    <string>/Users/you/.config/catnip/catnip.conf</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <!-- An array of intervals is launchd's every-six-hours. Minute 17
       rather than 0, to stay off everyone else's top of the hour. -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>17</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>17</integer></dict>
  </array>
  <!-- launchd's equivalent of Persistent=true: run on wake if the
       scheduled time was missed. -->
  <key>RunAtLoad</key>        <false/>
  <key>StandardOutPath</key>  <string>/Users/you/.local/share/catnip/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/you/.local/share/catnip/logs/launchd.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.github.tgpski.catnip.plist
```

`StartCalendarInterval` already fires on wake for a missed interval,
which is the behavior you want. Set `PATH` explicitly — launchd agents do
not inherit your shell's.

## leather tannery

`tannery/` is a [leather](https://github.com/TGPSKI/leather) workspace
that *is* the scheduler. One cron entry runs collect every six hours at
:07; everything downstream — the deterministic report, the meta-analyst,
the analyst passes, the editor — fires when its input arrives rather than
on a clock of its own. The meta-analyst's input is a report settling,
which happens about once a day, so three collections in four queue no
inference at all. There is no `catnip.timer` in this arrangement.
`tannery/README.md` documents the chain; this section is about running
it.

You need `leather` on `PATH`, `catnip` on `PATH` (`make install`), and
an OpenAI-compatible endpoint. No frontier model: every number an agent
reports comes back from a tool.

### Three values are one developer's

They are checked in, and all three are wrong on a fresh clone.

```yaml
# tannery/mcp-servers.yaml — absolute, rewrite for your clone
command: shell-mcp /path/to/catnip/tannery/shell-tools.json

# tannery/config.yaml — your endpoint and the model you serve
model: qwen36-35b-a3b-nvfp4
llm_endpoint: http://127.0.0.1:8000
```

The third is `CATNIP_CONFIG`, and it is the same trap as the pinned
`Environment=` line in the timer unit arriving by a different road. The
tannery's tools shell out to bare `catnip`, so config resolves from the
serve process's working directory and then
`~/.config/catnip/catnip.conf`. If your live config is anywhere else,
the tannery collects into a different data directory than the one you
read, and both halves look healthy. Put it in the unit that runs
`serve`, not in the shell where you tested it.

### Prove the tools resolve, then schedule

```bash
cd tannery
make validate      # parses every agent, lifecycle and toolset
make smoke-tools   # execs each read-only tool's real argv
make run-collect   # one real leg, by hand, before anything is scheduled
```

`leather validate` passing is necessary and not sufficient: a
misconfigured registry can load as zero tools while validation passes,
and the agent — still shown tool names in its system prompt — fabricates
the calls as prose. That reads exactly like a successful run.
`make smoke-tools` is the check that actually executes argv.

### Supervise serve

`leather serve` is a foreground process. Nothing restarts it after a
crash or a reboot, and `make serve` in a terminal dies with the
terminal.

```ini
# ~/.config/systemd/user/catnip-tannery.service
[Unit]
Description=catnip leather tannery
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/path/to/catnip/tannery
Environment=CATNIP_CONFIG=%h/.config/catnip/catnip.conf
ExecStart=/absolute/path/to/leather serve --config config.yaml --tannery tannery.yaml
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now catnip-tannery.service
```

`ExecStart` must be absolute, and if `command -v leather` resolves to a
version-manager shim (asdf, mise), point at the real binary — a shim run
without its manager's environment fails at startup. Lingering applies
here as much as to the timer, and arguably more: logout kills a
long-running scheduler mid-chain rather than between runs.

If you already had a timer, remove it — `catnip timer uninstall`.

### Checking a tannery

```bash
cd tannery
systemctl --user is-active catnip-tannery.service
leather status --config config.yaml
```

```
catnip-collect     success   last=2026-08-08 06:07:01  next=2026-08-08 12:07:00  runs=4
```

**`leather status` reports the schedule and the last outcome, not
liveness.** Every field comes from the state directory, `next=`
included — it is what the last live scheduler persisted, so a tannery
whose `serve` died an hour ago prints the same lines it printed while
healthy. Check the unit too; that is the half that answers whether
anything is going to run it. (Filed upstream as
[leather#77](https://github.com/TGPSKI/leather/issues/77).)

Note also that `catnip doctor`'s `timer` check stays at WARN,
`catnip.timer not enabled`, for the life of a tannery. That is correct.
The check that matters under any mechanism is run freshness.

| Symptom | Cause |
|---|---|
| `serve` exits seconds after starting, two lines of log | Another serve holds `.state/leather.lock`. One serve per state dir |
| An agent narrates tool results instead of calling tools | The registry loaded zero tools — `make smoke-tools`, then the upstream-issues table in `tannery/README.md` |
| An edit to an agent has no effect | Agents load at startup. Restart the service |
| Collect succeeds, the TUI shows nothing new | `CATNIP_CONFIG` missing from the unit — two data directories |
| Collect runs, no report follows | The chain's joints are HTTP intakes on `127.0.0.1:7751`; `api: true` and `api_addr` in `config.yaml` must match the URL in `agents/catnip-collect.lifecycle.yaml` |
| The report records `skipped` | Not a fault. The store did not advance and no day it covered was revised, so the report refused to describe yesterday as today |
| The report records `queued 0 cycle(s)` | Not a fault. No period settled on this run; a period settles about once a day and collection runs four times |
| No `prowl.md` for days | Check `.state/catnip-onsettle.json` against `catnip report --transitions`. Nothing settled means nothing to analyse; a settled period the state file does not list means the queue or the intake is the fault |

## GitHub Actions

Possible, and usually the wrong tool: a scheduled workflow needs a token
with push access to every repository stored as a secret, and the
collected data has to be committed somewhere or uploaded as an artifact.
catnip's whole premise is that the data stays on your machine. If you do
it anyway, remember Actions' scheduled runs are best-effort and can be
delayed by hours or skipped entirely on a busy queue — with a 14-day
window, run it more than daily.

## Checking that it is actually working

```bash
catnip doctor            # includes run freshness and the timer's state
catnip runs              # every run, and whether it was analyzed
```

`doctor` warns when the newest run is over 48 hours old — eight missed
runs at the default cadence, and still the threshold that matters, because
it is the one that means days are being lost. The timer being "enabled"
says nothing about whether the last five runs failed.

Common causes of a timer that stops producing data:

| Symptom | Cause |
|---|---|
| Timer enabled, no runs since you logged out | Lingering not enabled |
| Runs exist but are empty | Token expired or lost `repo` scope — `catnip doctor` |
| `catnip.service` failed, nothing in the log | `gh` not on the unit's `PATH` |
| Runs succeed, charts still empty | Traffic denied (no push access); see `reports/traffic-denied.tsv` |
| `leather status` shows a healthy schedule, no new runs | The scheduler is dead; `status` prints persisted state — check the unit |

For anything not on that list, `.agents/skills/catnip-triage` walks an
agent through diagnosing it properly.

## Disk and retention

A run is roughly 50–200 KB per repository, most of it raw JSON, and the
six-hourly default writes four run directories a day. Prune
periodically — `CATNIP_RETAIN_DAYS` (30) bounds this, and lowering it is
the knob if the four-fold growth matters:

```bash
catnip prune             # dry run, always
catnip prune --yes       # delete runs past CATNIP_RETAIN_DAYS
```

Pruning never touches `stats/history/` — that is the copy that outlives
GitHub's window — and it refuses to delete any run not yet ingested into
it. If you want the run directories gone sooner, lower
`CATNIP_RETAIN_DAYS`; the history keeps growing regardless, at a few
hundred bytes per repo per day.
