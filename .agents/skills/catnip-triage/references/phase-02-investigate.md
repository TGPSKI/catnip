---
name: phase-02-investigate
description: "Tier 2: build the run timeline, generate competing hypotheses, and execute the cheapest discriminating check until one survives."
metadata:
  parent: catnip-triage
---

# Phase 2: Investigation

Entered only with a `COORDINATES VERIFIED` verdict from Phase 1. The
reporter looked in the right place and it is still wrong.

## Step 1: Build the timeline

catnip's timeline is unusually easy to reconstruct, because every run
writes a manifest and every run ID is a UTC timestamp. Build it before
arguing about cause.

```bash
catnip runs
for m in "$(catnip config | awk '/runs_dir/{print $3}')"/*/manifest.json; do
  python3 -c "
import json,sys
d=json.load(open(sys.argv[1]))
print(f\"{d['run_id']}  repos={d['repos_fetched']}/{d['repos_selected']} \"
      f\"denied={d['traffic_denied']} rate={d['rate_remaining_before']}->{d['rate_remaining_after']} \"
      f\"{d['duration_seconds']}s\")" "$m"
done
```

| Time (UTC) | Event | Source |
|---|---|---|
| 20260801T031500Z | Run: 24/24 repos, 0 denied, 5000→4790, 38s | `manifest.json` — DEFINITIVE |
| 20260802T031500Z | Run: 24/24, **18 denied**, 41s | `manifest.json` — DEFINITIVE |
| 2026-08-02 ~02:00 | User rotated their GitHub token | ANECDOTAL |
| 20260803T031500Z | No run | absence in `catnip runs` — DEFINITIVE |

What to look for in the sequence:

| Pattern | Reading |
|---|---|
| `repos_selected` drops between runs | The config changed, or repos were archived/renamed |
| `traffic_denied` jumps from 0 | Token scope lost, or SSO authorization expired |
| `rate_remaining_after` near 0 | The run was truncated; later repos have no detail |
| `duration_seconds` collapses | The run bailed early — check the fetch step |
| A missing day | The timer did not fire, or fired and failed |
| `owner` differs between runs | Two configs are in play — go back to Phase 1 |

## Step 2: Generate at least two hypotheses

Never carry one. Fill the table with priors before checking anything.

| Hypothesis | Prior | Supporting | Contradicting | Discriminating check |
|---|---|---|---|---|
| H1 Token lost `repo` scope | H | `traffic_denied` jumped to all repos; user mentions rotation | Metadata still fetched fine | `catnip doctor` → `token scopes` + `traffic access` |
| H2 Repos genuinely have no traffic | L | Small account | 18 repos going to zero on one day is not organic | `gh api repos/<o>/<r>/traffic/clones` directly |
| H3 Analysis stage failed | M | One empty panel | Other panels fine | `catnip verify`, then `catnip analyze` and read stderr |

Priors come from the timeline, not from vibes. A change that coincides
with the symptom's first appearance earns a High prior; a standing
condition that predates it earns a Low one.

## Step 3: Discriminate — pick the cheapest check

State the check, its expected outcomes, and its cost *before* running it.

| Check | Cost | Eliminates |
|---|---|---|
| `catnip doctor` | seconds, 4 API calls | Scope, access, freshness, artifact and timer hypotheses at once |
| `cat <run>/reports/traffic-denied.tsv` | free | Per-repo access vs global failure |
| `catnip analyze <run>` (re-derive) | seconds, no API | Every "the CSV is wrong" hypothesis — analysis is deterministic from `raw/` |
| `gh api repos/<o>/<r>/traffic/clones` | 1 call | catnip vs GitHub: if GitHub returns zero, catnip is right |
| `catnip timer logs -n 100` | free | Timer-side failures |
| `diff <(ls run-A/raw) <(ls run-B/raw)` | free | Which endpoints stopped coming back |

`catnip analyze` on an existing run is the highest-leverage check in the
project: `raw/` is immutable input, so re-analysis is deterministic and
costs no API quota. If the CSVs come out the same, the fault is upstream
in collection; if they come out different, something about the analysis
environment changed.

**Before concluding a check gave an unexpected result**, re-verify the
coordinate it operates in: the right run directory, the right owner, the
right data dir, the right UTC day.

## Step 4: Narrow

Execute. Update the table. Do not discard contradicting evidence —
record it and let it kill the hypothesis.

| Outcome | Action |
|---|---|
| One hypothesis survives with DEFINITIVE evidence | Root cause. Go to Phase 3 |
| Several survive | Return to Step 3 with the next-cheapest check |
| None survive | The cause is outside the current model. Go to Step 5 |

## Step 5: When nothing survives

Look at mechanisms the mental model omitted. In catnip these are the
usual suspects:

| Hidden mechanism | Symptom it explains |
|---|---|
| `stats/*` endpoints are computed asynchronously (HTTP 202, empty body) | Code-frequency and contributor views empty on a repo's first run or two, then fine, with no config change |
| Traffic merges take element-wise **max**, never sum | A day's count that "should" be higher after re-running: max of two snapshots is not their sum, and that is correct |
| Totals are a pure rebuild, never an accumulator | Totals dropping after a prune: they are recomputed from history plus surviving runs |
| History ingest requires an *analyzed* run | Runs collected but never analyzed are skipped by ingest, so `all` develops holes while `catnip runs` looks healthy |
| The newest run is chosen lexically by UTC stamp | A manually-copied or renamed run directory can become "newest" |
| A deep-traffic stage failure is only a warning in `analyze` | Exactly one empty TUI panel, everything else fine |
| GitHub revises recent traffic days upward for ~48h | catnip's newest day disagreeing with GitHub's page, then agreeing later |

If one of these fits, add it as a hypothesis with the check that would
confirm it, and return to Step 3.

## Step 6: Contain

If the fault is ongoing and destructive, stop the bleeding while
preserving evidence.

| Situation | Containment | Preserve |
|---|---|---|
| Runs are collecting garbage (wrong owner) | `systemctl --user stop catnip.timer` | Do **not** delete the bad runs yet |
| Disk filling | `catnip prune` (dry run first) | It already refuses to delete un-ingested runs |
| Token compromised | Revoke in GitHub, then `gh auth login` | `manifest.json` files record what was collected and when |
| History store looks corrupt | Copy it aside **before** any `--rebuild` | `stats/history/traffic_daily.json` may be the only copy of old days |

Never run `catnip history --rebuild` during an investigation without
copying the store first. Rebuild re-scans surviving runs only — days
whose runs were already pruned exist nowhere else.

## Artifact Checkpoint

**File**: `sessions/{YYYY-MM-DD}-{slug}.md`

**Sections completed**:

- Timeline table, UTC, sourced per row
- Hypothesis table with priors, evidence, and discriminating checks
- Each check: what was run, what came back, which hypotheses it killed
- Surviving hypothesis with its DEFINITIVE evidence
- Containment actions with timestamps, if any

**Next**: @phase-03-remediate-document.md
