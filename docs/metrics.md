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
    daily_snapshots.jsonl  each fetch's reading of each still-movable day,
                        before the merge. Bounded by CATNIP_SETTLE_LOG_DAYS
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
the screen in step. `catnip view why <view>` prints the same
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
  "schema_version": 3,
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
  "paths": {"catnip": {"2026-07-23": {"/octocat/catnip": [64, 37, "Overview"]}}},
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

### Settling: the newest days are not answers yet

GitHub's traffic window ends on the day the fetch runs, and it returns that
day as a flat zero. The counts appear afterwards, and they keep appearing
for well over a day.

Measured by re-reading the same day out of four run directories, over the
same 35 repos every time, so no repo entering or leaving the account
explains it. As a share of the value that day eventually settled at:

| one day, read | views | clones |
|---|---|---|
| 12h after it closed | 33% | 47% |
| 30h after it closed | 100% | 100% |
| 36h after it closed | 100% | 100% |
| 90h after it closed | 100% | 100% |

A second day bounds the far end: read at +36h, +54h, +60h and +114h it
returned the identical count every time, so a day is final at 36h and stays
final 78 hours later. A third day was at 93% by +12h, so 12h is short even
when the shortfall is small — how much lands late runs from a few percent
to two thirds.

Days older than the settling window were byte-identical in all reads. The
late arrivals were not spread evenly across repos: seven went from exactly
zero to their full count while the other 28 were already final at 12h, and
four of those seven had been pushed on the day in question. **Traffic to a
freshly pushed repo is the part that lands late** — which, for a tool whose
job is ranking repos by recent traffic, is the part that most distorts the
ranking.

The proportions are the evidence; the counts are account traffic, which
GitHub shows only to the account's admin, and they are not needed to fix the
constant. What fixes it is when the value stopped changing.

GitHub documents none of this. The API reference gives the window ("the last
14 days") and the bucket alignment ("Timestamps are aligned to UTC midnight
of the beginning of the day or week"); the traffic page says only that "full
clones and visitor information update hourly". Read plainly that implies a
closed day is complete within the hour, and the table above says otherwise.
So 36 hours is a measurement, not a specification.
`CATNIP_SETTLE_HOURS` raises the wait for an account that settles slower;
it cannot lower it below the measured floor, because a short wait costs
nothing visible and publishes part of a day as all of it.

### Re-measuring it: `catnip settle`

The table above was derivable once, by accident: three run directories
survived `catnip prune`, and their raw payloads held the same day at
different values. The store cannot answer the question — max-merge per
(repo, metric, day) keeps the settled value and discards every
intermediate one.

So `history.py` writes each fetch's reading of each still-movable day to
`stats/history/daily_snapshots.jsonl` before merging it, and `catnip
settle` reads them back:

```
  day          reads repos  clones         views          moving at  final by
  2026-08-05       4    35  194 -> 417     105 -> 320           12h        30h
  2026-08-06       4    35  14 -> 33       22 -> 68             12h        66h
```

The first row is the table above, re-derived from a different input by
different code: 194/417 clones is 47%, 105/320 views is 33%, at 12h, final
by 30h. The second is what a missed collection costs — no run on
2026-08-08 left a 53-hour gap, so that day's change places no tighter than
"after 12h, done by 66h".

Four rules keep it honest.

- **The same repos in every reading.** A day's totals are summed over the
  repos present in *all* of its readings. A repo added or dropped between
  fetches otherwise moves the total on its own, and that movement would
  read as GitHub still counting.
- **A repo-day with no traffic is written as a zero.** GitHub returns no
  row for one, so a repo going from nothing to its full count — most of the
  late arrival, concentrated on repos pushed that day — is absent from the
  earlier reading rather than zero in it. The zeros are filled for the
  repos each fetch covered, which also stops a newly collected repo from
  reading as a revision.
- **A change proves only the age of the reading it followed.** On a daily
  timer the readings of one day sit ~24h apart, so a value differing
  between a 19h read and a 43h read proves the day was unfinished at 19h
  and final by 43h, and says nothing about 36h. Both bounds are reported
  and the day classifies as `unresolved`; contradicting the wait requires
  the lower bound to reach it. A second daily collection would place it.
- **A day still open is not evidence.** The fetch-day bucket reads as a
  flat zero, so readings taken before a day closed are excluded from both
  bounds — otherwise every day contradicts every wait.

`catnip settle` exits non-zero only on a proven contradiction; `catnip
doctor` carries the same verdict as a `settling` check. The log is bounded
by `CATNIP_SETTLE_LOG_DAYS` (90) rather than by `catnip prune`, which now
keeps runs because their days may still be revised. It is the one artifact
catnip will delete: every day in it is in the store at its settled value,
so trimming loses only how long that took.

`derive.settled_day` is the newest day whose own UTC close is `SETTLE_HOURS`
behind the newest fetch. It is where every window ends, what the TUI header
names, what the report's `meta.json` records, and what the interval guard
compares.

Two properties worth stating, because both were bugs first:

- **The edge is hours, not a day count.** The same "two days back" means a
  different amount of settling depending on what hour the timer fires; a
  05:07 UTC run and a 23:00 UTC run on the same date have read the day
  before last at 29h and 47h respectively. An hours rule holds whatever the
  schedule.
- **The edge is never the last day with traffic.** A zero day can be a real
  zero — this account recorded ten in June — and trimming trailing zeros
  would relabel a quiet Sunday as an unfinished one and slide every window a
  day left without saying so.

Because a day can be corrected after it was reported, two consumers carry
the consequence. `catnip report` records a `window_digest` of the numbers it
stated and recomputes when the store no longer matches. `catnip prune`
keeps any run whose days may still be revised, ingested or not: the store
holds the merged result, but only the run directory holds what a particular
fetch saw, and `history --rebuild` reconstructs from surviving runs alone.

### Which copy of a report is the answer

A report is named for the period it covers, and each recomputation is
written beside the last:

```
reports/2026-08-05-2w.20260807T031722Z/   provisional
reports/2026-08-05-2w.20260809T031519Z/   provisional, recomputed
reports/2026-08-05-2w/                    settled
```

Promotion is **not** keyed on the settling wait. `settled_day` is already
`settle_hours` behind the newest fetch, so every day a report covers is
older than the wait the moment it is written, and a rule on the wait alone
would promote everything immediately. It is keyed on GitHub's 14-day
window: past that no fetch can return the day, so the store's value is
final by construction rather than by measurement. That takes about two
weeks, so the sweep runs on every `catnip report` invocation.

`meta.json` records `first_written` and `last_recomputed` separately, and a
recomputed report carries a `first written` row in its provenance table.
`catnip report --locate` resolves the paths; nothing outside `report.py`
should reproduce the naming rule.

### And the inference beside it

`prowl.md` quotes measured figures out of the same store and cannot notice
when they move: a finding is prose and a tier, not a recomputable query.
The tannery records each published cycle's window digest and re-runs the
cycle when it differs — `catnip report --digest --end <day>` recomputes it
over the same days, so it fires on a revision rather than on a new day
arriving. A cycle closes permanently once its window leaves the 14-day
window, the same horizon report promotion uses.

Collecting more often does not shorten any of this. A day closes at 00:00
UTC, so a fetch on the following day reads it at most 24h old however many
times it runs — the wait is GitHub's pipeline, not a sampling rate. A second
daily run buys redundancy against a failed collection, not a fresher window.

**Where the rule is applied.** The arithmetic lives once, in
`derive.settle_hours` and `derive.settled_edge`. It is applied at each
distinct source, and only there:

| source | applied at |
|---|---|
| the durable store | `derive.window` — the door every store computation goes through |
| the store, in the TUI | trimmed once when the store is loaded |
| a run's own CSVs, in the TUI | trimmed once, against that run's stamp |
| the stitched series in `totals.py` | the 7d / prior-7d window bounds |
| a run's raw payloads | `runfiles.daily`, shared by the four traffic analyses |

The written CSVs are deliberately **not** trimmed. They are the record of
what GitHub returned, and `history.py` max-merges them into the store — a
day trimmed at write time is a day the store can never be corrected by.
Settling is a read-time rule.

A run directory with no parseable stamp keeps every day it holds, the same
way a store with no `fetches_ingested` keeps its newest day. Copying or
renaming a run out of the data directory destroys the only record of when
it was read, and showing what it holds beats refusing to render it.

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
