#!/usr/bin/env python3
"""The settle measurement: recording readings, and what they prove.

The point of these tests is the *bounds*. A daily timer reads each day
roughly 24h apart, so a value that changed between a 19h read and a 43h
read changed somewhere inside a 24h gap. Reporting that as "settled at
43h" would be an invention, and reporting it as "still moving at 43h"
would be a false alarm against a 36h wait. Both are asserted here.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_run, write_config  # noqa: E402

from catnip import derive, doctor, history, settle  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402
from catnip.config import DEFAULTS, Config  # noqa: E402


def write_log(path, rows):
    """rows: (fetch, day, repo, clones, views)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for fetch, day, repo, clones, views in rows:
            fh.write(json.dumps({"fetch": fetch, "day": day, "repo": repo,
                                 "clones": [clones, 1], "views": [views, 1]}) + "\n")


class ProbeWindowTests(unittest.TestCase):
    """What ingest records, and what it deliberately does not."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _ingest(self, **kwargs):
        run = make_run(self.root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(self.root / "runs", self.root / "history", "testuser", **kwargs)
        return self.root / "history" / "daily_snapshots.jsonl"

    def test_ingest_records_only_the_days_that_can_still_move(self):
        log = self._ingest()
        days = {json.loads(line)["day"] for line in log.read_text().splitlines()}
        # The run holds 2026-07-23 through its own day, 2026-08-05. Only the
        # settling wait plus its margin is worth logging; every older day
        # was identical in every read that has ever been taken of it.
        oldest = (date.fromisoformat("2026-08-05")
                  - timedelta(days=derive.settle_probe_days())).isoformat()
        self.assertEqual(min(days), oldest)
        self.assertEqual(max(days), "2026-08-05")
        self.assertLess(len(days), 14)

    def test_the_reading_is_what_the_fetch_saw_not_what_the_store_holds(self):
        log = self._ingest()
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertTrue(all(r["fetch"] == "20260805T120000Z" for r in rows))
        self.assertTrue(all(len(r["clones"]) == 2 and len(r["views"]) == 2 for r in rows))

    def test_rebuild_drops_the_log_rather_than_doubling_it(self):
        log = self._ingest()
        first = len(log.read_text().splitlines())
        with redirect_stdout(io.StringIO()):
            history.ingest(self.root / "runs", self.root / "history", "testuser",
                           rebuild=True)
        self.assertEqual(len(log.read_text().splitlines()), first)

    def test_an_upgraded_store_backfills_from_surviving_runs(self):
        log = self._ingest()
        expected = log.read_text()
        log.unlink()
        with redirect_stdout(io.StringIO()) as out:
            history.ingest(self.root / "runs", self.root / "history", "testuser")
        # The run is already in `fetches_ingested`, so nothing else moves —
        # but the reading in its CSVs is the only one that exists, and
        # waiting three collections to measure anything is a worse default.
        self.assertIn("Backfilled settle readings", out.getvalue())
        self.assertEqual(log.read_text(), expected)

    def _timeseries(self, fetch_id, rows):
        """A run holding only the traffic CSV. rows: (repo, day, metric, count)."""
        run = self.root / "runs" / fetch_id / "analysis"
        run.mkdir(parents=True, exist_ok=True)
        lines = ["repo_name,timestamp,metric,count,uniques"]
        lines += [f"{r},{d}T00:00:00Z,{m},{c},1" for r, d, m, c in rows]
        (run / "github_traffic_timeseries.csv").write_text("\n".join(lines) + "\n",
                                                           encoding="utf-8")
        return run.parent

    def test_a_repo_day_with_no_row_is_recorded_as_a_zero(self):
        # GitHub returns no row at all for a repo-day with no traffic, so a
        # repo that goes from nothing to its full count is simply ABSENT
        # from the earlier reading. That is most of the late arrival the
        # 36h measurement found, concentrated on repos pushed that day —
        # reading absence as absence loses exactly the movement worth
        # catching.
        run = self._timeseries("20260805T120000Z", [
            ("alpha", "2026-08-04", "clones", 7),
            ("alpha", "2026-08-04", "views", 20),
        ])
        readings = history.probe_readings(run)
        self.assertEqual(readings["2026-08-03"]["alpha"], {})
        written = history.append_daily_snapshots(
            self.root / "log.jsonl", "20260805T120000Z", readings)
        self.assertGreater(written, 1)
        rows = {(r["day"], r["repo"]): r for r in
                (json.loads(x) for x in (self.root / "log.jsonl").read_text().splitlines())}
        self.assertEqual(rows[("2026-08-03", "alpha")]["clones"], [0, 0])
        self.assertEqual(rows[("2026-08-04", "alpha")]["clones"], [7, 1])

    def test_a_repo_the_fetch_never_saw_is_not_invented_as_a_zero(self):
        # The other half of the same rule. Filling zeros for a repo this
        # fetch did not collect would make the account gaining a repo look
        # like GitHub still counting the day.
        run = self._timeseries("20260805T120000Z", [
            ("alpha", "2026-08-04", "clones", 7)])
        readings = history.probe_readings(run)
        self.assertEqual(set(readings["2026-08-04"]), {"alpha"})

    def test_trim_bounds_the_log_without_touching_recent_days(self):
        log = self.root / "history" / "daily_snapshots.jsonl"
        write_log(log, [
            ("20260501T120000Z", "2026-04-30", "alpha", 1, 2),
            ("20260805T120000Z", "2026-08-04", "alpha", 3, 4),
        ])
        dropped = history.trim_daily_snapshots(log, 30, newest_day="2026-08-05")
        self.assertEqual(dropped, 1)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([r["day"] for r in rows], ["2026-08-04"])

    def test_trim_leaves_the_file_alone_when_nothing_is_stale(self):
        log = self.root / "history" / "daily_snapshots.jsonl"
        write_log(log, [("20260805T120000Z", "2026-08-04", "alpha", 3, 4)])
        before = log.read_text()
        self.assertEqual(history.trim_daily_snapshots(log, 30, newest_day="2026-08-05"), 0)
        self.assertEqual(log.read_text(), before)


class BoundsTests(unittest.TestCase):
    """What a sequence of readings does and does not prove."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "daily_snapshots.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def curve(self, readings, day="2026-08-01"):
        """readings: (fetch stamp, clones). One repo, so no intersection."""
        write_log(self.log, [(f, day, "alpha", c, c * 4) for f, c in readings])
        return settle.curves(settle.load(self.log))[0]

    def test_a_day_final_in_its_first_closed_reading_is_settled_at_that_age(self):
        # 2026-08-01 closes at 2026-08-02T00:00Z; read at 19h and 43h.
        curve = self.curve([("20260802T190000Z", 30), ("20260803T190000Z", 30)])
        self.assertIsNone(curve["clones"]["still_moving_at"])
        self.assertEqual(curve["clones"]["final_by"], 19)
        self.assertEqual(curve["clones"]["final"], 30)

    def test_a_change_between_two_reads_proves_only_the_earlier_age(self):
        curve = self.curve([("20260802T190000Z", 10), ("20260803T190000Z", 30)])
        # It was unfinished at 19h — a fact. It was final by 43h — also a
        # fact. Anything said about 36h is the cadence talking, not GitHub.
        self.assertEqual(curve["clones"]["still_moving_at"], 19)
        self.assertEqual(curve["clones"]["final_by"], 43)

    def test_a_reading_taken_before_the_day_closed_is_not_evidence(self):
        # The fetch-day bucket reads as a flat zero. A day still open is
        # trivially unfinished; counting that as "GitHub was still adding
        # at -5h" would make every day contradict every wait.
        curve = self.curve([("20260801T190000Z", 0), ("20260802T190000Z", 30),
                            ("20260803T190000Z", 30)])
        self.assertEqual(curve["clones"]["readings"], 2)
        self.assertIsNone(curve["clones"]["still_moving_at"])

    def test_a_day_read_only_while_open_reports_no_final_value(self):
        curve = self.curve([("20260801T190000Z", 0)])
        self.assertEqual(curve["clones"]["readings"], 0)
        self.assertIsNone(curve["clones"]["final"])
        self.assertEqual(settle._span(curve["clones"]), "still open")

    def test_only_repos_present_in_every_reading_are_counted(self):
        # `beta` joins the account on the second fetch. Summing over the
        # union would show the day gaining 5 clones and read as GitHub
        # still counting it.
        write_log(self.log, [
            ("20260802T190000Z", "2026-08-01", "alpha", 30, 120),
            ("20260803T190000Z", "2026-08-01", "alpha", 30, 120),
            ("20260803T190000Z", "2026-08-01", "beta", 5, 20),
        ])
        curve = settle.curves(settle.load(self.log))[0]
        self.assertEqual(curve["repos"], 1)
        self.assertIsNone(curve["clones"]["still_moving_at"])


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "daily_snapshots.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def verdict(self, readings, hours=36):
        write_log(self.log, [(f, d, "alpha", c, c * 4) for f, d, c in readings])
        return settle.verdict(settle.curves(settle.load(self.log)), hours=hours)

    def test_a_change_after_the_wait_expired_contradicts_it(self):
        # Read at 43h with 10, at 67h with 30: still being counted at 43h,
        # which is past 36. That is proof, not a coarse-cadence artifact.
        result = self.verdict([("20260803T190000Z", "2026-08-01", 10),
                               ("20260804T190000Z", "2026-08-01", 30)])
        self.assertEqual(result["proven_short"], ["2026-08-01"])
        self.assertIn("raise CATNIP_SETTLE_HOURS to at least 44h",
                      settle.summary_line(result))

    def test_a_change_inside_a_straddling_gap_is_unresolved_not_a_contradiction(self):
        result = self.verdict([("20260802T190000Z", "2026-08-01", 10),
                               ("20260803T190000Z", "2026-08-01", 30)])
        self.assertEqual(result["proven_short"], [])
        self.assertEqual(result["unresolved"], ["2026-08-01"])
        self.assertIn("shorter collection interval", settle.summary_line(result))

    def test_a_day_final_before_the_wait_confirms_it(self):
        result = self.verdict([("20260802T190000Z", "2026-08-01", 30),
                               ("20260803T190000Z", "2026-08-01", 30)])
        self.assertEqual(result["confirmed"], ["2026-08-01"])
        self.assertIn("held their final value", settle.summary_line(result))

    def test_one_reading_measures_nothing(self):
        result = self.verdict([("20260802T190000Z", "2026-08-01", 30)])
        self.assertEqual(result["days_measured"], 0)
        self.assertEqual(result["days_thin"], ["2026-08-01"])
        self.assertIn("Not enough readings yet", settle.summary_line(result))

    def test_an_empty_log_is_not_an_error(self):
        result = settle.verdict(settle.curves(settle.load(self.log)))
        self.assertEqual(result["days_measured"], 0)
        with redirect_stdout(io.StringIO()) as out:
            settle.render([], result)
        self.assertIn("No readings", out.getvalue())


class SurfaceTests(unittest.TestCase):
    """The command and the doctor check an operator actually reads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "stats" / "history" / "daily_snapshots.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_command_exits_nonzero_only_on_a_contradiction(self):
        write_config(self.root)
        cfg = str(self.root / "catnip.conf")
        write_log(self.log, [("20260802T190000Z", "2026-08-01", "alpha", 30, 120),
                             ("20260803T190000Z", "2026-08-01", "alpha", 30, 120)])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(settle.main(["--config", cfg]), 0)
        self.assertIn("2026-08-01", out.getvalue())

        write_log(self.log, [("20260803T190000Z", "2026-08-01", "alpha", 10, 40),
                             ("20260804T190000Z", "2026-08-01", "alpha", 30, 120)])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(settle.main(["--config", cfg]), 1)

    def test_json_output_carries_the_verdict_and_the_readings(self):
        write_config(self.root)
        write_log(self.log, [("20260802T190000Z", "2026-08-01", "alpha", 30, 120),
                             ("20260803T190000Z", "2026-08-01", "alpha", 30, 120)])
        with redirect_stdout(io.StringIO()) as out:
            settle.main(["--config", str(self.root / "catnip.conf"), "--json"])
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["verdict"]["confirmed"], ["2026-08-01"])
        self.assertEqual(len(payload["days"][0]["observations"]), 2)

    def test_doctor_warns_when_the_readings_contradict_the_wait(self):
        write_config(self.root)
        cfg = Config.load(self.root / "catnip.conf")
        write_log(self.log, [("20260803T190000Z", "2026-08-01", "alpha", 10, 40),
                             ("20260804T190000Z", "2026-08-01", "alpha", 30, 120)])
        rep = doctor.Report()
        doctor.check_settling(rep, cfg)
        self.assertEqual(rep.checks[0]["status"], doctor.WARN)
        self.assertIn("catnip settle", rep.checks[0]["fix"])

    def test_doctor_does_not_warn_before_anything_is_measurable(self):
        write_config(self.root)
        rep = doctor.Report()
        doctor.check_settling(rep, Config.load(self.root / "catnip.conf"))
        self.assertEqual(rep.checks[0]["status"], doctor.PASS)
        self.assertIn("not measurable yet", rep.checks[0]["detail"])


class ConfiguredWaitTests(unittest.TestCase):
    """The knob has to reach `derive`, or the file and the code disagree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        derive._CONFIG_SETTLE_HOURS = None

    def tearDown(self):
        self._tmp.cleanup()
        derive._CONFIG_SETTLE_HOURS = None

    def test_the_knob_is_a_known_key(self):
        # It was documented in catnip.conf.example and rejected by the
        # parser: uncommenting the line catnip itself suggested made every
        # command exit 2 with "unknown key".
        self.assertIn("CATNIP_SETTLE_HOURS", DEFAULTS)
        self.assertIn("CATNIP_SETTLE_LOG_DAYS", DEFAULTS)
        path = write_config(self.root, CATNIP_SETTLE_HOURS="48")
        self.assertEqual(Config.load(path).integer("CATNIP_SETTLE_HOURS"), 48)

    def test_a_file_value_reaches_derive(self):
        path = write_config(self.root, CATNIP_SETTLE_HOURS="48")
        Config.load(path)  # prove the file parses before asserting on it
        with mock.patch.dict(os.environ, {"CATNIP_CONFIG": str(path)}, clear=False):
            os.environ.pop("CATNIP_SETTLE_HOURS", None)
            derive._CONFIG_SETTLE_HOURS = None
            self.assertEqual(derive.settle_hours(), 48)

    def test_the_floor_still_wins_over_a_lower_file_value(self):
        path = write_config(self.root, CATNIP_SETTLE_HOURS="6")
        with mock.patch.dict(os.environ, {"CATNIP_CONFIG": str(path)}, clear=False):
            os.environ.pop("CATNIP_SETTLE_HOURS", None)
            derive._CONFIG_SETTLE_HOURS = None
            self.assertEqual(derive.settle_hours(), derive.SETTLE_HOURS_FLOOR)


if __name__ == "__main__":
    unittest.main()
