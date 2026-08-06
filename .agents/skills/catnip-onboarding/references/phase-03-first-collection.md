---
name: phase-03-first-collection
description: "Collect the first run, verify every artifact exists, and read the data — including what is legitimately empty on a first run."
parent: catnip-onboarding
---

# Phase 3: First Collection

Produces: one run on disk that passes `catnip verify`, plus a history
store and totals.

**Carry forward from prior phases**

- The live config file path and owner, from Phase 2 (`catnip config`).
- The selected repo list the user approved in the Phase 2 dry run.

## Step 1: Collect

**Inspect** first — know the budget before spending it:

```bash
gh api rate_limit --jq .resources.core.remaining
```

Roughly 10 calls per repository with every optional endpoint on. If
remaining is less than `10 × repo count`, either wait for the hourly
reset or narrow the selection; a run that dies halfway leaves a partial
run directory that nothing downstream will trust.

**Generate**

```bash
catnip run
```

This is fetch → analyze → history → totals → verify. Expect a minute or
two for a few dozen repositories. Watch for:

| Output | Meaning |
|---|---|
| `N listed, M selected, K skipped by filters` | Matches the dry run. If not, the config changed |
| `NOTE: N repo(s) denied traffic data` | Those repos lack push access. Read `reports/traffic-denied.tsv`. Not fatal, but if N equals the repo count, go back to Phase 1 |
| `catnip run: FAILED at 'fetch'` | Network, credentials, or rate limit. The last 40 lines are printed |
| `All checks passed.` at the end | Phase essentially done — continue to Step 2 |

Do not interrupt a run to "try something". A partial run directory is
harmless (the next run makes a new one), but a half-ingested history
store is the one thing here with no clean recovery.

## Step 2: Verify the artifacts

**Inspect**

```bash
catnip verify
```

| Check | Not-PASS means | Action |
|---|---|---|
| `analysis` WARN, some CSVs missing | A deep-traffic stage failed inside analyze — it is only a warning there, which is why this check exists | `catnip analyze` again and read stderr |
| `history store` WARN, "not created yet" | The ingest step did not run | `catnip history` |
| `history store` WARN, "N run(s) not yet ingested" | Expected only if a run was collected but never analyzed | `catnip analyze` then `catnip history` |
| `totals` WARN | `catnip totals` | |
| Everything PASS | Continue to Step 3 |

## Step 3: Read the data, and explain what is empty

**Inspect**

```bash
catnip summary            # the run's own summary.md
catnip view traffic       # headless, no terminal requirements
catnip tui                # interactive — 'q' quits
```

**Decide** — a first run legitimately has holes. Say which, before the
user reports them as bugs:

| What looks broken | Whether it is |
|---|---|
| Code-frequency view is empty | Normal. GitHub computes `stats/*` asynchronously and returns an empty 202 on first contact. catnip warms them up and collects them later in the same run, but many repos only populate on the **second** run |
| The `all` timeframe looks the same as `2w` | Correct. The history store has exactly one run in it. It grows past GitHub's 14-day horizon only with time |
| Anomaly view is empty | Correct and good. MAD outlier detection needs a baseline; one run of 14 days rarely has one |
| Correlation and clustering are thin | Same reason — they need multiple repos with overlapping activity |
| A specific repo shows zero traffic | Check `reports/traffic-denied.tsv` first. Otherwise the repo genuinely had no clones or views in 14 days |
| Stars show but "stars by month" is empty | Star events need `CATNIP_FETCH_EVENTS=true` and a non-zero star count |

Walk the user through the TUI once: `1`–`=` jump to views, `t` cycles the
timeframe, `/` filters by repo name, `v` cycles forward, `q` quits.

## Step 4: Tell them where the data is

**Inspect**

```bash
catnip config | grep -E "data_dir|history_file"
catnip runs
```

Say plainly:

- Everything is under the data directory; nothing is sent anywhere.
- `stats/history/traffic_daily.json` is the copy that outlives GitHub's
  14-day window. If they back up one thing, it is that.
- If they collect private repositories, the data directory is as
  sensitive as those repositories are.

## Checkpoint

**Files written** (under `CATNIP_DATA_DIR`):

- `runs/<UTC stamp>/raw/`, `analysis/`, `reports/`, `manifest.json`
- `stats/totals.json`
- `stats/history/traffic_daily.json`, `snapshots.jsonl`

**Verify**:

```bash
catnip verify       # every data check PASS
catnip view traffic # real numbers
```

**Next phase**: @phase-04-automation.md — and do it now, in this session.
Every day between here and there is a day of traffic data that GitHub
will not serve again.
