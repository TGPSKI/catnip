---
name: phase-01-intake-coordinates
description: "Tier 1 fast path: capture the symptom, extract facts, and resolve which config, account, run, and window the reporter was actually looking at."
metadata:
  parent: catnip-triage
---

# Phase 1: Intake and Coordinate Resolution

Ends with one of two verdicts. Most catnip incidents end here.

**Configuration and command output only in this phase.** No reading of
`src/catnip/*.py`.

## Step 1: Capture the environment before touching anything

```bash
catnip doctor --json > /tmp/catnip-doctor.json   # the whole environment
catnip config                                     # config in effect + every path
catnip runs                                       # every run, and whether analyzed
```

| Status | Action |
|---|---|
| All three succeed | Continue to Step 2 |
| `catnip: config error: …` | That is the finding. The message names the file and line. Go to Phase 3 |
| `catnip: command not found` | The user is not running what they think they are. Establish which checkout or `PATH` entry they mean, then restart |
| `doctor` exits non-zero | Expected when something is broken — the JSON is still complete. Continue |

If a timer is involved, capture its side too, before any remediation:

```bash
catnip timer status
catnip timer logs -n 50
```

## Step 2: Extract facts

Every claim, tagged. Quote the user; do not paraphrase into a diagnosis.

| Fact | Source | Tag |
|---|---|---|
| "the traffic view is empty" | user | STATED |
| "it worked last week" | user | ANECDOTAL |
| Newest run is 6 days old | `doctor.json` → `runs` check | DEFINITIVE |
| The user edited `~/catnip/catnip.conf` | user | STATED |
| Config in effect is `~/.config/catnip/catnip.conf` | `catnip config` | CONFIG |
| Which timeframe the TUI was on | — | MISSING |

## Step 3: Adversarial review

For each INFERRED fact: what is the simplest alternative that makes it
wrong?

For each MISSING fact, score 1–5 on "could this single fact close the
case?" Anything 4–5 must be resolved before proceeding. In this domain
the usual 5s are:

| MISSING fact | Why it closes cases |
|---|---|
| Which timeframe the TUI was showing | `1d` on a quiet account looks identical to broken |
| Which config file the user edited | Editing a file that is not in effect explains every "no effect" report |
| Whether the repo in question is in `reports/skipped.tsv` | Filtered-out repos are the most common "missing data" |
| Whether they ran `catnip` from a clone or from `PATH` | Two checkouts, two configs, two data directories |
| Local time vs UTC of the "missed" run | Run IDs are UTC; the timer often did fire |

## Step 4: Resolve the reader

**What did the reporter actually look at?** This must be evidenced, not
assumed from what catnip is configured to do. Ask directly if it is not
in the report:

> "Exactly what did you run, from which directory, and — if this was the
> TUI — which view and which timeframe were on screen? This often closes
> the case immediately."

| Reporter's method | Coordinate it fixes |
|---|---|
| `catnip tui` with no argument | Newest run, per the config in effect |
| `catnip tui /some/path` | That run specifically — possibly stale |
| `catnip view traffic` | Newest run, text, default timeframe `2w` |
| GitHub's own Insights → Traffic page | GitHub's 14-day window and its own rounding |
| Opened a CSV by hand | Which run directory? Which column? |
| "I looked at the dashboard" | UNRESOLVED — ask. Do not proceed |

## Step 5: Resolve the system path

Derived from configuration, independently of Step 4.

```bash
catnip config                              # config_file, data_dir, runs_dir, stats_file
catnip timer print | grep CATNIP_CONFIG    # what the TIMER reads
gh api user --jq .login                    # which account the token is
```

Record:

```
CONFIG IN EFFECT (shell) : {path or "(defaults)"}
CONFIG PINNED IN TIMER   : {path or "(none installed)"}
DATA DIR                 : {path}
NEWEST RUN               : {run id, UTC}
CONFIGURED OWNER         : {value or "(auto)"}
TOKEN BELONGS TO         : {login}
```

## Step 6: Compare — the gate

Check each coordinate. Any single mismatch resolves the incident.

| Coordinate | Mismatch test | If mismatched |
|---|---|---|
| Config file | The file the user edited ≠ config in effect | `COORDINATE MISMATCH` — their edits were never read |
| Config file (timer) | Timer's pinned config ≠ shell's config | `COORDINATE MISMATCH` — the timer collects with different settings |
| Data directory | Runs exist under a different root than `catnip config` reports | `COORDINATE MISMATCH` — two data dirs, so almost certainly two configs |
| Account | `CATNIP_OWNER` ≠ token login **and** the user did not intend an org | `COORDINATE MISMATCH` — collecting the wrong account |
| Repo selection | The "missing" repo appears in `<run>/reports/skipped.tsv` | `COORDINATE MISMATCH` — filtered on purpose; the file names the key |
| Run | The reporter passed a path to `catnip tui` | `COORDINATE MISMATCH` — reading a stale run |
| Timeframe | The TUI was on `1d`, or on `2w` while asking about history | `COORDINATE MISMATCH` — the window, not the data |
| Clock | A run exists at a UTC stamp matching the local time they expected | `COORDINATE MISMATCH` — it did run |
| None differ | — | `COORDINATES VERIFIED` → Phase 2 |

The two cheapest checks in this entire skill (substitute the real
`runs_dir` from `catnip config`):

```bash
grep -h "<repo-name>" ~/.local/share/catnip/runs/*/reports/skipped.tsv
grep -h "<repo-name>" ~/.local/share/catnip/runs/*/reports/traffic-denied.tsv
```

## Step 7: Ground truth

Before closing the gate, validate the CONFIG-derived conclusion against
the live system with a non-destructive read.

| Conclusion from config | Ground-truth check |
|---|---|
| "Traffic should be readable" | `gh api "repos/<owner>/<repo>/traffic/clones" --jq .count` |
| "The owner is correct" | `gh api "users/<owner>" --jq .type` |
| "The timer is scheduled" | `systemctl --user list-timers catnip.timer` |
| "The run has the data" | `head -3 <run>/analysis/github_traffic_timeseries.csv` |
| "The history has coverage" | `python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['coverage'])" <history_file>` |

A config-derived belief that fails its ground-truth check is a finding,
not a coordinate mismatch — carry it into Phase 2 as DEFINITIVE evidence.

## Artifact Checkpoint

**File**: `sessions/{YYYY-MM-DD}-{slug}.md`

**Sections completed**:

- Symptom, verbatim
- Fact table with STATED / INFERRED / MISSING tags
- Adversarial review, with MISSING facts scored and any 4–5 resolved
- Reporter's method (evidenced) and the system's paths (from config)
- Coordinate comparison block
- Ground-truth check and result
- **Verdict**: `COORDINATE MISMATCH` or `COORDINATES VERIFIED`

**Route**:

| Verdict | Next |
|---|---|
| `COORDINATE MISMATCH` | @phase-03-remediate-document.md — catnip is behaving correctly; the fix is to the user's coordinates, and the artifact still gets written |
| `COORDINATES VERIFIED` | @phase-02-investigate.md |
