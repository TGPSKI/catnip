---
name: catnip-onboarding
description: "Configure catnip for a new user, from nothing installed to collecting on a timer. Four verified phases: prerequisites and credentials, configuration, first collection, automation. Use when someone says catnip is not set up, asks how to start collecting GitHub traffic data, or wants the timer running."
metadata:
  author: catnip
  version: "1.0"
compatibility: "bash, python3 >= 3.10, gh CLI. systemd or cron for phase 4."
---

# catnip Onboarding

Takes a user from a fresh clone to a machine that collects GitHub traffic
data every day and keeps it forever.

**You are configuring someone's machine, not editing a repository.** Each
phase ends with a verification command rather than a pull request, and
progress is detected by inspecting the machine's actual state — not by
what you remember doing earlier in the conversation.

## Why the order matters

GitHub's `/traffic/*` endpoints serve a rolling ~14-day window and
nothing older. Every day between "the user wanted this" and "the timer is
running" is a day permanently lost. So: get collection working before
making it pretty, and get the timer installed in the same session.

The other reason for the order: traffic requires **push access** to each
repository. A token without it fetches metadata perfectly and returns no
traffic at all — a partial success that looks like a working setup and is
only caught by probing a real traffic endpoint. Phase 1 does that probe
before any config exists to blame.

## Prerequisites

- A clone of catnip, or `catnip` on `PATH`. Everything below assumes you
  can run `catnip` — use `./bin/catnip` from inside the clone.
- The user present and able to authenticate interactively. `gh auth
  login` opens a browser; you cannot do it for them.
- **Do not run this against a machine that already collects.** If
  `catnip runs` lists runs, this is a reconfiguration, not an onboarding
  — go to Phase 2 and change only what the user asks for.

## Entry Point

Ask exactly one thing:

> **Whose repositories should catnip collect?** Your own account, or an
> organization you administer? (Press enter to use the account `gh` is
> already logged in as.)

Everything else is derived from the machine or decided inside a phase.

## Progress Detection

Run these three commands and read the machine's state. Do not ask the
user what they have already done.

```bash
catnip doctor --json     # tooling, credentials, access, data, timer
catnip config            # which config file is in effect, if any
catnip runs              # collected runs, if any
```

| Observation in `catnip doctor --json` | Meaning |
|---|---|
| `gh cli` or `gh auth` check has `"status": "FAIL"` | Phase 1 incomplete |
| `traffic access` check has `"status": "FAIL"` | Phase 1 incomplete — token cannot read traffic |
| `paths.config_file` is `""` | Phase 2 incomplete — no config file, running on defaults |
| `runs` check has `"status": "WARN"` and says `none under` | Phase 3 incomplete |
| `analysis` or `history store` check is not `PASS` | Phase 3 incomplete — collected but not usable |
| `timer` check says `not enabled` | Phase 4 incomplete |
| Every check `PASS` | Onboarding complete — go to Verification |

## Determine Phase

Route to the **first** incomplete phase. Phases are sequential; a later
phase assumes the earlier ones hold.

| Detected state | Action |
|---|---|
| `gh` missing, unauthenticated, or traffic denied | **Phase 1** — `references/phase-01-prerequisites.md` |
| Credentials fine, no config file | **Phase 2** — `references/phase-02-configuration.md` |
| Config exists, no usable run | **Phase 3** — `references/phase-03-first-collection.md` |
| Runs exist, no timer | **Phase 4** — `references/phase-04-automation.md` |
| All checks pass | **Verification** (below) |

## Route to Phase

Present the state before entering a phase, so the user can see what you
saw:

```
catnip onboarding
  Phase 1  Prerequisites & credentials   ✓
  Phase 2  Configuration                 ✓  (~/.config/catnip/catnip.conf)
  Phase 3  First collection              ✗  no runs yet
  Phase 4  Automation                    –

Next: Phase 3 — collect the first run (about a minute for 20 repos).
```

Then read that phase file and follow it. Do not skip ahead, and do not
perform a later phase's work "while you are here" — each phase's
verification assumes only its predecessors ran.

## Design Principles

1. **The machine is the source of truth.** Every routing decision comes
   from `catnip doctor`, `catnip config`, or the filesystem. Nothing
   comes from conversation history, which is wrong as soon as the user
   ran a command in another window.
2. **Ask less, infer more.** The owner is the only question in the entry
   point. The data directory, owner type, repo count, and rate budget
   are all observable. Ask about repo *selection* only after showing the
   user what a default selection would collect.
3. **Never guess a value into a config file.** A wrong `CATNIP_OWNER`
   produces a run that succeeds and collects nothing. If you cannot
   derive it, ask.
4. **Prefer the simplest configuration that works.** Empty
   `CATNIP_INCLUDE` (everything) is right for most accounts. Reach for
   globs when the account is large enough that the rate limit is a real
   constraint, not because filtering is available.
5. **Verify with a command, not with a claim.** A phase is complete when
   its verification command exits zero, not when you have written the
   file it asks for.

## Verification

When every phase is done, confirm the whole thing end to end:

```bash
catnip doctor          # every check PASS
catnip timer status    # next elapse is in the future
catnip view traffic    # real numbers, headlessly
```

Then tell the user, concretely:

- which config file is live, and that editing it is how they change
  anything;
- where their data lives (`catnip config` prints it) and that it is the
  only copy of traffic days older than two weeks;
- that `catnip tui` is the interactive view, and `q` quits;
- that `catnip doctor` is the first command to run if anything looks
  wrong, and `.agents/skills/catnip-triage` diagnoses what it reports.

## Phase Files

| Phase | File | Produces |
|---|---|---|
| 1 | @references/phase-01-prerequisites.md | A `gh` login that can read traffic |
| 2 | @references/phase-02-configuration.md | A config file with the right owner and selection |
| 3 | @references/phase-03-first-collection.md | One complete, verified run |
| 4 | @references/phase-04-automation.md | A timer that will still be collecting next month |
