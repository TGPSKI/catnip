#!/usr/bin/env python3
"""Measure how long GitHub keeps revising a day, from catnip's own readings.

`derive.SETTLE_HOURS_FLOOR` is 36. That is a measurement, not a
specification: GitHub documents no settling latency at all — only that
clone and visitor information "update hourly" — so the constant can rot
silently if GitHub's pipeline changes, and nothing else in catnip would
notice. It was derivable the first time only by accident, because three
run directories happened to survive `catnip prune` and could be compared.

The store cannot answer the question by construction. Merges are
element-wise max per (repo, metric, day), so the revision history is
destroyed on ingest: the store knows the final number and nothing about
how long it took to arrive. `history.py` therefore writes every reading
of every still-movable day to `daily_snapshots.jsonl` before merging, and
this module reads it back.

Two rules keep the answer honest:

- **The same repos in every reading.** A day's readings are summed over
  the repos present in *all* of them. A repo added or dropped between
  fetches otherwise moves the total on its own, and that movement would
  read as GitHub still counting.
- **A change is only proof at the age of the reading it followed.** With
  daily collection the readings of one day sit ~24h apart, so a value that
  differs between a 19h read and a 43h read changed *somewhere* in
  between. That proves the day was unfinished at 19h; it proves nothing
  about 36h. `still_moving_at` is the proven lower bound and `final_by`
  the upper one, and a wait is only contradicted when the lower bound
  reaches it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from catnip import derive
from catnip.config import Config, ConfigError

METRICS = derive.METRICS

#: Fewer readings than this says nothing about when a day stopped moving.
MIN_READINGS = 2


def load(path):
    """Every reading in the log, oldest fetch first. Unreadable lines are
    skipped: this file is a measurement, and a truncated tail is a reason
    to report less, never to fail a command that has real work to do."""
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("fetch") and row.get("day"):
                rows.append(row)
    rows.sort(key=lambda r: (r["day"], r["fetch"]))
    return rows


def day_close(day):
    """The instant a UTC day stops accepting traffic: its own midnight end."""
    return datetime.combine(date.fromisoformat(day) + timedelta(days=1),
                            datetime.min.time(), tzinfo=timezone.utc)


def age_hours(day, fetch_id):
    """How long after `day` closed the fetch read it. Negative while open."""
    when = derive.fetch_time(fetch_id)
    if when is None:
        return None
    return (when - day_close(day)).total_seconds() / 3600.0


def readings_by_day(rows):
    """{day: {fetch: {repo: {metric: [count, uniq]}}}}."""
    out = {}
    for row in rows:
        slot = out.setdefault(row["day"], {}).setdefault(row["fetch"], {})
        slot[row.get("repo", "")] = {m: row.get(m) or [0, 0] for m in METRICS}
    return out


def day_curve(day, fetches):
    """One day's readings, summed over the repos every reading contains."""
    common = None
    for repos in fetches.values():
        names = set(repos)
        common = names if common is None else (common & names)
    common = common or set()

    observations = []
    for fetch in sorted(fetches):
        totals = {m: 0 for m in METRICS}
        for repo in common:
            for m in METRICS:
                totals[m] += fetches[fetch][repo][m][0]
        observations.append({"fetch": fetch, "age_hours": age_hours(day, fetch),
                             "repos": len(common), **totals})

    curve = {"day": day, "repos": len(common), "readings": len(observations),
             "observations": observations}
    for metric in METRICS:
        curve[metric] = _bounds(observations, metric)
    return curve


def _bounds(observations, metric):
    """When this metric stopped moving, as a proven interval.

    `still_moving_at` is the age of the newest reading that was later
    contradicted — the last moment the day is *known* to have been
    unfinished. `final_by` is the age of the oldest reading that already
    held the final value with nothing disagreeing after it. The truth is
    somewhere between, and the gap is the collection cadence.

    Readings taken before the day closed carry a negative age and are
    excluded from both: a day still open is trivially unfinished, and
    saying so is not a measurement of GitHub's pipeline.
    """
    closed = [o for o in observations
              if o["age_hours"] is not None and o["age_hours"] >= 0]
    if not closed:
        return {"first": None, "final": None, "still_moving_at": None,
                "final_by": None, "readings": 0}
    final = closed[-1][metric]
    still_moving_at = None
    final_by = closed[-1]["age_hours"]
    for obs in closed:
        if obs[metric] != final:
            still_moving_at = obs["age_hours"]
    for obs in closed:
        if obs[metric] == final and (still_moving_at is None
                                     or obs["age_hours"] > still_moving_at):
            final_by = obs["age_hours"]
            break
    return {"first": closed[0][metric], "final": final,
            "still_moving_at": still_moving_at, "final_by": final_by,
            "readings": len(closed)}


def curves(rows):
    """Every day in the log with at least one reading, oldest first."""
    grouped = readings_by_day(rows)
    return [day_curve(day, grouped[day]) for day in sorted(grouped)]


def _widest(all_curves, field, days=None):
    """The largest `field` across both metrics, or None when never set."""
    seen = [c[m][field] for c in all_curves for m in METRICS
            if c[m][field] is not None and (days is None or c["day"] in days)]
    return max(seen) if seen else None


def verdict(all_curves, hours=None):
    """Does the log contradict the configured wait?

    Three outcomes, and the middle one is the common case on a daily
    timer. `proven_short` is a day observed still moving at or past the
    wait — that is a fact, and the wait is too low. `unresolved` is a day
    whose change lands inside a gap straddling the wait: the cadence
    cannot place it, and collecting twice a day would. `confirmed` is a
    day that already held its final value before the wait expired.
    """
    hours = derive.settle_hours() if hours is None else hours
    proven, unresolved, confirmed, thin = [], [], [], []
    for curve in all_curves:
        usable = max((curve[m]["readings"] for m in METRICS), default=0)
        if usable < MIN_READINGS:
            thin.append(curve["day"])
            continue
        lower = _widest([curve], "still_moving_at")
        upper = _widest([curve], "final_by")
        if lower is not None and lower >= hours:
            proven.append(curve["day"])
        elif upper is not None and upper > hours:
            unresolved.append(curve["day"])
        else:
            confirmed.append(curve["day"])
    return {
        "settle_hours": hours,
        "floor": derive.SETTLE_HOURS_FLOOR,
        "days_measured": len(confirmed) + len(unresolved) + len(proven),
        "days_thin": thin,
        "confirmed": confirmed,
        "unresolved": unresolved,
        "proven_short": proven,
        "observed_still_moving_at": _widest(all_curves, "still_moving_at"),
        "observed_final_by": _widest(all_curves, "final_by"),
    }


def summary_line(result):
    """One sentence an operator can act on, or find out nothing is wrong."""
    hours = result["settle_hours"]
    if result["proven_short"]:
        needed = int(result["observed_still_moving_at"] or hours) + 1
        return (f"{len(result['proven_short'])} day(s) were still being counted "
                f"at or past the configured {hours}h wait — raise "
                f"CATNIP_SETTLE_HOURS to at least {needed}h.")
    if not result["days_measured"]:
        return ("Not enough readings yet. Every collection adds one per "
                "recent day; two readings of the same closed day is the "
                "minimum this can say anything from.")
    if result["unresolved"]:
        return (f"No day was proven still counting at {hours}h, but "
                f"{len(result['unresolved'])} of {result['days_measured']} "
                f"changed somewhere inside a gap that straddles it. "
                f"Collecting twice a day would place the change; the wait is "
                f"not contradicted.")
    return (f"All {result['days_measured']} measured day(s) held their final "
            f"value before the {hours}h wait expired.")


# ---- rendering ---------------------------------------------------------------

def _hrs(value):
    return "—" if value is None else f"{value:.0f}h"


def _span(bounds):
    """`first -> final` over the closed readings, or why there are none."""
    if bounds["readings"] == 0:
        return "still open"
    if bounds["first"] == bounds["final"]:
        return str(bounds["final"])
    return f"{bounds['first']} -> {bounds['final']}"


def render(all_curves, result, stream=None):
    stream = sys.stdout if stream is None else stream
    print(f"settle measurement — {len(all_curves)} day(s) in the log, "
          f"configured wait {result['settle_hours']}h "
          f"(floor {result['floor']}h)\n", file=stream)
    if not all_curves:
        print("  No readings. `catnip history` writes them on every ingest; "
              "the log starts empty and fills from the next collection.",
              file=stream)
        return
    print(f"  {'day':<12} {'reads':>5} {'repos':>5}  {'clones':<14} "
          f"{'views':<14} {'moving at':>9} {'final by':>9}", file=stream)
    for curve in all_curves:
        # The first CLOSED reading, not the first reading: a day read while
        # still open returns a flat zero, and pairing that with the final
        # value would print every day as a hundredfold late arrival.
        clones = _span(curve["clones"])
        views = _span(curve["views"])
        lower = _widest([curve], "still_moving_at")
        upper = _widest([curve], "final_by")
        print(f"  {curve['day']:<12} {curve['readings']:>5} {curve['repos']:>5}  "
              f"{clones:<14} {views:<14} {_hrs(lower):>9} {_hrs(upper):>9}",
              file=stream)
    print(file=stream)
    print(f"  {summary_line(result)}", file=stream)
    if result["days_thin"]:
        print(f"  {len(result['days_thin'])} day(s) have one reading and cannot "
              f"be measured yet.", file=stream)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Measure how long GitHub keeps revising a day.")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--log", type=Path, help="Override the settle log path.")
    p.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2

    path = args.log or cfg.daily_snapshots_file
    all_curves = curves(load(path))
    result = verdict(all_curves)
    if args.json:
        json.dump({"log": str(path), "verdict": result,
                   "summary": summary_line(result), "days": all_curves},
                  sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    render(all_curves, result)
    # A contradicted wait is an operator action, and a command that reports
    # it with exit 0 is a command a timer will never surface.
    return 1 if result["proven_short"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
