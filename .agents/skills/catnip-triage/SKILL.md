---
name: catnip-triage
description: "Diagnose catnip problems — empty charts, missing repos, a timer that stopped, data that looks wrong, onboarding that will not complete. Resolves coordinate mismatches (which config, which account, which run, which timeframe) before investigating anything deeper. Use when catnip is not behaving as expected."
metadata:
  author: catnip
  version: "1.0"
compatibility: "bash, python3 >= 3.10, gh CLI, catnip on PATH. journalctl for timer issues."
---

# catnip Triage

Structured diagnosis for catnip. Two tiers: resolve coordinates first,
investigate only if they check out.

## Why coordinates come first

Almost every catnip complaint that turns out not to be a bug is one of
these:

- the config file being edited is not the config file in effect;
- the shell and the timer are reading different data directories;
- the token belongs to a different account than `CATNIP_OWNER`;
- the TUI is showing a 1-day window over an account with weekly traffic;
- the "missing" repository was filtered out on purpose, with the reason
  written to a file nobody read.

In every one of those, catnip is working exactly as configured. Deep
investigation before checking them wastes the session and often
"discovers" a bug that does not exist. Tier 1 is designed to close those
in under a minute.

## Start here

Capture the symptom in the user's own words, verbatim, before doing
anything. Then run the one command that reads the whole environment:

```bash
catnip doctor --json > /tmp/catnip-doctor.json
```

That single output carries: which config file is live, which account the
token belongs to, whether traffic endpoints answer, how old the newest
run is, which analysis artifacts are missing, the state of the history
store, and whether the timer is enabled. Read it before asking the user
anything you could have read there.

If `catnip doctor` itself cannot run, that is the finding — go to Phase 1
Step 1.

## The coordinate systems

These are catnip's, specifically. Resolve them in this order.

| Coordinate | Where it can differ | How it presents |
|---|---|---|
| **Config file** | `$CATNIP_CONFIG` · `./catnip.conf` · `~/.config/catnip/catnip.conf` · none (defaults) | "My changes have no effect" — the timer pins one path, the user edits another |
| **Data directory** | `CATNIP_DATA_DIR` per config, or the XDG default | "There are no runs" while runs exist under a different root |
| **Account** | the `gh` token's login vs `CATNIP_OWNER` | Runs succeed and collect nothing, or collect someone else's repos |
| **Repo selection** | `CATNIP_INCLUDE` / `EXCLUDE` / `FORKS` / `ARCHIVED` / `PRIVATE` | "Repo X is missing" — it was rejected, with the reason in `reports/skipped.tsv` |
| **Run** | newest run vs the one the TUI was opened on | Stale numbers; an argument was passed to `catnip tui` weeks ago in a script |
| **Timeframe** | TUI `1d` / `1w` / `2w` / `all` | "My data vanished" — a quiet day at `1d`; "history is missing" at `2w` |
| **Clock** | run IDs are **UTC**; users read local time | "The timer didn't run last night" when it ran at 03:00 UTC = 20:00 local the previous day |
| **systemd scope** | `--user` units (correct) vs system units; lingering on/off | Timer enabled, no runs after logout |

## Evidence rules

Label every claim. In this domain the labels matter because catnip
produces a great deal of DEFINITIVE evidence that is easy to skip past in
favor of a plausible story.

| Label | In catnip, that means |
|---|---|
| **DEFINITIVE** | `catnip doctor --json`, `manifest.json`, `reports/*.tsv`, a `gh api` response, a journal line, a file's existence |
| **CONFIG** | `catnip config` output, the config file, the rendered systemd unit — what *should* happen |
| **SOCIAL / ANECDOTAL** | "I definitely ran it yesterday", "it worked last week", "the timer is on" |

`manifest.json` in each run directory is the highest-value single file:
owner, repos listed/selected/skipped/fetched, traffic denials,
rate-limit before and after, duration. Read it before theorizing about
what a run did.

## Investigation progress

Keep one artifact per incident. It is the state machine.

**File**: `sessions/{YYYY-MM-DD}-{slug}.md` (create `sessions/` wherever
the user keeps notes; it is not part of the catnip repo)

| Artifact state | Phase |
|---|---|
| No artifact | **Phase 1** — @references/phase-01-intake-coordinates.md |
| Intake table present, no coordinate verdict | Continue Phase 1 |
| Verdict `COORDINATE MISMATCH` | Skip to **Phase 3** — @references/phase-03-remediate-document.md |
| Verdict `COORDINATES VERIFIED`, no hypotheses | **Phase 2** — @references/phase-02-investigate.md |
| Hypotheses present, none eliminated | Continue Phase 2 |
| Root cause identified, not remediated | **Phase 3** |
| Actions closed | Complete |

## The recurring hypotheses

Before generating your own, check these — they cover most real catnip
faults once coordinates are verified.

| Hypothesis | Suspect when | Discriminating check |
|---|---|---|
| **Traffic denied** (no push access) | Metadata is present, traffic is empty, for *some* repos | `cat <run>/reports/traffic-denied.tsv` |
| **Token scope lost** | Traffic empty for *every* repo; worked before | `catnip doctor` → `token scopes` and `traffic access` |
| **Timer never fires** | Newest run is days old, timer "enabled" | `loginctl show-user $USER --property=Linger`, then `catnip timer logs` |
| **`gh` not on the unit's PATH** | Service ran and failed instantly | `catnip timer logs -n 30` |
| **Stats endpoints still cold** | Code-frequency/contributors views empty on a young repo | Normal for a first run; re-run and compare |
| **Analysis stage failed** | One TUI panel empty, others fine | `catnip verify`, then `catnip analyze` and read stderr |
| **Rate limit exhausted mid-run** | Partial run, some repos missing detail | `manifest.json` → `rate_remaining_after` near 0 |
| **Data gap from pruning** | `all` timeframe has a hole | `coverage` in `traffic_daily.json`; check whether pruned runs were ingested |

## Anti-patterns, in this domain

- **Do not re-run `catnip run` to "see if it fixes itself"** before
  capturing state. A new run rewrites the newest-run pointer and can
  mask the evidence for what the previous one did.
- **Do not delete a run directory during an investigation.** If the
  history store is also incomplete, that run may hold the only copy of
  its traffic days.
- **Do not trust "the timer is on."** Enabled says nothing about whether
  the last five runs succeeded. `catnip timer status` and the journal do.
- **Do not conclude "GitHub is wrong"** because catnip's numbers differ
  from the web UI. GitHub's own graph shows a 14-day window and revises
  recent days upward; catnip's `all` reads the history store. Compare
  like for like — `catnip view traffic` at `2w`.
- **Do not read source before Tier 1 clears.** Config and command output
  only.

## Phase files

| Phase | File | Ends with |
|---|---|---|
| 1 | @references/phase-01-intake-coordinates.md | `COORDINATE MISMATCH` or `COORDINATES VERIFIED` |
| 2 | @references/phase-02-investigate.md | A surviving hypothesis with definitive evidence |
| 3 | @references/phase-03-remediate-document.md | A fix, a verification, and a written artifact |
