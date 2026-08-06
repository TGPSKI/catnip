#!/usr/bin/env python3
"""End-to-end pipeline over synthetic runs — no network, real code paths.

analyze -> history -> totals -> verify is the sequence the timer runs
every night. These tests assert the properties that only show up across
stages: that every CSV the TUI reads gets written, that ingesting the
same run twice does not double any number, and that overlapping traffic
windows are max-merged rather than summed.
"""
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_run, write_config  # noqa: E402

from catnip import doctor, history, totals  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402


def _rows(path):
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def analyze(self, run):
        # strict=True so a crashed deep-traffic stage fails the test here
        # rather than surfacing weeks later as one empty TUI panel.
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)

    def test_analyze_writes_every_artifact_the_tui_reads(self):
        run = make_run(self.root)
        self.analyze(run)
        missing = [name for name in doctor.TUI_CSVS
                   if not (run / "analysis" / name).is_file()
                   or (run / "analysis" / name).stat().st_size == 0]
        self.assertEqual(missing, [], f"analyze left {len(missing)} TUI input(s) empty")
        self.assertTrue((run / "analysis" / "summary.md").is_file())

    def test_analysis_is_scoped_to_the_repos_in_the_run(self):
        run = make_run(self.root, repos=("alpha", "beta-repo"))
        self.analyze(run)
        names = {r["repo_name"] for r in _rows(run / "analysis" / "github_repos.csv")}
        self.assertEqual(names, {"alpha", "beta-repo"})

    def test_traffic_timeseries_carries_both_metrics_per_repo(self):
        run = make_run(self.root, days=14)
        self.analyze(run)
        rows = _rows(run / "analysis" / "github_traffic_timeseries.csv")
        metrics = {(r["repo_name"], r["metric"]) for r in rows}
        self.assertIn(("alpha", "clones"), metrics)
        self.assertIn(("alpha", "views"), metrics)
        self.assertEqual(len([r for r in rows if r["repo_name"] == "alpha"
                              and r["metric"] == "clones"]), 14)

    def test_history_ingest_is_idempotent(self):
        run = make_run(self.root)
        self.analyze(run)
        with redirect_stdout(io.StringIO()):
            first = history.ingest(self.root / "runs", self.root / "history", "testuser")
            second = history.ingest(self.root / "runs", self.root / "history", "testuser")
        # Re-ingesting must be a no-op: the store is the only copy of days
        # older than GitHub's 14-day window, so a doubling bug here is
        # permanent and undetectable from the API.
        self.assertEqual(first["repos"], second["repos"])
        self.assertEqual(len(second["fetches_ingested"]), 1)

    def test_overlapping_runs_are_max_merged_not_summed(self):
        # Two runs a day apart snapshot the same trailing window.
        first = make_run(self.root, run_id="20260805T120000Z")
        second = make_run(self.root, run_id="20260806T120000Z")
        self.analyze(first)
        self.analyze(second)
        with redirect_stdout(io.StringIO()):
            store = history.ingest(self.root / "runs", self.root / "history", "testuser")
        alpha_clones = store["repos"]["alpha"]["clones"]
        # Each day appears in both runs with identical counts; max-merge
        # keeps one value, summing would double every day.
        self.assertEqual(len(alpha_clones), 14)
        self.assertEqual(alpha_clones["2026-07-23"][0], 2)

    def test_totals_rebuild_matches_the_history_store(self):
        run = make_run(self.root)
        self.analyze(run)
        cfg_path = write_config(self.root)
        with redirect_stdout(io.StringIO()):
            history.main([str(self.root / "runs"), "--config", str(cfg_path)])
            rc = totals.main(["--config", str(cfg_path)])
        self.assertEqual(rc, 0)
        stats = json.loads((self.root / "stats" / "totals.json").read_text())
        self.assertEqual(stats["owner"], "testuser")
        self.assertEqual(stats["total_repos"], 3)
        # 14 days x (2 + 3 clones/day baseline with the +i%3 wobble) — the
        # exact figure matters less than it being stable across a rebuild.
        rebuilt_once = stats["total_clones"]
        with redirect_stdout(io.StringIO()):
            totals.main(["--config", str(cfg_path)])
        stats2 = json.loads((self.root / "stats" / "totals.json").read_text())
        self.assertEqual(rebuilt_once, stats2["total_clones"],
                         "totals must be a pure rebuild, not an accumulator")

    def test_doctor_data_checks_pass_after_a_full_pipeline(self):
        run = make_run(self.root)
        self.analyze(run)
        cfg_path = write_config(self.root)
        with redirect_stdout(io.StringIO()):
            history.main([str(self.root / "runs"), "--config", str(cfg_path)])
            totals.main(["--config", str(cfg_path)])
            rc = doctor.main(["--config", str(cfg_path), "--data-only"])
        self.assertEqual(rc, 0)

    def test_doctor_reports_an_unanalyzed_run_instead_of_passing(self):
        make_run(self.root)  # fetched but never analyzed
        cfg_path = write_config(self.root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            doctor.main(["--config", str(cfg_path), "--data-only"])
        self.assertIn("analysis", buf.getvalue())
        self.assertIn("catnip analyze", buf.getvalue())

    def test_run_without_traffic_still_analyzes(self):
        # Repos that deny traffic (no push access) must not take the run down.
        run = make_run(self.root, with_traffic=False)
        self.analyze(run)
        self.assertTrue((run / "analysis" / "github_repos.csv").is_file())
        self.assertEqual(_rows(run / "analysis" / "github_traffic_timeseries.csv"), [])


if __name__ == "__main__":
    unittest.main()


class StoreUpgradeTests(unittest.TestCase):
    """A schema bump must backfill, not just relabel.

    `ingest` skips runs already in `fetches_ingested`, so a store built
    before the referrers/events sections existed declared schema 2 and
    then never filled them — the drilldown's cause rules and the audience
    view's referrer diversity stayed empty forever, with nothing on
    screen to say why.
    """

    def test_new_sections_backfill_from_already_ingested_runs(self):
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from fixtures import make_run

        from catnip import history
        from catnip.analyze import analyze_github

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = make_run(root)
            hist = root / "stats" / "history"
            with redirect_stdout(io.StringIO()):
                analyze_github(run, strict=True)
                history.ingest(root / "runs", hist, "testuser")

            # Simulate a store that predates the sections: drop them and
            # leave the run marked ingested, exactly as an upgrade looks.
            store_path = hist / "traffic_daily.json"
            store = json.loads(store_path.read_text())
            self.assertIn("events", store)
            del store["events"], store["referrers"]
            store_path.write_text(json.dumps(store))

            with redirect_stdout(io.StringIO()):
                history.ingest(root / "runs", hist, "testuser")

            after = json.loads(store_path.read_text())
            self.assertTrue(after.get("events"), "events were never backfilled")
            self.assertTrue(after.get("referrers"), "referrers were never backfilled")
            # And the traffic it already had is untouched.
            self.assertEqual(sorted(after["repos"]), sorted(store["repos"]))

    def test_backfill_is_idempotent(self):
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from fixtures import make_run

        from catnip import history
        from catnip.analyze import analyze_github

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = make_run(root)
            hist = root / "stats" / "history"
            with redirect_stdout(io.StringIO()):
                analyze_github(run, strict=True)
                history.ingest(root / "runs", hist, "testuser")
                first = json.loads((hist / "traffic_daily.json").read_text())
                history.ingest(root / "runs", hist, "testuser")
                second = json.loads((hist / "traffic_daily.json").read_text())
        self.assertEqual(first["events"], second["events"])
        self.assertEqual(first["referrers"], second["referrers"])
