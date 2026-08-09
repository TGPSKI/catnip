#!/usr/bin/env python3
"""Offline tests for the TUI data layer.

Focus: timeframe windowing. The chart and the top lists must describe the
same window, or the screen shows "Top Views (all history)" over a run's
trailing 14 days and quietly disagrees with itself.
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catnip.ui import DAY_WINDOW, AnalyticsData  # noqa: E402


def _write_run(root, timeseries, history=None, run_id="20260806T120000Z"):
    """Build a minimal data dir: <root>/runs/<id>/analysis + <root>/stats.

    `run_id` is not decoration: the run stamp is what decides how much of the
    data has finished settling, so a fixture whose stamp sits next to its
    newest row is a fixture with an empty window.
    """
    run = root / "runs" / run_id
    analysis = run / "analysis"
    analysis.mkdir(parents=True)
    (root / "stats" / "history").mkdir(parents=True)
    with (analysis / "github_traffic_timeseries.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["repo_name", "metric", "timestamp", "count", "uniques"])
        w.writeheader()
        for row in timeseries:
            w.writerow(row)
    with (root / "stats" / "totals.json").open("w") as fh:
        json.dump({"owner": "TEST"}, fh)
    if history is not None:
        with (root / "stats" / "history" / "traffic_daily.json").open("w") as fh:
            json.dump(history, fh)
    return run


def _ts(repo, metric, day, count):
    return {"repo_name": repo, "metric": metric,
            "timestamp": f"2026-08-{day:02d}T00:00:00Z", "count": count, "uniques": 1}


class WindowingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_window_is_by_date_not_per_repo_index(self):
        # `fresh` reports through Aug 4; `stale` stopped on Aug 1. Slicing each
        # repo's own buckets[-1:] put stale's Aug 1 count in "last day".
        rows = ([_ts("fresh", "views", d, 10) for d in range(1, 5)]
                + [_ts("stale", "views", d, 7) for d in range(1, 2)])
        data = AnalyticsData(_write_run(self.root, rows))
        day = data.windowed_views("1d")
        self.assertEqual(day.get("fresh"), 10)
        self.assertEqual(day.get("stale", 0), 0)
        week = data.windowed_views("1w")
        self.assertEqual(week.get("stale"), 7)

    def test_toplists_reconcile_with_chart_every_timeframe(self):
        rows = []
        for d in range(1, 15):
            rows += [_ts("a", "views", d, d), _ts("b", "views", d, 1),
                     _ts("a", "clones", d, 2), _ts("b", "clones", d, 1)]
        # Stamped late enough that all 14 days have settled.
        data = AnalyticsData(_write_run(self.root, rows, run_id="20260817T000000Z"))
        for tf in ("1d", "1w", "2w"):
            n = DAY_WINDOW[tf]
            self.assertEqual(
                sum(b["count"] for b in data.daily_views[-n:]),
                sum(data.windowed_views(tf).values()), f"views mismatch at {tf}")
            self.assertEqual(
                sum(b["count"] for b in data.daily_clones[-n:]),
                sum(data.windowed_clones(tf).values()), f"clones mismatch at {tf}")

    def test_all_timeframe_reads_the_history_store(self):
        # store covers 30 days; the run's CSV only carries the last 3
        history = {"repos": {"a": {"views": {f"2026-07-{d:02d}": [5, 1]
                                             for d in range(1, 31)},
                                   "clones": {f"2026-07-{d:02d}": [2, 1]
                                              for d in range(1, 31)}}}}
        rows = [_ts("a", "views", d, 5) for d in range(1, 4)]
        rows += [_ts("a", "clones", d, 2) for d in range(1, 4)]
        data = AnalyticsData(_write_run(self.root, rows, history))
        chart = sum(b["count"] for b in data.history_series("views"))
        self.assertEqual(chart, 150)
        # top list must match the chart, not the 3-day CSV window
        self.assertEqual(sum(data.windowed_views("all").values()), 150)
        self.assertEqual(sum(data.windowed_clones("all").values()), 60)

    def test_all_falls_back_to_csv_without_a_store(self):
        rows = [_ts("a", "views", d, 5) for d in range(1, 4)]
        data = AnalyticsData(_write_run(self.root, rows, run_id="20260806T000000Z"))
        self.assertEqual(sum(data.windowed_views("all").values()), 15)

    def test_empty_run_is_survivable(self):
        data = AnalyticsData(_write_run(self.root, []))
        self.assertEqual(data.windowed_views("2w"), {})
        self.assertEqual(data.windowed_views("all"), {})
        self.assertEqual(data.daily_views, [])

    def test_one_day_ranks_a_day_with_counts_not_the_unsettled_tail(self):
        # The store's newest days are the ones GitHub is still counting: they
        # read zero for every repo. Ranking on them printed an empty
        # "Top 10 by Views (last day)" in alphabetical order.
        history = {"repos": {"a": {"views": {"2026-08-03": [9, 3],
                                             "2026-08-04": [0, 0],
                                             "2026-08-05": [0, 0]},
                                   "clones": {"2026-08-03": [4, 2],
                                              "2026-08-04": [0, 0],
                                              "2026-08-05": [0, 0]}}},
                   "fetches_ingested": ["20260805T120000Z"]}   # settles through 08-03
        rows = [_ts("a", "views", 3, 9), _ts("a", "clones", 3, 4)]
        data = AnalyticsData(_write_run(self.root, rows, history))
        self.assertEqual(data.settled_day(), "2026-08-03")
        self.assertEqual(data.windowed_views("1d"), {"a": 9})
        self.assertEqual(data.windowed_clones("1d"), {"a": 4})

    def test_the_unsettled_tail_is_off_the_chart_too(self):
        # The chart and the top lists read the same trimmed series, or the
        # headline shows a cliff to zero the ranking says did not happen.
        history = {"repos": {"a": {"views": {"2026-08-03": [9, 3],
                                             "2026-08-04": [0, 0]},
                                   "clones": {}}},
                   "fetches_ingested": ["20260805T120000Z"]}
        data = AnalyticsData(_write_run(self.root, [], history))
        self.assertEqual([b["ts"] for b in data.daily_views], ["2026-08-03"])

    def test_a_store_without_run_stamps_keeps_its_newest_day(self):
        history = {"repos": {"a": {"views": {"2026-08-05": [9, 3],
                                             "2026-08-06": [2, 1]},
                                   "clones": {}}}}
        data = AnalyticsData(_write_run(self.root, [], history))
        self.assertEqual(data.windowed_views("1d"), {"a": 2})

    def test_the_header_names_the_newest_day_it_is_showing(self):
        # "last day" is two days ago. Saying so is the difference between a
        # window and a claim about recency.
        history = {"repos": {"a": {"views": {"2026-08-03": [9, 3]}, "clones": {}}},
                   "fetches_ingested": ["20260805T120000Z"]}
        data = AnalyticsData(_write_run(self.root, [], history))
        self.assertEqual(data.settled_day(), "2026-08-03")

    def test_explicit_store_paths_win_over_layout_inference(self):
        # A run directory copied out of the data dir keeps working when the
        # caller passes the stores explicitly — this is what `catnip tui
        # /some/other/run` relies on.
        rows = [_ts("a", "views", d, 5) for d in range(1, 4)]
        run = _write_run(self.root, rows)
        data = AnalyticsData(run,
                             stats_file=self.root / "stats" / "totals.json",
                             history_file=self.root / "stats" / "history" / "traffic_daily.json")
        self.assertEqual(data.totals.get("owner"), "TEST")


if __name__ == "__main__":
    unittest.main()
