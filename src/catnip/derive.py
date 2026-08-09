#!/usr/bin/env python3
"""Derived metrics over the durable daily store.

Every windowed number the TUI shows is computed here, and every one of
them is computed from `<data>/stats/history/traffic_daily.json` — never
from GitHub's rolling 14-day totals.

That is not a preference. The rolling totals are non-monotonic: their
left edge falls off every day, so the difference between two consecutive
snapshots of them is a mixture of "what happened since" and "what aged
out", with no way to separate the two. A view built on that difference
reports artifacts and reports them confidently — a repository whose clone
count "fell by 301" when nothing at all happened to it. The store is
append-only and max-merged, so a window over it means the same thing on
day 1 and on day 600.

Two conventions hold throughout:

- **Uniques, not raw counts, for anything about people.** Raw counts
  include the operator's own traffic and one crawler's repetition; the
  API cannot exclude either. `field=0` selects the raw count and
  `field=1` the uniques, and every human-behavior metric here passes 1.
- **An absent input is not a zero.** A component that cannot be computed
  (no referrer history, too few unique visitors for a per-visitor rate)
  is reported as None and dropped from its composite, whose weights are
  renormalized over what remains. Scoring it zero would say "this repo
  has no human audience" when the truth is "catnip has not observed
  enough to say".

`DERIVATIONS` holds the formula, thresholds and inputs for each derived
view; the TUI renders it as the `[?]` overlay. A score the operator
cannot explain from inside the TUI is a defect, so a new derived
quantity here is expected to arrive with its entry there.
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

#: Run directory stamp, e.g. 20260807T120728Z.
_STAMP = re.compile(r"^\d{8}T\d{6}Z$")

#: How long after a day closes GitHub is still adding counts to it. Measured,
#: not documented — see `settled_day` for the readings and `docs/metrics.md`
#: for the method. A day is reported only once this much time has passed
#: since its own UTC midnight close.
#:
#: A floor, not a default: `CATNIP_SETTLE_HOURS` can raise it when an account
#: settles slower than this one did, and cannot lower it. Lowering is the
#: change that silently reintroduces the bug — a shorter wait costs nothing
#: visible and publishes a third of a day's traffic as the whole of it.
SETTLE_HOURS_FLOOR = 36

#: How many days back of a fetch's payload are worth recording for the
#: settle measurement, beyond the wait itself. Three: enough observations
#: after the wait expires to show a day standing still, without logging the
#: whole 14-day window every night for days that provably never move again.
SETTLE_PROBE_MARGIN_DAYS = 3

#: Resolved `CATNIP_SETTLE_HOURS` from the config file. None until asked.
_CONFIG_SETTLE_HOURS = None


def _config_settle_hours():
    """`CATNIP_SETTLE_HOURS` as the config file resolves it, or "".

    The one thing this module reads besides a store. A knob only the
    environment honours is a coordinate mismatch: the operator edits the
    file, `catnip config` shows the new value, and every window keeps
    using the old one. Resolved once, because `settled_edge` is on the
    path of every windowed number.
    """
    global _CONFIG_SETTLE_HOURS
    if _CONFIG_SETTLE_HOURS is None:
        try:
            from catnip.config import Config, ConfigError
            try:
                _CONFIG_SETTLE_HOURS = Config.load().get("CATNIP_SETTLE_HOURS", "")
            except ConfigError:
                # A broken config file is `catnip doctor`'s to report. Here
                # it means one unreadable knob, not an unusable store.
                _CONFIG_SETTLE_HOURS = ""
        except ImportError:  # pragma: no cover - derive is importable alone
            _CONFIG_SETTLE_HOURS = ""
    return _CONFIG_SETTLE_HOURS


def settle_hours():
    """The effective settling wait, in hours.

    Read through a function rather than frozen at import so a test, a shell
    and a long-running TUI all see the same value from the same environment.
    """
    raw = os.environ.get("CATNIP_SETTLE_HOURS") or _config_settle_hours()
    try:
        return max(SETTLE_HOURS_FLOOR, int(raw))
    except (TypeError, ValueError):
        return SETTLE_HOURS_FLOOR


def settle_probe_days(hours=None):
    """How many days back a fetch records for the settle measurement."""
    hours = settle_hours() if hours is None else hours
    return -(-hours // 24) + SETTLE_PROBE_MARGIN_DAYS

#: How many trailing days GitHub's traffic endpoints serve. Documented, for
#: once: "the last 14 days". A day older than this cannot be requested again
#: by anyone, which makes it the one horizon past which the store's value is
#: final by construction rather than by measurement.
TRAFFIC_WINDOW_DAYS = 14

#: Trailing days per timeframe key. None means "the whole store".
WINDOW_DAYS = {"1d": 1, "1w": 7, "2w": 14, "all": None, "epoch": None}

METRICS = ("clones", "views")
RAW, UNIQ = 0, 1

# ---- audience calibration ----------------------------------------------------
# Tunable, and surfaced in the [?] overlay precisely so they can be argued
# with against live data rather than trusted.
RATIO_HUMAN = 0.5     # clones per visitor at or below this reads as pure audience
RATIO_CRAWLER = 20.0  # and at or above this as a fetcher fleet
DEPTH_BAND = (1.5, 8.0)   # views per unique visitor that a person plausibly browses
DEPTH_MIN_VISITORS = 3    # below this the per-visitor rate is one client, not a rate
BURST_MIN_DAYS = 7        # burst is meaningless when the window is a day or two
REFERRER_FULL = 5         # distinct referrers that count as fully diverse
AUDIENCE_WEIGHTS = {"ratio": 0.45, "burst": 0.20, "depth": 0.20, "referrers": 0.15}
AUDIENCE_BANDS = ((0.60, "audience"), (0.30, "mixed"), (0.0, "crawler"))
# Below this many unique cloners + visitors there is nothing to classify.
# A repo with five unique cloners and two unique visitors is not "between
# human and fetcher" — the composite simply has four data points and
# "mixed" reads as a finding when it is an absence of one. Same cutoff and
# same reasoning as INTENT_LOW_SIGNAL; the two views must not disagree
# about whether a repo has enough traffic to be described.
AUDIENCE_MIN_SIGNAL = 10

# ---- intent calibration ------------------------------------------------------
# The score is smoothed clones PER unique visitor — a rate, not a share, so
# it runs above 1. The old 0.5/0.2 cuts were written for a bounded 0-200
# scale and, applied here, label ten of eleven ranked repos "developer",
# which is a label carrying no information. 2.0 means "cloned twice for
# every person who looked".
INTENT_BANDS = ((2.0, "developer"), (0.75, "tooling"), (0.0, "reference"))
INTENT_LOW_SIGNAL = 10    # uniq clones + uniq views below this cannot rank

# ---- anomaly calibration -----------------------------------------------------
Z_EXTREME, Z_SIGNIFICANT, Z_MINOR = 3.0, 2.0, 1.0
# A materiality floor, and it is not optional. Most repos on a personal
# account sit at zero clones for a fortnight, so ONE clone is a colossal
# departure from their own baseline and scores |z| well past 3. Without a
# floor, "everything that ever moved" is an anomaly, ~30 repos qualify on
# an ordinary Tuesday, and campaign collapsing folds the whole account into
# one event every single day. Statistically extreme is not the same as
# worth waking up for.
Z_MIN_VALUE = 3           # a spike of fewer events than this is not material
CAMPAIGN_MIN_REPOS = 3    # simultaneous material spikes at/above this are one event

# ---- coupling calibration ----------------------------------------------------
COUPLE_MIN_DAYS = 7       # fewer aligned days than this is not a correlation
LAG_MIN_DAYS = 30         # and lag needs a month before it means anything
COUPLE_MIN_CLONES = 5     # ignore pairs of near-silent repos


# ---- store access ------------------------------------------------------------

def _repos(store):
    return (store or {}).get("repos") or {}


def store_days(store):
    """Every date observed in the store, oldest first."""
    return sorted({d for metrics in _repos(store).values()
                   for m in METRICS for d in (metrics.get(m) or {})})


def latest_day(store):
    """The newest date observed, settled or not. `settled_day` is what a
    window, a ranking or a freshness check should ask for."""
    days = store_days(store)
    return days[-1] if days else None


def fetch_time(stamp):
    """The UTC instant of a run stamp like `20260807T120728Z`, or None."""
    try:
        if not _STAMP.match(stamp):
            return None
    except TypeError:
        return None
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def fetch_day(stamp):
    """The UTC date of a run stamp, or None."""
    ts = fetch_time(stamp)
    return ts.date().isoformat() if ts else None


def latest_fetch(store):
    """The UTC instant of the newest fetch merged into the store, or None
    when no run stamp survives (a hand-built or imported store)."""
    times = [t for t in (fetch_time(s) for s in (store or {}).get("fetches_ingested") or [])
             if t]
    return max(times) if times else None


def latest_fetch_day(store):
    """The UTC date of the newest fetch merged into the store, or None."""
    ts = latest_fetch(store)
    return ts.date().isoformat() if ts else None


def settled_edge(fetched, hours=None):
    """The newest day a fetch at `fetched` saw finished, or None.

    `fetched` is a datetime or a run stamp. A day is finished only once its
    own UTC close is `hours` behind the read.
    """
    if isinstance(fetched, str):
        fetched = fetch_time(fetched)
    if fetched is None:
        return None
    hours = settle_hours() if hours is None else hours
    # The newest day whose close (D+1 at 00:00Z) is at or before the cutoff
    # is the day before the cutoff's own date, for every time of day.
    cutoff = fetched - timedelta(hours=hours)
    return (cutoff.date() - timedelta(days=1)).isoformat()


def settled_rows(rows, fetched, key="timestamp"):
    """`rows` minus the days that fetch had not finished counting.

    For the per-run analyses, which read GitHub's payloads straight out of a
    run directory and never see the store. The run's own stamp is the read
    time, so the same rule applies without a store to ask.

    The written CSVs are deliberately not filtered this way: they are the
    record of what GitHub returned, and `history.py` max-merges them, so a
    day trimmed at write time is a day the store can never be corrected by.
    Settling is a read-time rule.
    """
    edge = settled_edge(fetched)
    rows = [r for r in rows if isinstance(r, dict)]
    if edge is None:
        return rows
    return [r for r in rows if (r.get(key) or "")[:10] <= edge]


def settled_day(store, fetched=None):
    """The newest day whose counts GitHub had finished revising.

    GitHub's traffic window ends on the day the fetch runs, and that day is
    returned as a flat zero — the counts appear afterwards. They keep
    appearing for well over a day. Measured by re-reading one day out of
    four run directories, same 35 repos every read, as a share of what that
    day eventually settled at:

        read 12h after it closed:   33% of views,  47% of clones
        read 30h after it closed:  100%           100%
        read 36h after it closed:  100%           100%

    A second day bounds the far end: identical at +36h, +54h, +60h and
    +114h, so a day is final at 36 hours and stays final 78 hours later.
    Days older than the window were identical in every read. The late
    arrivals were not spread evenly: seven repos went from exactly zero to
    their full count, and four of them had been pushed on the day in
    question. Traffic to a freshly pushed repo is the part that lands late,
    which for a tool that ranks repos by recent traffic is the part that
    most distorts the ranking.

    Shares, not counts: the counts are account traffic, which GitHub shows
    only to the account's admin. When the value stopped changing is what
    fixes the constant.

    So the edge is `settle_hours()` behind the newest fetch, not a fixed
    number of calendar days: the same day-count means different amounts of
    settling depending on what time the timer runs, and this rule holds
    whatever hour it fires. GitHub documents none of this — it says only
    that clone and visitor information "update hourly" — so the constant is
    measured, and `docs/metrics.md` carries the measurement.

    Zero is a legal value for a settled day (this account recorded ten of
    them in June), so the edge never hunts for the last day with traffic:
    that would relabel a quiet Sunday as an unfinished one and slide every
    window a day left without saying so.

    `fetched` overrides the store's own stamps for a caller that knows the
    fetch it is reading (the TUI, pointed at one run directory). Returns
    None when the store holds nothing settled yet.
    """
    days = store_days(store)
    if not days:
        return None
    edge = settled_edge(fetched or latest_fetch(store))
    if edge is None:
        return days[-1]
    settled = [d for d in days if d <= edge]
    return settled[-1] if settled else None


def final_day(now=None):
    """The newest day no future fetch could revise.

    Deliberately not `settle_hours` behind anything: `settled_day` is
    already that far behind the newest fetch, so every day a report covers
    is older than the wait at the moment it is written, and a rule on the
    wait alone would be vacuous. Past the 14-day window no fetch can
    return the day at all, so the store's value is final by construction.

    Strictly past, not on the boundary — the endpoints sometimes return a
    fifteenth bucket. Wall-clock, not the store's newest fetch: a day that
    fell out of the window while collection was stopped is more final, not
    less.
    """
    now = now or datetime.now(timezone.utc)
    return (now.date() - timedelta(days=TRAFFIC_WINDOW_DAYS + 1)).isoformat()


def window(store, timeframe, end=None):
    """The dates in the trailing window for `timeframe`, oldest first.

    A calendar window, not the last N observations: skipping absent days
    would silently stretch "last 7 days" across a fortnight whenever the
    timer missed a night, and every rate computed from it would be wrong
    by exactly the amount nobody would notice.

    The window ends at `settled_day`, so the fetch day's half-counted
    bucket is outside every window rather than being the whole of `1d`.
    """
    days = store_days(store)
    if not days:
        return []
    end = end or settled_day(store)
    if end is None:
        return []
    n = WINDOW_DAYS.get(timeframe, 14)
    if n is None:
        return [d for d in days if d <= end]
    start = (date.fromisoformat(end) - timedelta(days=n - 1)).isoformat()
    return [d for d in days if start <= d <= end]


def previous_window(store, timeframe, end=None):
    """The window of the same length immediately before `window`.

    Returns [] when the store does not reach back far enough — the caller
    must then say "no comparison yet" rather than compare against a
    shorter span and call the difference a change.
    """
    cur = window(store, timeframe, end)
    if not cur:
        return []
    n = WINDOW_DAYS.get(timeframe, 14)
    if n is None:
        return []
    prev_end = (date.fromisoformat(cur[0]) - timedelta(days=1)).isoformat()
    prev_start = (date.fromisoformat(prev_end) - timedelta(days=n - 1)).isoformat()
    days = [d for d in store_days(store) if prev_start <= d <= prev_end]
    return days if len(days) >= n else []


def daily(store, repo, metric, field=RAW):
    """{day: value} for one repo and metric; {} when the repo is unknown."""
    slot = (_repos(store).get(repo) or {}).get(metric) or {}
    out = {}
    for day, val in slot.items():
        if isinstance(val, list):
            out[day] = val[field] if len(val) > field else 0
        else:
            out[day] = val if field == RAW else 0
    return out


def series(store, repo, metric, days, field=RAW):
    """Values for exactly `days`, in order, zero-filling unobserved days."""
    d = daily(store, repo, metric, field)
    return [d.get(day, 0) for day in days]


def total(store, repo, metric, days, field=RAW):
    return sum(series(store, repo, metric, days, field))


def account_series(store, metric, days, field=RAW):
    """Account-wide daily totals over `days`."""
    out = [0] * len(days)
    index = {day: i for i, day in enumerate(days)}
    for repo in _repos(store):
        for day, val in daily(store, repo, metric, field).items():
            i = index.get(day)
            if i is not None:
                out[i] += val
    return out


def repo_names(store):
    return sorted(_repos(store))


def active_repos(store, days, min_total=1):
    """Repos with any observed traffic in `days` — the default row set for
    every account-wide view. Ninety-eight rows of which eighty are empty
    is not a view of an account, it is a directory listing."""
    out = []
    for repo in repo_names(store):
        if sum(total(store, repo, m, days) for m in METRICS) >= min_total:
            out.append(repo)
    return out


# ---- referrers and events ----------------------------------------------------

def referrers_in(store, repo, days):
    """{referrer: [count, uniques]} observed for `repo` within `days`.

    None — not {} — when the store has no referrer history at all, so the
    audience composite can tell "no referrers" apart from "never looked".
    """
    section = (store or {}).get("referrers")
    if not section:
        return None
    seen = set(days)
    out = {}
    for day, table in (section.get(repo) or {}).items():
        if day not in seen:
            continue
        for ref, val in table.items():
            count, uniq = (val + [0, 0])[:2] if isinstance(val, list) else (val, 0)
            prev = out.get(ref, [0, 0])
            out[ref] = [max(prev[0], count), max(prev[1], uniq)]
    return out


def events_in(store, repo, days):
    """{'releases': [(day, tag)], 'pushes': {day: commits}} inside `days`."""
    section = ((store or {}).get("events") or {}).get(repo) or {}
    seen = set(days)
    releases = sorted((day, tag) for tag, day in (section.get("releases") or {}).items()
                      if day in seen)
    pushes = {day: n for day, n in (section.get("pushes") or {}).items() if day in seen}
    return {"releases": releases, "pushes": pushes}


# ---- statistics --------------------------------------------------------------

def median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return 0.0
    if n % 2:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def modified_z(values):
    """Modified z-score per element, robust to a series of mostly zeros.

    The textbook form is 0.6745 * (x - median) / MAD. On this data the
    MAD is frequently zero — a repo sits at zero clones for twelve days
    and then takes 541 — and dividing by it yields 0.0 for every day,
    scoring the largest events in the account as perfectly ordinary. The
    mean-absolute-deviation fallback (x - median) / (1.253314 * meanAD)
    is the standard remedy, and it is the difference between a heatmap
    that shows the release waves and one that shows nothing at all.
    """
    n = len(values)
    if n < 2:
        return [0.0] * n
    med = median(values)
    deviations = [abs(v - med) for v in values]
    mad = median(deviations)
    if mad > 0:
        return [0.6745 * (v - med) / mad for v in values]
    mean_ad = sum(deviations) / n
    if mean_ad > 0:
        return [(v - med) / (1.253314 * mean_ad) for v in values]
    return [0.0] * n


def pearson(xs, ys):
    """Pearson r; 0.0 when either series is constant (r is undefined then)."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sxx = sum((xs[i] - mx) ** 2 for i in range(n))
    syy = sum((ys[i] - my) ** 2 for i in range(n))
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _band(value, bands):
    for threshold, label in bands:
        if value >= threshold:
            return label
    return bands[-1][1]


# ---- B. windowed deltas and rates --------------------------------------------

def deltas(store, timeframe, end=None):
    """Per-repo windowed change, from the daily store.

        cur  = sum(daily, current N days)
        prev = sum(daily, previous N days)
        d    = cur - prev
        rate = cur / N   (per day)

    `comparable` is False when the store does not reach back a full second
    window; the delta is then not a change and must not be drawn as one.
    A negative clone delta is now only ever a genuine decline, which is
    the whole point of abandoning the snapshot diff.
    """
    days = window(store, timeframe, end)
    prev_days = previous_window(store, timeframe, end)
    comparable = bool(prev_days)
    n = max(1, len(days))
    out = {}
    for repo in repo_names(store):
        row = {"repo": repo, "days": days, "comparable": comparable, "window_len": n}
        touched = False
        for metric in METRICS:
            cur = total(store, repo, metric, days)
            prev = total(store, repo, metric, prev_days) if comparable else 0
            row[metric] = {
                "cur": cur,
                "prev": prev if comparable else None,
                "delta": (cur - prev) if comparable else None,
                "rate": cur / n,
                "uniq": total(store, repo, metric, days, UNIQ),
                "series": series(store, repo, metric, days),
            }
            touched = touched or cur or prev
        if touched:
            out[repo] = row
    return out


def first_difference(values):
    """Day-over-day change: the discrete first derivative of a series.

    The level answers "how much"; this answers "getting faster or
    slower", which is the question a delta column can only imply. Day one
    has no predecessor and is reported as 0 rather than dropped, so the
    result stays aligned with the days it describes.
    """
    return [0] + [values[i] - values[i - 1] for i in range(1, len(values))]


def trend_slope(values):
    """Least-squares slope of a series against its index, per day.

    A single number for "which way is this going", robust to the day-to-day
    noise that makes a first difference hard to read at a glance. Returns
    0.0 for a series too short or too flat to have a direction.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom <= 0:
        return 0.0
    return sum((i - mean_x) * (values[i] - mean_y) for i in range(n)) / denom


def momentum(store, repo, timeframe, end=None):
    """Everything the momentum view draws for one repo, per metric.

    `slope` is per day; `accel` is the slope of the first difference, so a
    repo can be falling but decelerating — which reads as "the drop is
    levelling off" and is invisible in a single delta.
    """
    days = window(store, timeframe, end)
    prev_days = previous_window(store, timeframe, end)
    out = {"days": days, "comparable": bool(prev_days)}
    for metric in METRICS:
        values = series(store, repo, metric, days)
        diffs = first_difference(values)
        prev_total = sum(series(store, repo, metric, prev_days)) if prev_days else None
        n = max(1, len(days))
        out[metric] = {
            "values": values,
            "diffs": diffs,
            "slope": trend_slope(values),
            "accel": trend_slope(diffs),
            "rate": sum(values) / n,
            "prev_rate": (prev_total / max(1, len(prev_days))) if prev_days else None,
            "total": sum(values),
            "prev_total": prev_total,
        }
    return out


# ---- A. audience: human vs fetcher -------------------------------------------

def _human_ratio(ratio):
    """Clones per unique visitor, log-scaled into a 0..1 human-ness score."""
    if ratio <= 0:
        return 1.0
    lo, hi = math.log10(RATIO_HUMAN), math.log10(RATIO_CRAWLER)
    return _clamp01(1.0 - (math.log10(ratio) - lo) / (hi - lo))


def _human_depth(views, uniq_visitors):
    """Views per unique visitor, scored as a band rather than a slope.

    "Bots don't browse" is true, but so is its mirror image, and the
    mirror image is what live data shows: a fetcher fleet with two unique
    visitors and 150 page views scores an enormous views-per-visitor
    while doing no browsing whatsoever. A person reads a few pages per
    visit. Both tails are inhuman, so the score peaks inside the band and
    falls away on either side, and it is withheld entirely below
    DEPTH_MIN_VISITORS, where the denominator is one client rather than a
    population.
    """
    if uniq_visitors < DEPTH_MIN_VISITORS:
        return None
    depth = views / uniq_visitors
    lo, hi = DEPTH_BAND
    if lo <= depth <= hi:
        return 1.0
    if depth < lo:
        return _clamp01(depth / lo)
    return _clamp01(hi / depth)


def audience(store, timeframe, end=None, weights=None):
    """Per-repo audience components and composite classification.

        ratio     = uniq_cloners / max(uniq_visitors, 1)   [primary]
        burst     = max_day_clones / window_clones
        depth     = views / uniq_visitors
        referrers = distinct referrers observed in the window

    Each maps to a 0..1 "human-ness" score; the composite is their
    weighted mean over whichever components are available. Classification
    labels the row and nothing else — the store is never filtered by it,
    because a crawler wave is itself a signal that something published.
    """
    weights = weights or AUDIENCE_WEIGHTS
    days = window(store, timeframe, end)
    long_enough = len(days) >= BURST_MIN_DAYS
    out = {}
    for repo in active_repos(store, days):
        clone_days = series(store, repo, "clones", days)
        clones = sum(clone_days)
        uniq_cloners = total(store, repo, "clones", days, UNIQ)
        views = total(store, repo, "views", days)
        uniq_visitors = total(store, repo, "views", days, UNIQ)

        ratio = uniq_cloners / max(uniq_visitors, 1)
        burst = (max(clone_days) / clones) if (clones and long_enough) else None
        refs = referrers_in(store, repo, days)
        diversity = len(refs) if refs is not None else None

        scores = {
            "ratio": _human_ratio(ratio),
            "burst": (1.0 - burst) if burst is not None else None,
            "depth": _human_depth(views, uniq_visitors),
            "referrers": (_clamp01(diversity / REFERRER_FULL)
                          if diversity is not None else None),
        }
        present = {k: v for k, v in scores.items() if v is not None}
        wsum = sum(weights.get(k, 0.0) for k in present)
        composite = (sum(v * weights.get(k, 0.0) for k, v in present.items()) / wsum
                     if wsum > 0 else 0.0)

        signal = uniq_cloners + uniq_visitors
        out[repo] = {
            "repo": repo,
            "ratio": ratio,
            "burst": burst,
            "depth": (views / uniq_visitors) if uniq_visitors else None,
            "referrer_diversity": diversity,
            "scores": scores,
            "missing": sorted(k for k, v in scores.items() if v is None),
            "score": composite,
            "signal": signal,
            "label": ("low-signal" if signal < AUDIENCE_MIN_SIGNAL
                      else _band(composite, AUDIENCE_BANDS)),
            "clones": clones,
            "uniq_cloners": uniq_cloners,
            "views": views,
            "uniq_visitors": uniq_visitors,
        }
    return out


# ---- D. clone intent ---------------------------------------------------------

def intent(store, timeframe, end=None, audience_rows=None):
    """Laplace-smoothed clone intent, on uniques.

        score = (uniq_cloners + 1) / (uniq_visitors + 2)

    Smoothing is what stops a repo with seven clones from tying one with
    541: with no prior, both are "1.00" and the ranking at the top of the
    list is noise. There is no cap — the previous 0-200 scale truncated
    exactly where the signal lives, so every fetcher fleet landed on
    200.0 and the ordering above it was lost.

    `conflict` marks a repo whose intent reads developer while the
    audience view classifies it as a crawler: the same clone count, read
    two ways, disagreeing. That is a flag, not a celebration.
    """
    days = window(store, timeframe, end)
    aud = audience_rows if audience_rows is not None else audience(store, timeframe, end)
    out = {}
    for repo in active_repos(store, days):
        uniq_cloners = total(store, repo, "clones", days, UNIQ)
        uniq_visitors = total(store, repo, "views", days, UNIQ)
        signal = uniq_cloners + uniq_visitors
        score = (uniq_cloners + 1) / (uniq_visitors + 2)
        label = "low-signal" if signal < INTENT_LOW_SIGNAL else _band(score, INTENT_BANDS)
        a = aud.get(repo) or {}
        out[repo] = {
            "repo": repo,
            "score": score,
            "raw_ratio": uniq_cloners / max(uniq_visitors, 1),
            "uniq_cloners": uniq_cloners,
            "uniq_visitors": uniq_visitors,
            "signal": signal,
            "label": label,
            "audience_label": a.get("label"),
            "conflict": label == "developer" and a.get("label") == "crawler",
        }
    return out


# ---- C. anomalies as a repo x day grid ---------------------------------------

def anomalies(store, timeframe, end=None, repos=None):
    """Modified-z per repo per day, shaped for a strip heatmap.

    Returns {'days', 'rows', 'campaigns'} where rows is
    [(repo, [{'z','metric','dir'} | None, ...])] aligned to days, and
    campaigns maps a day to the repos that spiked together on it.

    Collapsing simultaneous spikes matters because the operator's own
    launches are the dominant anomaly source on a personal account: one
    release afternoon produces forty rows in a flat table and reads as
    forty independent events, when it is one event with forty
    consequences.
    """
    days = window(store, timeframe, end)
    names = repos if repos is not None else active_repos(store, days)
    rows, campaigns = [], defaultdict(list)
    for repo in names:
        cells = [None] * len(days)
        for metric in METRICS:
            values = series(store, repo, metric, days)
            med = median(values)
            for i, z in enumerate(modified_z(values)):
                if abs(z) < Z_MINOR:
                    continue
                cell = {"z": z, "metric": metric,
                        "dir": "spike" if z > 0 else "dip",
                        "value": values[i], "median": med,
                        "material": abs(values[i] - med) >= Z_MIN_VALUE}
                cur = cells[i]
                # Materiality outranks magnitude. A view count going 1 -> 2
                # scores the same |z| as clones going 0 -> 80 on a series
                # with the same shape, and picking by |z| alone would label
                # the day with the trivial metric and hide the real one.
                if cur is None or (cell["material"], abs(z)) > (cur["material"], abs(cur["z"])):
                    cells[i] = cell
        rows.append((repo, cells))
        for i, cell in enumerate(cells):
            if (cell and cell["material"] and cell["dir"] == "spike"
                    and abs(cell["z"]) >= Z_SIGNIFICANT):
                campaigns[days[i]].append(repo)
    campaigns = {day: sorted(repos_) for day, repos_ in campaigns.items()
                 if len(repos_) >= CAMPAIGN_MIN_REPOS}
    return {"days": days, "rows": rows, "campaigns": campaigns}


def severity(z):
    a = abs(z)
    if a >= Z_EXTREME:
        return "extreme"
    if a >= Z_SIGNIFICANT:
        return "significant"
    if a >= Z_MINOR:
        return "minor"
    return None


# ---- attribution: value <-> cause <-> repo -----------------------------------

#: A release or push on day D is allowed to explain movement on D..D+LAG.
#: Traffic from an announcement arrives the same afternoon and trails off
#: over a day or two; beyond that the link is a story, not evidence.
ATTRIBUTION_LAG = 2

#: Confidence tiers, strongest first. The label is the point: an inferred
#: cause and an observed one must never render identically.
ATTRIBUTION_TIERS = ("direct", "coupled", "account", "dip", "unexplained",
                     "no-effect")


def _causes_by_day(store, repo, days):
    """{day: [(kind, detail)]} for one repo — releases and pushes."""
    events = events_in(store, repo, days)
    out = defaultdict(list)
    for day, tag in events["releases"]:
        out[day].append(("release", tag))
    for day, n in sorted(events["pushes"].items()):
        out[day].append(("push", f"{n} commit(s)"))
    return out


def attribution(store, timeframe, end=None, funnel_rows=None):
    """Rows tying movement to whatever plausibly caused it.

    Built as a timeline rather than an anomaly table on purpose. A brand
    new store has no variance to score, no previous window to difference
    and no aligned days to correlate — but GitHub returns a repo's whole
    release history on the first fetch, so "here is what you shipped and
    what the traffic did around it" is answerable from run one. The
    statistical tiers layer on as the store deepens; the view is never
    empty because it never depended on them.

    Every row carries its tier, because a cause observed in the same repo
    and a cause inferred from a coupled one are different claims.
    """
    days = window(store, timeframe, end)
    if not days:
        # Same keys as the full return below. A caller must never have to
        # know which branch produced its dict: this one omitted
        # `coupling_available`, so an empty window raised KeyError halfway
        # through drawing the attribution view — inside curses, where a
        # traceback takes the terminal with it. `catnip report` reads the
        # same key and would have died the same way.
        return {"days": days, "rows": [], "unexplained": [],
                "coupling_available": False}

    grid = anomalies(store, timeframe, end)
    cells = dict(grid["rows"])
    campaigns = grid["campaigns"]
    index = {day: i for i, day in enumerate(days)}
    causes = {repo: _causes_by_day(store, repo, days) for repo in cells}

    # Coupling is the most expensive input and the first to be unavailable
    # on a young store; ask for it once and tolerate its absence.
    couples = {}
    if len(days) >= COUPLE_MIN_DAYS:
        for pair in coupled(store, timeframe, end)["pairs"]:
            # Positive coupling only. An anti-correlated pair moves in
            # OPPOSITE directions, so a release in one is evidence against
            # a same-direction spike in the other, not for it — using |r|
            # here credited a repo's spike to a partner it moves away from.
            if pair["r"] >= 0.5:
                couples.setdefault(pair["a"], []).append((pair["b"], pair["r"]))
                couples.setdefault(pair["b"], []).append((pair["a"], pair["r"]))

    rows = []
    claimed = set()

    def cause_near(repo, day):
        """A cause in `repo` on day..day-LAG that could explain `day`."""
        i = index[day]
        for back in range(ATTRIBUTION_LAG + 1):
            j = i - back
            if j < 0:
                continue
            for kind, detail in causes.get(repo, {}).get(days[j], []):
                return kind, detail, back
        return None

    # 1. Movement first, each row labelled by the strongest cause found.
    for repo, repo_cells in cells.items():
        for i, cell in enumerate(repo_cells):
            if not cell or not cell["material"]:
                continue
            day = days[i]
            row = {"day": day, "repo": repo, "metric": cell["metric"],
                   "value": cell["value"], "median": cell["median"],
                   "z": cell["z"], "dir": cell["dir"], "via": None}
            # Only a spike gets a cause. A release does not cause a drop —
            # the drop after one is decay, and labelling it "release" reads
            # as though shipping cost you traffic. Dips are shown, and left
            # unattributed.
            if cell["dir"] == "dip":
                row.update(tier="dip", cause=None, detail="", lag=None)
                rows.append(row)
                continue
            own = cause_near(repo, day)
            if own:
                kind, detail, lag = own
                row.update(tier="direct", cause=kind, detail=detail, lag=lag)
                claimed.add((repo, day))
            else:
                partner = next(
                    ((other, r, cause_near(other, day))
                     for other, r in couples.get(repo, [])
                     if cause_near(other, day)), None)
                if partner:
                    other, r, (kind, detail, lag) = partner
                    # Name whose cause this is. Rendering the partner's
                    # release in this row's cause column reads as though
                    # THIS repo shipped it.
                    row.update(tier="coupled", cause=f"{kind}\u2197",
                               detail=f"{other}: {detail}", lag=lag, via=(other, r))
                elif repo in campaigns.get(day, ()):
                    row.update(tier="account", cause=None,
                               detail=f"{len(campaigns[day])} repos moved together",
                               lag=None)
                else:
                    row.update(tier="unexplained", cause=None, detail="", lag=None)
            rows.append(row)

    # 2. Causes that produced no measurable movement. On a young store this
    #    is most of the view, and it is still the honest answer to "what did
    #    I ship and what happened" — silence is a result.
    for repo, by_day in causes.items():
        for day, entries in by_day.items():
            if (repo, day) in claimed:
                continue
            i = index[day]
            after = series(store, repo, "clones", days[i:i + ATTRIBUTION_LAG + 1])
            rows.append({"day": day, "repo": repo, "metric": "clones",
                         "value": sum(after), "median": None, "z": 0.0,
                         "dir": "flat", "tier": "no-effect",
                         "cause": entries[0][0], "detail": entries[0][1],
                         "lag": None, "via": None})

    rows.sort(key=lambda r: (r["day"], -abs(r["z"]), r["repo"]), reverse=True)
    return {"days": days, "rows": rows,
            "unexplained": [r for r in rows if r["tier"] == "unexplained"],
            "coupling_available": bool(couples)}


def release_response(store, repo, metric="clones"):
    """What this repo's releases have historically been worth.

    A `no-effect` row says a release moved nothing. Whether that is
    disappointing depends entirely on what this repo's releases normally
    do, and the store knows: every past release with the traffic that
    followed it. Returns [] when there is no history to compare against,
    which is itself the answer for a repo shipping for the first time.
    """
    section = ((store or {}).get("events") or {}).get(repo) or {}
    releases = sorted((day, tag) for tag, day in (section.get("releases") or {}).items())
    # Through `window`, like every other store computation here: a release
    # from the last day or two would otherwise have its follow-on traffic
    # summed over days GitHub has not finished counting, and this repo's own
    # history is the baseline every other release is judged against.
    days = window(store, "all")
    if not releases or not days:
        return []
    index = {d: i for i, d in enumerate(days)}
    out = []
    for day, tag in releases:
        i = index.get(day)
        if i is None:
            continue
        after = series(store, repo, metric, days[i:i + ATTRIBUTION_LAG + 1])
        before = series(store, repo, metric, days[max(0, i - 7):i])
        out.append({
            "day": day, "tag": tag, "after": sum(after),
            "baseline": (sum(before) / len(before)) if before else 0.0,
        })
    return out


def finding_context(store, timeframe, row, end=None):
    """Everything known about one attribution row, for its detail view.

    Deliberately assembled here rather than in the view: the numbers a
    finding is judged on should come from the same place the finding did.
    """
    days = window(store, timeframe, end)
    repo = row["repo"]
    grid = anomalies(store, timeframe, end)
    cells = dict(grid["rows"])
    day = row["day"]
    i = days.index(day) if day in days else None

    metric = row.get("metric") or "clones"
    values = series(store, repo, metric, days)
    med = median(values)
    devs = [abs(v - med) for v in values]
    mad = median(devs)
    mean_ad = (sum(devs) / len(devs)) if devs else 0.0

    same_day = sorted(
        other for other, other_cells in cells.items()
        if other != repo and i is not None and i < len(other_cells)
        and other_cells[i] and other_cells[i]["material"])

    others = []
    for j, cell in enumerate(cells.get(repo) or []):
        if cell and cell["material"] and days[j] != day:
            others.append({"day": days[j], "metric": cell["metric"],
                           "z": cell["z"], "dir": cell["dir"],
                           "value": cell["value"]})

    partner = None
    if row.get("via"):
        other, r = row["via"]
        raw = pearson(series(store, repo, metric, days),
                      series(store, other, metric, days))
        partner = {"repo": other, "r": r, "raw_r": raw,
                   "values": series(store, other, metric, days)}

    return {
        "days": days, "values": values, "index": i, "metric": metric,
        "median": med, "mad": mad, "mean_ad": mean_ad,
        "material_floor": Z_MIN_VALUE,
        "same_day": same_day,
        "campaign": grid["campaigns"].get(day, []),
        "other_findings": others,
        "partner": partner,
        "release_history": release_response(store, repo, metric),
        "events": events_in(store, repo, days),
        "audience": (audience(store, timeframe, end) or {}).get(repo),
        "intent": (intent(store, timeframe, end) or {}).get(repo),
    }


# ---- E. coupled repos --------------------------------------------------------

def coupled(store, timeframe, end=None, metric="clones", limit=None):
    """Pearson r on residuals, after removing account-wide co-movement.

    A raw correlation over a fortnight of this account is dominated by
    one thing: release days, when everything moves at once. Those 0.99
    pairs are the launch wave correlating with itself, and no action
    follows from them. Subtracting each repo's expected share of the
    account's daily total leaves what is left after the wave — pairs that
    are fetched together for reasons of their own, which is the input to
    cross-linking a catalog.

    `lag_available` stays False below LAG_MIN_DAYS aligned days: a lead
    of one day measured over fourteen is a coin toss with a decimal point.
    """
    days = window(store, timeframe, end)
    if len(days) < COUPLE_MIN_DAYS:
        return {"days": days, "pairs": [], "lag_available": False,
                "reason": f"needs >= {COUPLE_MIN_DAYS} aligned days, have {len(days)}"}

    names = [r for r in active_repos(store, days)
             if total(store, r, metric, days) >= COUPLE_MIN_CLONES]
    account = account_series(store, metric, days)
    account_total = sum(account) or 1

    residuals = {}
    for repo in names:
        values = series(store, repo, metric, days)
        share = sum(values) / account_total
        residuals[repo] = [values[i] - share * account[i] for i in range(len(days))]

    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            r = pearson(residuals[a], residuals[b])
            if r == 0.0:
                continue
            pairs.append({"a": a, "b": b, "r": r, "n": len(days),
                          "raw_r": pearson(series(store, a, metric, days),
                                           series(store, b, metric, days))})
    pairs.sort(key=lambda p: -abs(p["r"]))
    if limit:
        pairs = pairs[:limit]
    return {"days": days, "pairs": pairs,
            "lag_available": len(days) >= LAG_MIN_DAYS, "reason": ""}


def coupled_for(store, timeframe, repo, end=None, top=4):
    """The repos most coupled to one repo — the drilldown's adjacency list."""
    result = coupled(store, timeframe, end)
    out = []
    for pair in result["pairs"]:
        if pair["a"] == repo:
            out.append({"repo": pair["b"], "r": pair["r"], "n": pair["n"]})
        elif pair["b"] == repo:
            out.append({"repo": pair["a"], "r": pair["r"], "n": pair["n"]})
    return out[:top]


# ---- F. funnel depth ---------------------------------------------------------

DEPTH_CATEGORIES = ("doc_blob", "code_blob", "src_blob", "dir_tree")
FRONT_DOOR = "overview"


def path_rows(store):
    """Popular paths from the store, in the shape the run CSV produced.

    The newest observation per repo, not a union across days: GitHub
    returns a rolling ~14-day top-10, so each stored day IS a complete
    snapshot of that window and stacking several would double-count the
    days they overlap. Reading the newest reproduces exactly what the run
    CSV said, with the one difference that matters — it survives `catnip
    prune`, which used to delete the only copy.

    Returns [] when the store predates schema 3, so callers fall back to
    the CSV rather than showing an empty funnel on an old store.
    """
    section = store.get("paths") or {}
    out = []
    for repo, by_day in section.items():
        if not by_day:
            continue
        newest = max(by_day)
        for path, value in by_day[newest].items():
            count = value[0] if len(value) > 0 else 0
            uniques = value[1] if len(value) > 1 else 0
            title = value[2] if len(value) > 2 else ""
            out.append({"repo_name": repo, "path": path, "title": title,
                        "count": str(count), "uniques": str(uniques),
                        "observed": newest})
    return out


def funnel_rows(store):
    """Classified path rows from the store, replacing traffic_funnel.csv.

    The CSV is `path_rows` plus a category, and the classifier is pure —
    so deriving it here costs one function call per path and removes the
    funnel's last dependency on a run directory surviving.
    """
    from catnip.traffic_funnel import classify_path
    return [dict(row, category=classify_path(row["path"]),
                 view_count=row["count"], unique_visitors=row["uniques"])
            for row in path_rows(store)]


def funnel_depth(funnel_rows):
    """Per-repo category mix and how far past the front door traffic got.

        depth_ratio = (doc_blob + code_blob + src_blob + dir_tree)
                      / max(overview, 1)

    One number for "did anyone read anything, or did they bounce off the
    README". Each observation is a rolling 14-day top-10 — GitHub offers
    no per-day path history — so this still does not respond to the
    timeframe selector, and the views that show it say so. What it no
    longer depends on is a run directory: `path_rows` serves the newest
    stored observation, so the funnel outlives `catnip prune`.
    """
    by_repo = defaultdict(lambda: defaultdict(int))
    uniq_by_repo = defaultdict(int)
    for row in funnel_rows:
        repo = row.get("repo_name", "")
        cat = row.get("category", "") or "other"
        if not repo:
            continue
        try:
            views = int(row.get("view_count") or 0)
        except (TypeError, ValueError):
            views = 0
        try:
            uniq = int(row.get("unique_visitors") or 0)
        except (TypeError, ValueError):
            uniq = 0
        by_repo[repo][cat] += views
        uniq_by_repo[repo] += uniq
    out = {}
    for repo, cats in by_repo.items():
        deep = sum(cats.get(c, 0) for c in DEPTH_CATEGORIES)
        front = cats.get(FRONT_DOOR, 0)
        total_views = sum(cats.values())
        out[repo] = {
            "repo": repo,
            "categories": dict(cats),
            "deep": deep,
            "front": front,
            "total": total_views,
            # Per-page uniques SUMMED. GitHub reports uniques per path and
            # gives no way to dedupe a person across paths, so a reader who
            # opened three pages counts three times: this is an upper bound
            # on distinct visitors, not a count of them. It is still the
            # right column next to views, because the ratio between them is
            # what separates one client hammering from a real readership.
            "uniq": uniq_by_repo[repo],
            "depth_ratio": deep / max(front, 1),
        }
    return out


# ---- the [?] overlay ---------------------------------------------------------

DERIVATIONS = {
    "audience": [
        "AUDIENCE — is this repo's traffic people or fetchers?",
        "",
        "  ratio     = uniq_cloners / max(uniq_visitors, 1)      [primary]",
        "  burst     = max_day_clones / window_clones",
        "  depth     = views / uniq_visitors",
        "  referrers = distinct referrers observed in the window",
        "",
        f"  ratio <= {RATIO_HUMAN} scores 1.0 (audience), >= {RATIO_CRAWLER} scores 0.0",
        "  (crawler); log-scaled between. burst scores 1-burst: crawlers land",
        f"  on push day, people spread out. depth peaks inside {DEPTH_BAND[0]}-{DEPTH_BAND[1]}",
        "  views/visitor and falls away on BOTH sides — 150 views from 2 unique",
        "  visitors is one client hammering, not deep reading. referrers score",
        f"  distinct/{REFERRER_FULL}.",
        "",
        "  score = weighted mean over available components",
        f"  weights: {', '.join(f'{k} {v}' for k, v in AUDIENCE_WEIGHTS.items())}",
        f"  label:  >= {AUDIENCE_BANDS[0][0]} audience, >= {AUDIENCE_BANDS[1][0]} mixed, else crawler",
        "",
        f"  low-signal when uniq_cloners + uniq_visitors < {AUDIENCE_MIN_SIGNAL}. Read it",
        "  as 'not enough evidence', not as a middling result: five unique",
        "  cloners and two unique visitors is four data points, and calling",
        "  that 'mixed' reports a finding where there is only an absence of",
        "  one. MIXED means the components genuinely disagree — some human",
        "  signal, some fetcher signal, on a repo with enough traffic to say.",
        "",
        f"  burst is withheld below {BURST_MIN_DAYS} days in the window, depth below",
        f"  {DEPTH_MIN_VISITORS} unique visitors, referrers when the store has no referrer",
        "  history. Withheld components are dropped and the weights renormalized —",
        "  never scored zero. Missing components are listed per row.",
        "",
        "  inputs: durable daily store, UNIQUES (field 1) except depth's",
        "  numerator and burst, which are raw counts.",
        "  Classification labels the row; it never filters the store.",
    ],
    "deltas": [
        "DELTAS — what changed, over the selected timeframe",
        "",
        "  cur  = sum(daily, current N days)",
        "  prev = sum(daily, previous N days)",
        "  d    = cur - prev",
        "  rate = cur / N        (per day)",
        "",
        "  N comes from the t/T timeframe selector. Both windows are read from",
        "  the durable daily store, never from GitHub's rolling 14-day totals:",
        "  those lose their left edge daily, so differencing two snapshots of",
        "  them mixes 'what happened' with 'what aged out' and manufactures",
        "  negative deltas for repos where nothing happened at all.",
        "",
        "  A row shows 'n/a' instead of a delta when the store does not yet",
        "  reach back a second full window. A negative delta here is a real",
        "  decline.",
        "",
        "  Every window ends on the settled day: the day before the newest",
        "  fetch. GitHub's bucket for the day a fetch runs is still filling",
        "  when the fetch reads it, so that day is in the store reading zero",
        "  everywhere and is outside every window here.",
        "",
        "  inputs: durable daily store, raw counts (uniques shown alongside).",
    ],
    "anomaly": [
        "ANOMALIES — which repo, which day, how far from its own normal",
        "",
        "  z = 0.6745 * (x - median) / MAD                    [when MAD > 0]",
        "  z = (x - median) / (1.253314 * meanAD)             [when MAD = 0]",
        "",
        f"  |z| >= {Z_EXTREME} extreme, >= {Z_SIGNIFICANT} significant, >= {Z_MINOR} minor.",
        "  Cell intensity is max |z| that day across clones and views; the glyph",
        "  says which metric drove it (c clones, v views, lowercase = dip).",
        "",
        "  The meanAD fallback is load-bearing: a repo that sits at zero for",
        "  twelve days and then takes 541 clones has a MAD of zero, and the",
        "  textbook formula scores its spike 0.00 — the largest events in the",
        "  account, reported as perfectly ordinary.",
        "",
        f"  MATERIALITY: a cell is an event only if it moves >= {Z_MIN_VALUE} from the",
        "  repo's median. Most repos sit at zero for a fortnight, so one clone is",
        "  statistically enormous for them; with no floor about thirty repos",
        "  qualify on an ordinary Tuesday. Immaterial cells still render, dimmed,",
        "  but never join a campaign.",
        "",
        f"  CAMPAIGNS: {CAMPAIGN_MIN_REPOS}+ repos spiking on one day fold into a single",
        "  account-event row and their cells dim. Your own launches are the",
        "  dominant anomaly source; 40 rows for one release afternoon reads as",
        "  40 independent events, which is the wrong number by 39.",
        "",
        "  inputs: durable daily store, raw counts, over the selected window.",
    ],
    "profile": [
        "CLONE INTENT — how many of the people who looked, cloned?",
        "",
        "  score = (uniq_cloners + 1) / (uniq_visitors + 2)     [Laplace]",
        "",
        "  Read it as smoothed clones per unique visitor. It is a RATE, not a",
        "  share: a repo cloned more often than it is browsed scores above 1,",
        "  and nothing caps it.",
        "",
        f"  >= {INTENT_BANDS[0][0]} developer, >= {INTENT_BANDS[1][0]} tooling, else reference.",
        f"  low-signal when uniq_cloners + uniq_visitors < {INTENT_LOW_SIGNAL}.",
        "",
        "  Uniques, not raw counts: raw clones include one crawler's repetition,",
        "  which is how a fetcher fleet used to top this list. Smoothing is why a",
        "  7-clone repo can no longer tie a 541-clone one — unsmoothed, both are",
        "  1.00 and the ranking at the top is noise. There is no cap; the old",
        "  0-200 scale truncated exactly where the ordering mattered.",
        "",
        "  A row marked CONFLICT scores developer here while the audience view",
        "  classifies it a crawler. Same clones, two readings, disagreeing.",
        "",
        "  inputs: durable daily store, UNIQUES, over the selected window.",
    ],
    "correlation": [
        "COUPLED REPOS — fetched together for reasons of their own",
        "",
        "  share_r    = sum(r, window) / sum(account, window)",
        "  residual_r = daily_r - share_r * account_daily",
        "  r          = Pearson(residual_a, residual_b)",
        "",
        "  Raw Pearson over a fortnight of one account is dominated by release",
        "  days, when everything moves at once: those 0.99 pairs are the launch",
        "  wave correlating with itself, and nothing follows from them.",
        "  Removing each repo's expected share of the day's account total",
        "  leaves co-movement that is not the wave.",
        "",
        f"  Needs >= {COUPLE_MIN_DAYS} aligned days. Lag is suppressed below {LAG_MIN_DAYS} days:",
        "  a one-day lead measured over fourteen is a coin toss with a decimal",
        f"  point. Pairs below {COUPLE_MIN_CLONES} window clones are skipped as noise.",
        "",
        "  inputs: durable daily store, raw clone counts, over the window.",
    ],
    "attribution": [
        "ATTRIBUTION — what moved, and what plausibly caused it",
        "",
        "  A timeline, not an anomaly table. GitHub returns a repo's whole",
        "  release history on the first fetch, so this view answers 'what did",
        "  I ship and what happened' from run one — before there is variance",
        "  to score, a previous window to difference, or aligned days to",
        "  correlate. The statistical tiers appear as the store deepens.",
        "",
        f"  A release or push explains movement for {ATTRIBUTION_LAG} day(s) after it.",
        "",
        "  TIERS, strongest first. The label is the point: an observed cause",
        "  and an inferred one must never read the same.",
        "",
        "    direct       this repo shipped, and this repo moved.",
        "    coupled      a repo correlated with this one shipped, and this",
        "                 one moved with no cause of its own. The partner and",
        "                 its r are named. Needs "
        f"{COUPLE_MIN_DAYS}+ days of store.",
        "    account      3+ repos moved together that day and no single",
        "                 cause is attributable to this one.",
        "    dip          a material fall. Deliberately unattributed: a",
        "                 release does not cause a drop, and labelling the",
        "                 decay after one 'release' reads as though shipping",
        "                 cost you traffic.",
        "    unexplained  material movement, no cause in the store.",
        "    no-effect    you shipped and nothing measurable followed.",
        "                 Silence is a result, and on a young store this is",
        "                 most of the view.",
        "",
        "  inputs: durable daily store — the events log for causes, daily",
        "  counts for effects, residualized coupling for the paired tier.",
        "  Absent event data (a store predating the events section) means no",
        "  causes, not 'nothing shipped'.",
    ],
    "funnel": [
        "FUNNEL — what people actually opened, and how deep they got",
        "",
        "  depth_ratio = (doc_blob + code_blob + src_blob + dir_tree)",
        "                / max(overview, 1)",
        "",
        "  'Got past the front door': above 1.0, more traffic reads content than",
        "  bounces off the landing page. The heatmap is row-normalized, so each",
        "  repo's MIX is comparable even when its volume is not.",
        "",
        "  uniq is per-page uniques SUMMED across the repo's pages. GitHub",
        "  reports uniques per path with no way to dedupe a person across",
        "  paths, so someone who read three pages counts three times — read it",
        "  as an upper bound, and read views/uniq as the real signal: 92 views",
        "  from 1 unique is one client, 88 from 59 is a readership.",
        "",
        "  TIMEFRAME DOES NOT APPLY. GitHub's popular-paths endpoint is a rolling",
        "  14-day snapshot with no dated history to store, so this view always",
        "  shows the newest run's window whatever t/T says. It is the one view",
        "  that cannot honour the selector, and this is why.",
        "",
        "  inputs: the run's github_traffic_funnel CSV, raw view counts.",
    ],
    "table": [
        "REPO TABLE — every repo, sortable by any derived column",
        "",
        "  momentum = clones delta over the selected window        [deltas]",
        "  audience = composite human-ness score, 0..1             [audience]",
        "  depth    = deep views / overview views                  [funnel]",
        "  stars/uv = stars / max(uniq_visitors in window, 1)",
        "",
        "  Momentum and audience come from the durable daily store and follow",
        "  t/T. Depth comes from the run's path CSV and does not (see the funnel",
        "  derivation). stars/uv is a stock over a flow — a rough 'how much",
        "  reputation per person who showed up', not a rate.",
        "",
        "  This view replaced the old 'top repos by criteria' screen, whose",
        "  criteria (age, stars) answered questions nobody was asking.",
    ],
    "traffic": [
        "TRAFFIC — daily clones and views, account-wide",
        "",
        "  Bars are daily totals from the durable daily store, summed across",
        "  every repo. The window follows t/T; at 'epoch' the whole store is",
        "  drawn and the stars-per-month chart appears beneath it.",
        "",
        "  Top lists are windowed sums over the same days, so a repo cannot",
        "  appear in a list under a window it did not earn.",
        "",
        "  uniques ~= distinct visitors. GitHub's API cannot exclude the repo",
        "  owner, so raw counts include your own traffic; uniques mostly do not.",
        "",
        "  inputs: durable daily store. GitHub's rolling totals are used only",
        "  for the all-time figures on the stats line, which is all they can",
        "  honestly support.",
    ],
    "drilldown": [
        "REPO DRILLDOWN — one repo, every derived series",
        "",
        "  Chart 1  daily views and clones, raw counts, from the daily store.",
        "           A ^ above a bar marks |z| >= 2 (see the anomaly derivation).",
        "           A | rule under the axis marks a release or a push that day,",
        "           from the store's event log — so a spike is drawn next to",
        "           its cause instead of merely near it.",
        "  Chart 2  uniques: unique cloners and unique visitors per day, with",
        "           the audience ratio (uniq_cloners / uniq_visitors) beneath.",
        "  Funnel   this repo's category mix and depth_ratio, from the run CSV",
        "           (rolling 14 days, does not follow t/T).",
        "  Coupled  residualized Pearson against every other active repo.",
        "  Activity PRs opened/merged, releases, and commit-days in the window.",
    ],
}


def derivation(view):
    """The [?] overlay text for a view; a short honest note when absent."""
    return DERIVATIONS.get(view) or [
        f"{view.upper()} — no derivation registered.",
        "",
        "This view shows collected data directly rather than a derived score.",
        "If it does compute something, that is a defect: a number the operator",
        "cannot explain from inside the TUI is exactly what this overlay exists",
        "to prevent.",
    ]
