# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`derive.py` — every windowed number, computed from the durable daily
  store alone.** Windows, deltas and rates, audience components, clone
  intent, per-repo/per-day modified-z, residualized coupling, and funnel
  depth. Pure functions, offline-testable, and the home of `DERIVATIONS`:
  the `[?]` overlay text lives beside the formula it describes, so an
  explanation cannot drift from the code that produced the number.
- **Repo drilldown (`[space]` on any repo row; `[enter]` also opens).** A full-screen view of
  one repo: dual daily chart with inline anomaly markers and release/push
  rules drawn under the days that caused a spike, the uniques track with
  the audience ratio, the repo's own funnel mix and depth, its coupled
  repos, and its PR/release/commit activity. `j`/`k` steps to the next
  repo in the list it was opened from. Every repo-row view is now a
  launcher into it.
- **Account events are a pane, not a caption.** The anomaly view's
  campaign rows became a focusable, scrollable, sortable list (`[tab]`
  to focus, `s`/`S` to sort by severity, breadth or date), and `[space]`
  opens an event detail screen: which repos moved that day, each one's
  |Z|, value and median, and whatever release or push it followed. Each
  row carries a colour block in the severity of its worst repo.
- **`catnip report` — a deterministic markdown analysis of the store.**
  Headline totals and change, biggest movers with trend, what moved and
  what caused it, account events, audience, clone intent, coupled repos,
  content depth, and an explicit section on what it cannot tell you.
  Every figure is tagged `measured` and the same store plus timeframe
  produces byte-identical output. Writes to
  `<data>/reports/<UTC stamp>/report.md` with a `meta.json` recording the
  store's latest day, so a report is attributable.
- **An interval guard with the store as the primary gate.** It refuses to
  write again until the store's newest day advances — catnip collects
  daily, so two reports over identical data are one finding printed
  twice. A one-day clock floor applies only when an older report did not
  record which day it covered; when the store can answer, the clock is
  not consulted, because a new day is new information an hour later as
  much as a week later. `--force` overrides for prototyping.
- **`catnip-prowl` agent skill.** Runs the deterministic report, then
  hunts: forms hypotheses, tests them against the raw data, and reports
  refutations as well as findings. Its output is a separate `prowl.md`
  beside the reproducible `report.md`, and every claim is tagged
  `measured`, `inferred` or `speculative`. A reader must never have to
  guess whether a sentence is arithmetic or an agent's idea.
- **Attribution view (`5`), replacing code frequency.** value <-> cause
  <-> repo: what moved, and the release or push that plausibly caused it,
  with the paired form (a cause in one repo explaining movement in a
  repo coupled to it). Built as a timeline rather than an anomaly table
  on purpose — GitHub returns a repo's whole release history on the
  first fetch, so it answers "what did I ship and what happened" from run
  one, before there is variance to score, a previous window to difference
  or aligned days to correlate. The statistical tiers appear as the store
  deepens; the view is never blank because it never depended on them.
  Every row is labelled with its tier, because an observed cause and an
  inferred one are different claims:
  `direct` (this repo shipped and moved), `coupled` (a correlated repo
  shipped; the partner and its r are named), `account` (a wave, no single
  cause), `dip` (deliberately unattributed), `unexplained`, `no-effect`
  (you shipped and nothing followed — silence is a result).
- **`[space]` on an attribution row opens the finding.** Not the repo —
  a row is a claim about one day, so the thing to inspect is the claim.
  Shows the effect series with the day marked, the statistics the tier
  rests on (median, MAD, meanAD, z, and the materiality floor it cleared
  or did not), the borrowed cause for `coupled` rows with both series
  and the raw-vs-residual gap, and for `no-effect` rows what this repo's
  earlier releases actually drew. Then context: who else moved that day,
  this repo's other findings in the window, its audience and intent, its
  busiest page. A tier is a judgement from thin evidence and the operator
  should be able to overturn it here rather than take it on trust.
- **`[space]` on a deltas row opens a momentum view.** Level, the
  day-over-day first derivative drawn around a zero line, a least-squares
  slope, and this window's rate against the previous one. A deltas cell
  says a repo moved by N; it cannot say whether the move is still
  happening, so a repo that spiked once and stopped looks identical to
  one climbing steadily. leather reads "falling -1.15/day, decelerating"
  while its window is still 7.6x the previous — both true, and only
  visible together. `[enter]` still opens the generic drilldown.
- **The funnel is two tables, and now behaves like it.** `[space]` walks
  down into the selected repo's pages and back out; each pane keeps its
  own sort, cursor and scrollbar; and the grid gained a `uniq` column
  (per-page uniques summed, documented as an upper bound because GitHub
  cannot dedupe a reader across paths). Sorting pages by uniques rather
  than views is what separates a readership from one client: 88 views
  from 59 people beats 92 views from 1.
- **`[space]` on a correlated pair opens both repos side by side** — each
  daily series, each residual series, the share of the account each
  accounts for, and a plain sentence about what residualizing changed. A
  correlation table gives a number and no way to check it; this shows the
  data the r was computed on.
- **The funnel names its shading and lists real pages.** The four fill
  weights were an unexplained texture; they now carry a legend (share of
  that row's largest category) and the panel beneath the grid lists the
  selected repo's actual top pages with views, uniques and paths. A
  category mix says what kind of page was read and can never say which.
- **Horizontal scrolling on the anomaly grid** (`h`/`l` or arrows). At
  the `all` and `epoch` timeframes 54 days do not fit as labelled
  columns; the grid now shows as many legible, individually dated
  columns as the terminal allows and scrolls, instead of shrinking to
  one character per day with a label every third column.
- **`[?]` derivation overlay on every view** — formula, thresholds, input
  columns, and whether the inputs are raw counts or uniques. Withheld
  components are named rather than scored zero.
- **Audience view (`2`) — human traffic versus fetcher fleets.** Combines
  clones-per-unique-visitor (log-scaled, the primary discriminator),
  burst concentration, browsing depth, and referrer diversity into a
  tunable composite. Classification labels the row and never filters the
  store: a crawler wave is itself a signal that something published.
- **Store-only operation.** The viewer runs with no run directory at all,
  which is what `catnip prune` eventually leaves behind. New
  `--history-file`, `--stats-file` and `--store-only` flags.
- **`github_commit_daily.csv`** — `stats/commit_activity` returns seven
  daily counts per week and `analyze.py` was collapsing them to the
  weekly total, discarding the only daily authorship signal catnip
  collects. It is what lets the drilldown put a cause under a spike.
- **Store schema 2**: a dated `referrers` section (GitHub's referrer
  endpoint is rolling, so observations are stored under the day they were
  observed) and an `events` log of releases and commit-days per repo.
  Both additive; a version-1 store gains them on its next ingest and is
  never rewritten.
- **`is_fork` / `is_private` in `github_repos.csv`**, and a global repo
  scope (`o` cycles owned / all / forks) applied to every view. A fork's
  commit history and language bytes are upstream's; "your languages" was
  describing other people's projects, and "most commits" was ranking
  them. Nothing is filtered when fork status is unknown, because "we
  cannot tell" must not silently become "not a fork".
- `catnip view why --timeframe <view>` prints a derivation as text.
- Scrollbars on every long list, and `[tab]`-selectable top-views /
  top-clones lists on the traffic screen so the landing view's rankings
  open the drilldown like any other repo row.
- `tests/test_derive.py` and `tests/test_tui_drilldown.py`; the TUI smoke
  suite now visits every view at every timeframe, opens and closes the
  drilldown from every row view, and opens the overlay on each screen.
- Demo GIF (`docs/media/demo.gif`), embedded at the top of the README.
- `tests/test_tui_layout.py`: renders every view at six terminal sizes
  through a recording `put` and asserts nothing lands on the header or
  footer row.

### Changed

- **Defaults now answer the common question first.** Audience and clone
  intent hide low-signal repos, deltas hides repos with no traffic in the
  window, the table opens filtered to repos with traffic and sorted by
  clones, and the anomaly view opens on material events only. Two thirds
  of a personal account is repos with under ten unique visitors; showing
  them first buried the dozen that can actually be described.
- **Selected rows highlight the name cell and mark the right edge**
  rather than inverting the whole row — these views end in bars,
  sparklines and grid cells, and reverse video across them destroyed the
  shape the row existed to show.
- **Sorts show their direction** (`clones ↓`) and `S` flips it. "sort:
  score" with the smallest value on top reads as a bug whatever the
  reason for it.
- **Every footer key is one the current screen can act on.** `[enter]repo`
  over an empty list and `[f]ilter` with one category were a legend of
  small lies the operator had to test individually.
- **Code frequency respects `t/T`.** It charted all 52 weeks GitHub
  returns whatever the selector said, so "last 7 days" drew nine years.
  Weeks that overlap the window count, since commit_activity is weekly,
  and the title says how many weeks that turned out to be.
- **Deltas gained a sort** (movement, clones, views, Δclones, Δviews,
  rate, name) under the same `s`/`S` contract as every other view.
- **One sort contract everywhere: `s` picks the column, `S` picks the
  direction.** Each column carried its own natural direction, so cycling
  columns silently reversed the order — and the audience view opened
  "sorted by score" with 0.00 at the top. Numeric columns now all open
  biggest-first, text columns A-Z, and the flip persists across column
  changes.
- **Vertical rhythm across every list view** — legends, column headers
  and data no longer stack with no separation, and the drilldown's
  sections are spaced. Funnel columns carry readable labels (`home`,
  `docs`, `tree`) instead of four-character truncations of their internal
  names, and its selection no longer inverts a whole row of grid cells.
- **The header claimed a scope the views were not applying.** Fork status
  comes from a run's repo CSV; a run predating the `is_fork` column
  leaves it unknown, `in_scope()` correctly filters nothing — and the
  header still printed `[owned]`, so language bytes and commit counts
  from sixty-three forks were presented as though curated. The tag now
  says `[owned: needs catnip analyze]` when the data cannot support it.
- **A schema bump relabelled the store without filling it.** `ingest`
  skips runs already in `fetches_ingested`, so a store upgraded to
  schema 2 declared `referrers` and `events` and then never populated
  them — the drilldown's release and push rules and the audience view's
  referrer diversity stayed permanently empty, with nothing on screen to
  explain why. Both merges are max/union and therefore idempotent, so
  ingest now backfills them from already-ingested runs on disk. Traffic
  is deliberately not re-merged: `fetches_ingested` is what keeps ingest
  cheap.
- **The scrollbars never rendered.** They were drawn at `max_x - 1`, and
  `TuiApp._put` rejects any `x >= max_x - 1`, so every one was silently
  dropped. The offline layout harness did not reproduce it because its
  fake `put` was unbounded — it now clips exactly like the real one, so a
  test can no longer pass against a canvas no terminal has.
- **`summary.md` was still headed "sh-github Analytics"** — the
  predecessor's name, written into every run since the rename.
- **The owner fallback was the literal string `github`**, which reads
  like an account name. It now falls back to the durable store's owner —
  which a run without a manifest cannot supply but the store always can —
  and says `(owner unset)` when nothing knows.
- **Zero and never-collected are distinguishable everywhere.** A store
  with no event log says so instead of reporting `0 commits`; unmeasured
  table columns render `-`; a repo with too little traffic to classify is
  `low-signal`, not `mixed`.
- **Deltas (`6`) rewritten onto the daily store.** It differenced two
  snapshots of GitHub's rolling totals, which lose their oldest day every
  night — so it reported window artifacts as change, including large
  negative clone deltas for repos where nothing had happened, and it
  ignored the timeframe selector entirely. Δ is now
  `sum(current N days) − sum(previous N days)` with a per-day rate, it
  respects `t`/`T`, and a row says `n/a` rather than inventing a
  comparison when the store does not yet reach back a second window.
- **Anomaly view (`7`) is a repo × day heatmap**, sorted by peak severity,
  with **campaign collapsing**: three or more repos spiking on one day
  fold into a single account-event row and their cells dim. The operator's
  own launches are the dominant anomaly source, and one release afternoon
  previously read as forty independent events. Per-day detail moved to the
  drilldown.
- **Clone intent (`8`) now uses uniques and Laplace smoothing**,
  `(uniq_cloners + 1) / (uniq_visitors + 2)`, and the 200 cap is gone — it
  truncated the ordering exactly where the signal was, so every fetcher
  fleet tied at "200.0 developer". Label thresholds were recalibrated for
  the smoothed scale (the old cuts labelled ten of eleven ranked repos
  "developer"). A repo scoring developer while the audience view
  classifies it a crawler is flagged CONFLICT in-row.
- **Correlation (`9`) residualizes before correlating.** Each repo's
  expected share of the day's account-wide total is subtracted first, so
  the surviving pairs are coupled fetch behaviour rather than the release
  wave agreeing with itself. Lag output is suppressed below 30 aligned
  days.
- **Funnel (`0`) is per-repo**: a row-normalized repo × category heatmap
  plus a `depth_ratio` leaderboard — deep views over overview views, "did
  anyone get past the front door". It previously mixed every repo into one
  set of category totals.
- **Table (`3`) gained derived columns** — momentum, audience, depth,
  stars-per-unique-visitor — all sortable, and it lists repos the newest
  run no longer has but the store still remembers.
- **The code-frequency view was deleted**, its slot taken by attribution.
  It charted weekly commits across every repo, which answered "how much
  did I type" and never "did any of it matter". Its data is not lost:
  daily commit counts are what attribution uses as causes.
- **The top-repos view was deleted.** Ranking by age and stars answered
  nothing the table did not answer better; its useful remnants are the
  table's new columns. The audience view took slot `2`; every other view
  kept its digit.
- **The history view was deleted**, folded into the traffic view as the
  `epoch` stop on the `t`/`T` cycle. It duplicated view 1 with a longer
  window.
- Windowed totals and daily series now read the durable store at *every*
  timeframe rather than only at `all`, so a window means the same thing on
  day 1 and day 600.

### Changed (development)

- **`make quick` (~4s) alongside `make check` (~45s, was ~103s).** The pty
  smoke was 96% of the wall clock — it spawns real terminals and sleeps
  0.35s per keypress — and one test spent ~38s re-proving a view x
  timeframe cross-product that `test_tui_layout` already asserts offline
  against a character grid, ~400x faster. The pty suite now covers only
  what a terminal can uniquely prove: that curses does not raise.
- **`docs/metrics.md` rewritten for the store-first architecture.** It
  documented the run-scoped CSV scores and stopped there, which was the
  whole of the derived layer when it was written and is now the smaller
  half. It covers `derive.py`'s definitions — the meanAD fallback and
  materiality floor, campaign collapsing, the withheld-and-renormalized
  audience weights, Laplace-smoothed intent, attribution tiers,
  residualized coupling — and the schema-2 store with its events,
  referrers and reconstructed star/fork series.

### Fixed

- **`esc` quit the application from inside a focused pane.** `[space]`
  and `[tab]` move *into* a pane — the funnel's top-pages list, the
  anomaly account-events table, the traffic repo lists — but pane focus
  was not a layer on the escape stack, so escape fell through to the
  framework's quit and closed the TUI from a screen the operator was
  still reading. Escape now unwinds one layer at a time: overlay, search,
  pane focus, then quit. Found only because a demo recording exited
  mid-scene and the remaining scripted keys landed at the shell prompt.
- **Three of five detail views advertised a truncated key hint** —
  "[j/k] next findin", "next pai", "next rep". Each call site carried its
  own `max_x - <literal>` offset and the literals had drifted from the
  strings they positioned; the column is now derived from the text. The
  funnel's explainer had the same fault from the other side, one sentence
  long enough that 150 columns cut it at "so it ove"; its clauses are now
  dropped whole, and the rolling-14d caveat moved into the title because
  it was the first clause to be shed and it is the one that changes how
  every number on the screen is read.
- **`catnip report` was advertised in the help and never dispatched** —
  it printed "unknown command" and then listed itself among the available
  commands. Found by running `catnip-prowl`, whose first phase is to run
  the report. A test now parses the usage block and asserts every
  advertised command has a matching case arm.
- **A chart could promise a marker it did not draw.** A bar carrying both
  `peak` and `value_labels` had the label written over the marker, so a
  title reading "▲ marks this day" showed none. Fixed upstream in pane:
  the marker is load-bearing and the value is already legible from the
  axis.
- **No path was ever classified `overview`, on any repo.** GitHub's
  popular-paths endpoint reports a repo's landing page as `/owner/repo`;
  `classify_path` matched only `/` and the bare `/tree/main` forms, so
  every front door fell into `other`. `depth_ratio` is deep views over
  overview views, which made it a bare count of deep views for the whole
  account and displayed every repo as though nobody had ever landed on
  its front page. With the fix, leather reads 2.42 — its docs and code
  outdraw its landing page — which matches the ~2:1 that the redesign
  brief independently measured by hand.
- **MAD anomaly detection was blind to the largest events in the
  account.** `modified_z_score` returned 0.00 whenever the MAD was zero,
  which is exactly the shape of the events worth detecting: a repo sits at
  zero clones for eleven days and then takes 421 in an afternoon, so its
  median and MAD are both 0. On live data that silenced the single biggest
  spike in the store. Both the CSV writer and the TUI now fall back to
  `(x − median) / (1.253314 × meanAD)`, which scores that same day 8.7,
  and both gained a materiality floor so that one clone against a baseline
  of zero — statistically enormous, practically nothing — no longer fills
  the table.
- History view drew its stars-per-month x-axis labels onto the footer
  (`[q]uit 24-05load`), and at short heights the daily-clones chart
  overflowed too. Charts are now sized against the space that actually
  remains — reserving the axis-label row `bar_chart` needs — and are
  dropped rather than floored to a height that does not fit. The drilldown
  applies the same rule and degrades to sparklines at the 60x16 floor.

## [0.1.0] - 2026-08-05

### Added

- Initial public release, generalized from a private single-account
  analytics pipeline into a tool any GitHub user can configure.
- `catnip` CLI: `init`, `doctor`, `config`, `run`, `fetch`, `analyze`,
  `history`, `totals`, `verify`, `tui`, `view`, `summary`, `runs`,
  `prune`, `timer`.
- Configuration layer (`CATNIP_*` keys, one resolution order shared by
  the bash fetcher and the Python pipeline), with repo include/exclude
  globs and fork/archived/private toggles.
- Owner auto-detection from the authenticated `gh` account; support for
  both personal accounts and organizations.
- systemd **user** timer (`catnip timer install`) with `Persistent=true`,
  plus a cron fallback (`catnip timer cron`).
- `catnip doctor`: environment, credential, scope, traffic-access, and
  data checks with `--json` output for agents.
- `catnip prune`: retention that refuses to delete any run whose traffic
  days are not yet in the history store.
- Run manifests (`manifest.json`) recording owner, selection counts,
  rate-limit consumption, and traffic denials per run.
- Test suite: config resolution, repo selection, retention invariants,
  systemd unit rendering, an offline end-to-end pipeline over synthetic
  fixtures, and a pty smoke test that drives every TUI view at three
  terminal sizes.
- `.agents/skills/`: `catnip-onboarding` (directed configuration
  workflow) and `catnip-triage` (abductive diagnostic workflow).
- Curses TUI vendored on [pane](https://github.com/TGPSKI/pane).
