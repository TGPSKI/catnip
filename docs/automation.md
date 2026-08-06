# Automation

catnip is worth automating for one reason: GitHub's traffic API serves a
rolling ~14-day window and nothing older. Anything you do not collect
inside that window is gone permanently. Daily is the right cadence; the
point of the schedule is not freshness, it is not losing days.

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
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
```

`Persistent=true` is the load-bearing line: if the machine was asleep or
off at the scheduled time, systemd runs the job on wake. Cron has no
equivalent, and a laptop that misses ten days loses ten days.

Change the cadence in your config, then re-install:

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
17 3 * * *  CATNIP_CONFIG=/home/you/.config/catnip/catnip.conf \
            /home/you/catnip/bin/catnip run --quiet >> \
            /home/you/.local/share/catnip/logs/cron.log 2>&1
```

Two things to know. Cron cannot catch up a missed run — if the machine is
off at 03:17 you lose that day's collection, so on a laptop prefer
systemd, or run twice a day. And cron's `PATH` is minimal: if `gh` lives
somewhere unusual, set `PATH=` at the top of your crontab.

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
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
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

`doctor` warns when the newest run is over 48 hours old. That is the
signal that matters — the timer being "enabled" says nothing about
whether the last five runs failed.

Common causes of a timer that stops producing data:

| Symptom | Cause |
|---|---|
| Timer enabled, no runs since you logged out | Lingering not enabled |
| Runs exist but are empty | Token expired or lost `repo` scope — `catnip doctor` |
| `catnip.service` failed, nothing in the log | `gh` not on the unit's `PATH` |
| Runs succeed, charts still empty | Traffic denied (no push access); see `reports/traffic-denied.tsv` |

For anything not on that list, `.agents/skills/catnip-triage` walks an
agent through diagnosing it properly.

## Disk and retention

A run is roughly 50–200 KB per repository, most of it raw JSON. Prune
periodically:

```bash
catnip prune             # dry run, always
catnip prune --yes       # delete runs past CATNIP_RETAIN_DAYS
```

Pruning never touches `stats/history/` — that is the copy that outlives
GitHub's window — and it refuses to delete any run not yet ingested into
it. If you want the run directories gone sooner, lower
`CATNIP_RETAIN_DAYS`; the history keeps growing regardless, at a few
hundred bytes per repo per day.
