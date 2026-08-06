---
name: phase-03-remediate-document
description: "Fix it, prove the fix, assess whether traffic days were permanently lost, and write the artifact."
metadata:
  parent: catnip-triage
---

# Phase 3: Remediation and Artifact

Entered from either verdict: a coordinate mismatch (catnip was right, the
observation was in the wrong place) or a confirmed root cause.

## Step 1: Remediate

### Coordinate mismatches

The system needs no change. Fix the coordinate, and fix the reason it was
ambiguous — otherwise the same incident recurs next month.

| Mismatch | Fix | Prevent the recurrence |
|---|---|---|
| Edited a config that is not in effect | Move the edits into the live file | Delete the stray config, or set `CATNIP_CONFIG` in the user's shell profile so there is one answer |
| Timer reads a different config than the shell | `catnip timer install` re-pins the current one | Show them `catnip timer print \| grep CATNIP_CONFIG` as the check |
| Two data directories | Decide which is canonical; the other's runs can be moved in **before** anything is deleted | One config, one `CATNIP_DATA_DIR` |
| Wrong owner | Correct `CATNIP_OWNER`; note that prior runs collected a different account and their history entries are that account's | Set `CATNIP_OWNER_TYPE` explicitly too |
| Repo filtered out | Adjust the exact key named in `reports/skipped.tsv` | `catnip fetch --dry-run` before trusting a selection change |
| Reading a stale run | `catnip tui` with no argument | Remove the hardcoded path from whatever script had it |
| Timeframe confusion | `t` cycles 1d → 1w → 2w → all | Explain that `all` reads the history store and only grows with time |
| UTC vs local | None needed — it did run | Show `catnip runs` and translate one stamp with them |

### Confirmed faults

| Root cause | Fix | Verify |
|---|---|---|
| Token lost `repo` scope | `gh auth refresh --scopes repo` | `catnip doctor` → `traffic access` PASS |
| Fine-grained token missing permission | Add **Administration: read** | same |
| No push access to a repo | Nothing to fix — that repo cannot yield traffic. Consider excluding it | It stops appearing in `traffic-denied.tsv` |
| Timer never fires (lingering) | `sudo loginctl enable-linger $USER` | `loginctl show-user $USER --property=Linger` |
| `gh` not on the unit's PATH | Adjust `Environment=PATH=` in the unit, then `catnip timer install` to re-render | `systemctl --user start catnip.service` and read the log |
| Analysis stage failed | `catnip analyze` and read stderr; if reproducible, it is a bug — file it with the stderr and the run's `manifest.json` | `catnip verify` all PASS |
| Rate limit truncated the run | Narrow `CATNIP_INCLUDE`, or set `CATNIP_FETCH_STATS=false` | Next run's `rate_remaining_after` stays healthy |
| Runs collected but never analyzed | `catnip analyze <run>` for each, then `catnip history` | `history store` check reports 0 un-ingested |

## Step 2: Assess data loss — do this every time

This is the step specific to catnip, and it is not optional. GitHub
serves ~14 trailing days and nothing older, so any window where
collection was broken may be permanently gone.

```bash
python3 -c "
import json,sys
s=json.load(open(sys.argv[1]))
print('coverage:', s.get('coverage'))
print('runs ingested:', len(s.get('fetches_ingested', [])))
" "$(catnip config | awk '/history_file/{print $3}')"
```

| Finding | Meaning | Action |
|---|---|---|
| `coverage` is one contiguous range through today | No loss | State that plainly |
| `coverage` has a gap | Days in the gap are **unrecoverable** if older than 14 days | Say so; do not imply a re-run can fix it |
| Gap is inside the last 14 days | Still recoverable **right now** | Run `catnip run` immediately, before the window slides |
| Runs exist on disk covering the gap, un-ingested | Recoverable from disk | `catnip analyze <run>` then `catnip history` |

A gap that is still inside the window is the one case in this skill where
speed matters more than documentation. Collect first, write it up after.

## Step 3: Verify the fix

Verify with commands, not with confidence.

```bash
catnip doctor            # every check PASS
catnip run               # a full pipeline end to end
catnip verify            # every artifact present
catnip timer status      # next elapse in the future, if a timer is involved
```

| Result | Action |
|---|---|
| All pass | Close the investigation |
| The original symptom persists | The hypothesis was wrong. Return to Phase 2 Step 4 with this as contradicting evidence |
| A new symptom appears | New incident, new artifact. Do not merge them |

## Step 4: Write the artifact

The session ends; the artifact persists. Write it even for a coordinate
mismatch — especially for one, because the confusion is what recurs.

```markdown
# {YYYY-MM-DD} — {short symptom}

## Executive summary
{2–3 sentences: what was reported, what was actually true, what changed.}

## Verdict
COORDINATE MISMATCH | ROOT CAUSE CONFIRMED

## Findings

### F1: {title}
| Field | Value |
|---|---|
| Evidence | {command output, file path, manifest field} |
| Confidence | DEFINITIVE / CONFIG / SOCIAL / ANECDOTAL |
| Exposure window | {UTC start} → {UTC end} |
| Data lost | {none / N days, N repos, unrecoverable} |
| Remediation | {exact commands run} |
| Verified by | {command and its result} |

## Timeline
| Time (UTC) | Event | Source |
|---|---|---|

## Data-loss assessment
{coverage before and after; whether any days are permanently gone}

## Open actions
| Priority | Action | Owner |
|---|---|---|
| Critical | | |
| High | | |
| Medium | | |
```

## Step 5: Close the loop on catnip itself

If the investigation exposed a gap in catnip rather than in the user's
setup, say which and file it:

| Observation | What to file |
|---|---|
| `catnip doctor` did not catch a problem it could have | A new check, with the fix line it should print |
| A failure was silent until much later | Where the check belongs — usually `doctor.TUI_CSVS` or a stage's exit code |
| The fix required knowledge not in the docs | A line in `docs/configuration.md` or `docs/automation.md` |
| A coordinate was genuinely ambiguous | Whether catnip could print the coordinate itself — most of `doctor`'s output exists because of exactly this |

Include the run's `manifest.json` and the `catnip doctor --json` output
in the issue, redacted. Between them they carry nearly everything a
maintainer would otherwise ask for.

## Artifact Checkpoint

**File**: `sessions/{YYYY-MM-DD}-{slug}.md`

**Sections completed**:

- Executive summary and verdict
- Findings, each with evidence, confidence, exposure window, remediation
- Timeline, updated with the investigation's own events
- Data-loss assessment — explicitly, even when the answer is "none"
- Verification commands and their results
- Open actions, prioritized

**Investigation is complete when** every open action is closed or
assigned, and `catnip doctor` passes.
