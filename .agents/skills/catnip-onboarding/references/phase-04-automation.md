---
name: phase-04-automation
description: "Install the schedule and prove it will still be running next month: systemd timer, cron, or the leather tannery, plus lingering, retention, and the failure modes that are silent."
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

Two questions, in this order. The second one is about the machine; the
first one is about what the user wants, and it is the one that actually
decides.

**What should be automated?**

| Want | Mechanism |
|---|---|
| Collection — data lands daily, they read it in the TUI | systemd timer, cron, or launchd — Steps 2–5 |
| Collection *and* a written report, and a periodic analyst pass over it | leather tannery — Step 6 |

The tannery is strictly more machinery: it needs `leather` on `PATH` and
an OpenAI-compatible LLM endpoint the machine can reach. **If either is
missing, install the timer now and come back to leather later** — every
day without a schedule is a traffic day permanently lost, and the
tannery can replace the timer at any point.

**Inspect** — what the machine supports:

```bash
command -v systemctl && systemctl --user is-system-running 2>/dev/null
command -v leather
uname -s
```

| Observation | Mechanism |
|---|---|
| Wants the report chain, `leather` present, endpoint reachable | leather tannery — Step 6 |
| `systemctl --user` works | systemd user timer — Step 2 |
| Linux, no systemd | cron — Step 4 |
| Darwin | launchd — Step 5 |
| WSL without systemd | cron — Step 4 |

**Install exactly one collector.** The tannery runs `catnip run` on its
own schedule, so a timer plus a tannery is two fetches a day against one
rate budget, racing over the same data directory. Step 6 retires the
timer for that reason.

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

Applies to the timer **and** to a supervised `leather serve` (Step 6c).
A long-running scheduler is if anything more exposed: logout kills it
mid-chain rather than between runs.

**Inspect**

```bash
loginctl show-user "$USER" --property=Linger
```

| Status | Meaning |
|---|---|
| `Linger=yes` | User units run whether or not anyone is logged in. Done |
| `Linger=no` | User units are torn down at logout. The schedule only fires while the user happens to be logged in — which presents weeks later as "catnip randomly stopped collecting" |

**Decide** — this needs root, so the user runs it:

```bash
sudo loginctl enable-linger "$USER"
```

| Situation | Action |
|---|---|
| Personal desktop, user stays logged in | Still enable it — a reboot logs them out |
| Server, headless | Required. Without it the schedule effectively never runs |
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

## Step 6: leather tannery

`tannery/` in the clone is a [leather](https://github.com/TGPSKI/leather)
workspace that *is* the scheduler. Its own cron runs collect daily at
05:07 and the meta-analyst every third day at 08:22; everything
downstream — the deterministic report, the analyst passes, the editor —
fires when its input arrives rather than on a clock. There is no
`catnip.timer` in this arrangement. `tannery/README.md` documents the
chain; this step only gets it running on the user's machine.

Output lands beside the data: `report.md` from the report agent,
`prowl.md` from the analyst chain.

### 6a. Point the tannery at this machine

Three values are checked in as one developer's, and all three are wrong
on a fresh clone. Fix them before validating anything.

| Where | Value | Correct to |
|---|---|---|
| `tannery/mcp-servers.yaml` | absolute path to `shell-tools.json` | this clone's path |
| `tannery/config.yaml` | `model:`, `llm_endpoint:` | the user's endpoint and served model |
| the environment `serve` runs in | `CATNIP_CONFIG` | Phase 2's config path |

`CATNIP_CONFIG` is the same failure as the pinned `Environment=` line in
the timer unit, arriving by a different road. The tannery's tools shell
out to bare `catnip`, so config resolves from the serve process's
working directory and then `~/.config/catnip/catnip.conf`. If the live
config is anywhere else, the tannery collects into a different data
directory than the user reads, and both look healthy. Export it in the
unit that runs `serve` — not in the interactive shell where it was
tested.

### 6b. Prove the tools resolve before scheduling anything

```bash
cd tannery
make validate       # parses every agent, lifecycle and toolset
make smoke-tools    # execs each read-only tool's real argv
```

**`leather validate` passing is necessary, not sufficient.** A
misconfigured tool registry can load as zero tools while validation
passes; the agent still sees tool names in its prompt and fabricates the
calls as prose, which reads like a successful run. `make smoke-tools` is
the check that actually executes argv. Writers are deliberately not
smoked — a smoke test that fetches the whole account is not a smoke
test.

Then one real leg, by hand:

```bash
make run-collect    # ~41 min for 111 repos; the fetch is serial
make run-report     # writes report.md from the store
```

Same rule as Phase 3: do not schedule a chain that has never completed
once under observation.

### 6c. Supervise serve

`leather serve` is a foreground process. Nothing restarts it after a
crash or a reboot, and `make serve` in a terminal ends when the terminal
does. Give it a user unit:

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
systemctl --user status catnip-tannery.service
```

Two details that will bite:

- `ExecStart` must be an absolute path. If `command -v leather` resolves
  to a version-manager shim (asdf, mise), point at the real binary
  instead — a shim run without the manager's environment fails at start
  with a confusing error.
- Lingering, from Step 3, applies here. Without it, logging out stops the
  scheduler.

On a machine without systemd, `nohup leather serve … & echo $! >
.state/tannery.pid` from `tannery/` is the fallback, with the caveat
that nothing brings it back after a reboot. **Kill it by that recorded
PID only** — never by process-name pattern, which takes down any other
leather server on the machine.

### 6d. Retire the timer

If a timer was installed earlier — this session, or months ago — remove
it. Two schedulers means two fetches against one rate budget.

```bash
catnip timer status      # is one installed?
catnip timer uninstall   # removes the units; never touches collected data
```

### 6e. Failure modes specific to the tannery

| Symptom | Cause |
|---|---|
| `serve` exits seconds after starting, two lines of log | Another serve already holds `.state/leather.lock`. One serve per state dir |
| An agent narrates tool results instead of calling tools | The registry loaded zero tools. `make smoke-tools`, then `tannery/README.md`'s upstream-issues table |
| An edit to an agent has no effect | Agents load at startup. `systemctl --user restart catnip-tannery.service` |
| Collect succeeds, the user's TUI shows nothing new | `CATNIP_CONFIG` missing from the unit — two data directories |
| Collect runs, no report follows | The chain's joints are HTTP intakes on `127.0.0.1:7751`. `api: true` and `api_addr` in `config.yaml` must match the URL in `agents/catnip-collect.lifecycle.yaml` |
| Report records `skipped` | Correct behavior, not a fault: the store did not advance, so the report refused to describe yesterday as today |

## Step 7: Retention

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

## Step 8: Prove it will fire

**Inspect** — by mechanism:

```bash
catnip timer status                      # timer
crontab -l                               # cron
launchctl list | grep catnip             # launchd

cd tannery                               # tannery — both commands, not either
systemctl --user is-active catnip-tannery.service
leather status --config config.yaml
```

`leather status` prints one line per scheduled agent, read from the
state dir:

```
catnip-collect     success   last=2026-08-08 05:07:01  next=2026-08-09 05:07:00  runs=1
catnip-prowl-meta  pending   last=never                next=2026-08-10 08:22:00  runs=0
```

**It reports the schedule and the last outcome, not liveness.** Every
field comes from the state dir, including `next=` — that is the value
the last live scheduler persisted, not a live computation. A tannery
whose `serve` died an hour ago prints exactly the same lines it printed
while healthy, and stays plausible until its next fire comes and goes.
`systemctl --user is-active` is the half that answers "is anything going
to run it", which is why both are listed above. (Filed upstream as
[leather#77](https://github.com/TGPSKI/leather/issues/77); if a later
leather reports liveness in `status` itself, this collapses to one
command.)

| Observation | Action |
|---|---|
| Timer: `NEXT` is a real timestamp in the future | Correct |
| Timer: `catnip.service` shows `failed` | `catnip timer logs` — usually `gh` not on the unit's `PATH` |
| Timer: not listed | Written but not enabled: `systemctl --user enable --now catnip.timer` |
| Tannery: `active`, and `catnip-collect` has a future `next=` | Correct |
| Tannery: `active`, but `leather status` lists no agents | Agents failed to load — `systemctl --user status catnip-tannery.service` for the startup error, then `make validate` |
| Tannery: `inactive`, but `next=` looks fine | The scheduler is dead and `next=` is the value it persisted before dying. This is the failure the two-command check exists to catch |
| Tannery: `next=` is in the past | Same failure, one interval later. Nothing advances a schedule that has no process behind it |
| Tannery: `last=` is days old on a daily agent | Collect is failing, not missing. `catnip runs` for the data, the service log for the cause |

Optionally, fire one now rather than waiting a day:

```bash
systemctl --user start catnip.service     # timer
catnip timer logs -n 30

cd tannery && make run-collect            # tannery, one-shot, off-schedule
```

**A tannery leaves `catnip doctor`'s `timer` check at WARN, `catnip.timer
not enabled`, forever.** That is correct and is not something to fix.
The check that matters under either mechanism is run freshness: doctor
warns when the newest run is over 48 hours old, which is the only signal
that distinguishes a schedule that works from one that is merely
installed.

## Checkpoint

**Files written**, by mechanism:

- timer: `~/.config/systemd/user/catnip.service`, `catnip.timer`
- cron: a crontab entry
- launchd: `~/Library/LaunchAgents/com.github.tgpski.catnip.plist`
- tannery: `~/.config/systemd/user/catnip-tannery.service`, plus the
  edits to `tannery/mcp-servers.yaml` and `tannery/config.yaml`

**Verify**:

```bash
catnip doctor          # every check PASS (timer WARN is expected under a tannery)
catnip runs            # the data itself — the check no mechanism can fake
```

## Handoff

Onboarding is done. Tell the user, in this order:

1. **It runs itself now.** `catnip timer status` says when next; under a
   tannery it is `systemctl --user is-active catnip-tannery.service`
   plus `leather status --config config.yaml` from `tannery/`, because
   the second one prints a schedule even when the first one is dead.
2. **`catnip tui` reads it.** `q` quits, `t` changes the timeframe.
3. **`catnip doctor` is the first thing to run if anything looks
   wrong** — it warns when the newest run is over 48 hours old, which is
   the signal that actually matters. "Enabled" says nothing about
   whether the last five runs succeeded.
4. **Come back in two weeks.** The `all` timeframe and the anomaly view
   need history to be interesting. That is the whole point of the
   schedule.
5. For anything `doctor` reports that they cannot fix,
   `.agents/skills/catnip-triage` diagnoses it.

If they installed the tannery, add:

6. **`report.md` appears daily, `prowl.md` every third day** beside the
   data. They are separate files because one reproduces byte-for-byte
   from the store and one is inference.
7. **`.agents/skills/catnip-prowl` does the same hunt on demand**, in
   this conversation, without waiting for the cycle.
