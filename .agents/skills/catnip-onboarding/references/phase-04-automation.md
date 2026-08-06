---
name: phase-04-automation
description: "Install the schedule and prove it will still be running next month: timer, lingering, retention, and the failure modes that are silent."
parent: catnip-onboarding
---

# Phase 4: Automation

Produces: a schedule that survives logout, reboot, and a sleeping laptop.

**Carry forward from prior phases**

- The live config path, from Phase 2 — the installer pins it into the
  unit, so it must be the one the user actually edits.
- A verified run, from Phase 3. Do not automate a pipeline that has not
  succeeded once by hand.

## Step 1: Pick the mechanism

**Inspect**

```bash
command -v systemctl && systemctl --user is-system-running 2>/dev/null
uname -s
```

| Observation | Mechanism |
|---|---|
| `systemctl --user` works | systemd user timer — Step 2 |
| Linux, no systemd | cron — Step 4 |
| Darwin | launchd — Step 5 |
| WSL without systemd | cron — Step 4 |

## Step 2: Install the systemd user timer

**Inspect** — show the units before installing them:

```bash
catnip timer print
```

Point out the two lines that matter, because they are the ones a
hand-written unit gets wrong:

- `Environment=CATNIP_CONFIG=…` — the config path is pinned. Without it,
  the timer resolves config from a working directory and can silently
  read a different file than the user edits.
- `Persistent=true` — a run missed while the machine was asleep fires on
  wake. With a 14-day API window, this is the difference between a gap
  and no gap.

**Generate**

```bash
catnip timer install
```

| Output | Action |
|---|---|
| `wrote …` for both units, `enabled catnip.timer` | Continue to Step 3 |
| `NOTE: user lingering is off` | **Do not skip this.** Step 3 |
| `systemd is not available` | Go to Step 4 |

## Step 3: Lingering — the silent failure

**Inspect**

```bash
loginctl show-user "$USER" --property=Linger
```

| Status | Meaning |
|---|---|
| `Linger=yes` | The timer runs whether or not anyone is logged in. Done |
| `Linger=no` | User units are torn down at logout. The timer only fires while the user happens to be logged in — which presents weeks later as "catnip randomly stopped collecting" |

**Decide** — this needs root, so the user runs it:

```bash
sudo loginctl enable-linger "$USER"
```

| Situation | Action |
|---|---|
| Personal desktop, user stays logged in | Still enable it — a reboot logs them out |
| Server, headless | Required. Without it the timer effectively never runs |
| User cannot get sudo | Say plainly that collection will only happen while they are logged in, and suggest cron via their own crontab as an alternative that survives logout |

## Step 4: cron (no systemd)

**Generate**

```bash
catnip timer cron        # prints a line with real absolute paths
crontab -e               # the user pastes it
```

Two limitations to state, not bury:

- **cron cannot catch up.** If the machine is off at the scheduled time,
  that day is lost — permanently, given the 14-day window. On a laptop,
  schedule twice a day.
- **cron's `PATH` is minimal.** If `gh` is not in `/usr/bin`, add a
  `PATH=` line at the top of the crontab. This is the most common reason
  a cron-driven catnip does nothing at all.

## Step 5: launchd (macOS)

There is no `catnip timer` support for launchd. `docs/automation.md` has
a complete plist to adapt. Two things to get right:

- Set `EnvironmentVariables` → `PATH` explicitly; launchd agents do not
  inherit a shell's.
- `StartCalendarInterval` already runs a missed interval on wake, which
  is the `Persistent=true` equivalent. Leave `RunAtLoad` false.

## Step 6: Retention

**Decide** — how long run directories survive. The history store is never
pruned and is unaffected by this.

| Situation | `CATNIP_RETAIN_DAYS` |
|---|---|
| Default | 30 |
| Tight disk, many repos | 14 |
| Wants raw JSON kept for re-analysis | 90+ |

Runs are roughly 50–200 KB per repository. Pruning is never automatic:

```bash
catnip prune          # dry run, always
catnip prune --yes    # actually delete
```

Show the user a dry run and point at the guard:

```
! keep  20260601T031500Z  (NOT INGESTED — deleting would lose 66d-old traffic days)
```

catnip refuses to delete a run that is not in the history store,
regardless of age. If they see that line, the fix is `catnip history`,
not a bigger hammer.

## Step 7: Prove it will fire

**Inspect**

```bash
catnip timer status
```

| Observation | Action |
|---|---|
| `NEXT` is a real timestamp in the future | Correct |
| `catnip.service` shows `failed` | `catnip timer logs` — usually `gh` not on the unit's `PATH` |
| Timer not listed | It was written but not enabled: `systemctl --user enable --now catnip.timer` |

Optionally, fire one now rather than waiting a day:

```bash
systemctl --user start catnip.service
catnip timer logs -n 30
```

## Checkpoint

**Files written**:

- `~/.config/systemd/user/catnip.service`
- `~/.config/systemd/user/catnip.timer`

(or a crontab entry, or a launchd plist)

**Verify**:

```bash
catnip doctor          # timer check PASS, next elapse shown
catnip timer status
```

## Handoff

Onboarding is done. Tell the user, in this order:

1. **It runs itself now.** `catnip timer status` says when next.
2. **`catnip tui` reads it.** `q` quits, `t` changes the timeframe.
3. **`catnip doctor` is the first thing to run if anything looks
   wrong** — it warns when the newest run is over 48 hours old, which is
   the signal that actually matters. "Enabled" says nothing about
   whether the last five runs succeeded.
4. **Come back in two weeks.** The `all` timeframe and the anomaly view
   need history to be interesting. That is the whole point of the timer.
5. For anything `doctor` reports that they cannot fix,
   `.agents/skills/catnip-triage` diagnoses it.
