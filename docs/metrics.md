# Metrics and data layout

Everything catnip writes lives under `CATNIP_DATA_DIR`
(`~/.local/share/catnip` by default).

```
runs/<UTC stamp>/
  manifest.json    what this run did: owner, counts, rate spend, duration
  raw/             one JSON body per endpoint per repo, exactly as returned
  analysis/        16 CSVs + summary.md — the schema everything else reads
  reports/         skipped.tsv, traffic-denied.tsv
stats/
  totals.json           account-wide rollup, rebuilt from scratch each run
  history/
    traffic_daily.json  the permanent daily series — never pruned
    snapshots.jsonl     append-only, one line per (run, repo)
```

Run IDs are UTC stamps (`20260805T031722Z`), so lexical order is
chronological. Nothing depends on file mtimes.

## The analysis CSVs

These are the contract. The TUI, the history store, and the totals all
read them; nothing downstream reads `raw/`.

| File | Grain | Key columns |
|---|---|---|
| `github_repos.csv` | one row per repo | 50+ columns: identity, counts, scores, traffic totals, `status`, `age_group` |
| `github_stats_by_repo.csv` | one row per repo | the subset used for cross-run snapshots and deltas |
| `github_traffic_timeseries.csv` | repo × metric × day | `repo_name, metric, timestamp, count, uniques` — the long-format series everything traffic-related derives from |
| `github_traffic_paths.csv` | repo × path | `path, title, count, uniques` |
| `github_traffic_referrers.csv` | repo × referrer | `referrer, count, uniques` |
| `github_traffic_velocity.csv` | repo | recent-vs-prior rate of change |
| `github_languages.csv` | repo × language | `bytes, bytes_percentage` |
| `github_lang_distribution.csv` | language | account-wide totals |
| `github_code_frequency.csv` | repo × week | `additions, deletions, total, commits` |
| `github_contributions.csv` | repo × contributor | `contributions, weeks, avg_weekly` |
| `github_pull_requests.csv` | PR | `state, merged, merged_at, created_at, closed_at` |
| `github_issues.csv` | issue | `state, created_at, closed_at, is_pr` |
| `github_releases.csv` | release | `tag_name, published_at, draft, prerelease` |
| `github_release_assets.csv` | asset | `asset, download_count` |
| `github_stargazer_events.csv` | star | `repo_name, starred_at` |
| `github_fork_events.csv` | fork | `repo_name, created_at` |
| `traffic_anomaly.csv` | repo × day | MAD-based outlier score and severity |
| `traffic_cloner_profile.csv` | repo | clone-intent score, ratios, weekend share, peak day |
| `traffic_funnel.csv` | repo × category | path taxonomy: overview / code / docs / releases |
| `traffic_correlation.csv` | repo pair | Pearson correlation of daily series |
| `traffic_clusters.json` | cluster | cosine-similarity grouping with centroids |

`doctor.TUI_CSVS` lists the sixteen the TUI cannot open without. `catnip
verify` checks them; a deep-traffic stage that fails inside `catnip
analyze` is only a warning, so that check is what turns a silently empty
panel into a visible failure.

## Derived metrics

**`traffic_score`** — a weighted blend, normalized against the busiest
repo in the run: clones 0.35, unique cloners 0.25, views 0.20, unique
viewers 0.10, distinct paths 0.05, recency 0.05. It answers "what is
getting attention right now", which is why unique cloners weigh nearly as
much as raw clones: one CI job cloning hourly should not outrank ten
different people.

**`activity_score`** — the same idea for development: stars 0.25, commits
0.30, contributors 0.15, forks 0.15, languages 0.05, recency 0.05.

Both are relative to the repos in that run. They rank; they do not
compare across accounts.

**Anomaly severity** — median absolute deviation on each repo's daily
series. MAD rather than standard deviation because a single scanner flood
inflates a standard deviation enough to hide everything else, including
itself.

**Clone intent** — the clones-to-views ratio, weekend share, and unique
ratio, bucketed into profiles like `bot-heavy` and `tooling`. A repo
cloned far more often than it is viewed is being consumed by machines.

## The history store

`stats/history/traffic_daily.json` is the reason catnip runs on a timer.

```json
{
  "schema_version": 1,
  "owner": "octocat",
  "fetches_ingested": ["20260804T031500Z", "20260805T031722Z"],
  "coverage": [["2026-07-23", "2026-08-05"]],
  "repos": {
    "catnip": {
      "clones": {"2026-07-23": [4, 3]},
      "views":  {"2026-07-23": [31, 12]}
    }
  }
}
```

Each day is `[count, uniques]`. Merges are **element-wise max**, never
sum: every run re-snapshots the same trailing window, and GitHub revises
recent days upward as its pipeline settles, so max is the settled value
and summing would inflate everything several-fold.

`coverage` records contiguous observed ranges. A gap longer than the
traffic window starts a new range, so a month when the timer was off
renders as a gap rather than as zeros.

## Totals

`stats/totals.json` is a pure output — regenerated from scratch every
time, never read back as input. It uses the history store as its base and
stitches on any runs not yet ingested. Alongside the account-wide sums it
carries `org_daily` (the daily series), `repo_snapshots` with per-repo
deltas since the previous run, 7-day and prior-7-day windows for
trend arrows, and `top_repos` by activity.

## Rate limit

Roughly 10 API calls per repository per run with every optional endpoint
enabled, against 5,000/hour for an authenticated token. The manifest
records the before and after:

```json
"rate_remaining_before": "5000",
"rate_remaining_after":  "4903",
"duration_seconds": 41
```

`stats/*` endpoints are the expensive part in wall-clock rather than
quota: GitHub computes them asynchronously and returns HTTP 202 with an
empty body on first contact. catnip fires all of them up front, does the
detail loop, then collects them at the end — which is why code-frequency
data often appears on a repo's second run rather than its first.
