# Metrics and data layout

Everything catnip writes lives under `CATNIP_DATA_DIR`
(`~/.local/share/catnip` by default).

```
runs/<UTC stamp>/
  manifest.json    what this run did: owner, counts, rate spend, duration
  raw/             one JSON body per endpoint per repo, exactly as returned
  analysis/        22 CSVs, 1 JSON, 3 markdown — the schema everything reads
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
| `github_commit_daily.csv` | repo × day | daily commit counts — what the attribution view reads for push causes |
| `github_top_repos.csv` | repo | the ranked shortlist, by traffic and by activity |
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

`doctor.TUI_CSVS` lists the seventeen the TUI cannot open without.
`catnip verify` checks them; a deep-traffic stage that fails inside
`catnip analyze` is only a warning, so that check is what turns a
silently empty panel into a visible failure.

Most of the TUI no longer reads any of these. Every windowed number comes
from the history store via `derive.py`; the CSVs answer the questions the
store does not hold — languages, pull requests, code frequency, and the
path taxonomy GitHub only ever exposes as a rolling snapshot. That split
is why the viewer still works after `catnip prune` has removed every run.

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

Everything below is store-scoped instead, and lives in
`src/catnip/derive.py` — one implementation per formula, shared by the
TUI, `catnip view`, and `catnip report`, so the three can never disagree
about the same day. `[?]` in the TUI prints these definitions from
`DERIVATIONS` rather than from prose, which is what keeps this page and
the screen in step. `catnip view why --timeframe <view>` prints the same
thing headlessly.

**Anomaly severity** — a modified z-score on each repo's daily series:
`0.6745 × (value − median) / MAD`. MAD rather than standard deviation
because a single scanner flood inflates a standard deviation enough to
hide everything else, including itself.

MAD has one failure mode that matters here: a series that is mostly
identical days has `MAD = 0`, and every score divides to zero — a 421-clone
day against a median of 3 scored 0.00 and never appeared. When MAD is
zero the mean absolute deviation is used instead, scaled by 1.2533 so the
two agree on normal data. A `Z_MIN_VALUE` floor then drops days too small
to be interesting whatever their score, because on a quiet repo 1 clone
against a median of 0 is arithmetically extreme and substantively
nothing.

**Account events** — same-day anomalies across three or more repos are
collapsed into one event. A release wave moves the whole account at once,
and reporting it as fifteen independent findings buries the one repo that
moved for its own reasons.

**Audience** — is this repo's traffic people or fetchers? A weighted mean
over four components: clones-per-unique-visitor ratio (0.45), burst
concentration (0.2), views-per-visitor depth (0.2), and referrer spread
(0.15). Components without evidence are **withheld and the weights
renormalized**, never scored zero — a repo with six days of data has no
burst measurement, and calling that 0.0 would report a finding where
there is only an absence of one.

**Clone intent** — Laplace-smoothed, on uniques:
`(uniq_cloners + 1) / (uniq_visitors + 2)`, banded into `developer`,
`tooling` and `reference`. Smoothing matters at the small counts that
dominate a personal account: without it, 1 cloner and 0 visitors is an
infinite ratio. Below ten combined uniques the repo is `low-signal`
rather than any band.

**Attribution** — for each material move, the release or push within two
days before it, tiered `direct`, `coupled`, `account`, `dip`,
`unexplained` or `no-effect`. `unexplained` is a first-class outcome, not
a fallback: most traffic has no visible cause, and a tool that always
names one is fitting noise.

**Coupling** — Pearson correlation of daily series after **residualizing**
each repo against its expected share of the account-wide daily total.
Without that step every pair of repos in an active account correlates,
because they all rise on the days the account is busy; what survives is
the pair that moves together for its own reasons.

**Momentum and depth** — first difference, fitted slope, and rate against
the previous window; and `(docs + code + tree) / home` as a measure of how
far past the front door traffic actually got.

## The history store

`stats/history/traffic_daily.json` is the reason catnip runs on a timer.

```json
{
  "schema_version": 2,
  "owner": "octocat",
  "fetches_ingested": ["20260804T031500Z", "20260805T031722Z"],
  "coverage": [["2026-07-23", "2026-08-05"]],
  "events_era": "reconstructed from starred_at/created_at timestamps (all-time)",
  "repos": {
    "catnip": {
      "clones": {"2026-07-23": [4, 3]},
      "views":  {"2026-07-23": [31, 12]}
    }
  },
  "events": {
    "catnip": {
      "pushes":   {"2026-07-23": 7},
      "releases": {"v0.1.0": "2026-07-23"}
    }
  },
  "referrers": {"catnip": {"2026-07-23": {"news.ycombinator.com": [88, 59]}}},
  "stars_by_month": {"catnip": {"2026-07": 12}},
  "forks_by_month": {"catnip": {"2026-07": 2}}
}
```

Schema 2 added `events`, `referrers` and the star/fork series — the
inputs behind attribution, the referrer component of the audience score,
and the epoch view. Schema 3 added `paths`, dated the same way referrers
are: `/traffic/popular/*` is rolling, so paths that lived only in run
directories were destroyed by `catnip prune` and could never be
refetched. Only the top ten paths per repo are ever returned, so the
stored series is a biased sample — a page that never cracks the cut is
invisible, and one that drops out looks like it stopped rather than fell
below the line. Ingest skips runs already listed in
`fetches_ingested`, so a schema bump has to **backfill explicitly**:
declaring the new sections and waiting for the next run leaves them
permanently empty for every day already collected, and the views built on
them read as flat rather than as absent.

The star and fork series are reconstructed from event timestamps rather
than observed daily, so they run back to the account's first star while
the traffic series begins whenever collection did. `events_era` says so
in the file, because two series with different reaches in one store is
exactly the kind of thing a later reader assumes away.

Absence is never rendered as zero. A repo with no recorded push or
release on a moved day reports *no cause recorded in the store*, which is
a different statement from *nothing shipped* — and the store cannot tell
the two apart for any day it was not running.

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
