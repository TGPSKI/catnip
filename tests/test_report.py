#!/usr/bin/env python3
"""The deterministic report and its interval guard.

The report is the floor the catnip-prowl skill stands on, so the property
that matters most is boring: same store, same timeframe, same bytes. If
that ever stops holding, a reader cannot tell the skill's inference from
the arithmetic underneath it.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_run  # noqa: E402

from catnip import history, report  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402
from catnip.config import Config  # noqa: E402

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        run = make_run(cls.root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(cls.root / "runs", cls.root / "stats" / "history",
                           "testuser")
        cls.run_dir = run
        cls.store = json.loads(
            (cls.root / "stats" / "history" / "traffic_daily.json").read_text())
        cls.cfg = Config.load(None)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def build(self, timeframe="2w"):
        return report.build_report(self.store, self.cfg, timeframe, self.run_dir, NOW)

    def test_the_same_inputs_produce_the_same_bytes(self):
        self.assertEqual(self.build(), self.build())

    def test_every_section_is_present(self):
        text = self.build()
        for heading in ("## Provenance", "## Headline", "## Biggest movers",
                        "## What moved, and what caused it", "## Account events",
                        "## Audience", "## Clone intent", "## Coupled repos",
                        "## Content", "## What this report cannot tell you"):
            self.assertIn(heading, text, heading)

    def test_it_declares_its_provenance(self):
        # The skill adds inferred and speculative; this half must be
        # unambiguously the measured one.
        self.assertIn("`measured`", self.build())

    def test_it_states_what_it_cannot_tell_you(self):
        text = self.build()
        tail = text.split("## What this report cannot tell you")[1]
        self.assertIn("Who.", tail)
        self.assertIn("GitHub serves ~14", tail)
        self.assertGreater(len([ln for ln in tail.splitlines()
                                if ln.startswith("- ")]), 1)

    def test_a_thin_store_is_reported_as_thin_not_as_flat(self):
        thin = {"schema_version": 2, "owner": "t",
                "repos": {"a": {"clones": {"2026-07-01": [3, 3]}, "views": {}}}}
        text = report.build_report(thin, self.cfg, "2w", None, NOW)
        self.assertIn("absence of evidence", text)

    def test_an_empty_store_does_not_raise(self):
        text = report.build_report({"repos": {}}, self.cfg, "2w", None, NOW)
        self.assertIn("store is empty", text)

    def test_every_timeframe_renders(self):
        for tf in ("1d", "1w", "2w", "all", "epoch"):
            self.assertIn("# catnip report", self.build(tf))


class GuardTests(unittest.TestCase):
    """Store-advance is the primary gate; the clock is a secondary floor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.store = {"repos": {"a": {"clones": {"2026-08-06": [1, 1]}, "views": {}}}}

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, latest_day, written, window_digest=None, settled=True):
        """A previous report. `settled=False` writes one in the pre-settling
        format, whose latest_day is a fetch day and means something else."""
        out = self.dir / written
        out.mkdir(parents=True)
        (out / "report.md").write_text("x")
        meta = {"latest_day": latest_day, "written": written}
        if settled:
            meta["settle_hours"] = 36
        if window_digest is not None:
            meta["window_digest"] = window_digest
        (out / "meta.json").write_text(json.dumps(meta))

    def test_the_first_report_always_runs(self):
        ok, why = report.guard(self.dir, self.store, NOW)
        self.assertTrue(ok)
        self.assertIn("no previous", why)

    def test_a_store_that_has_not_advanced_is_refused(self):
        self._write("2026-08-06", "20260801T000000Z")
        ok, why = report.guard(self.dir, self.store, NOW)
        self.assertFalse(ok)
        self.assertIn("Nothing new", why)

    def test_an_advanced_store_runs_even_hours_later(self):
        # Store-advance is primary: a new day is new information whatever
        # the clock says.
        self._write("2026-08-01", "20260806T090000Z")
        ok, _ = report.guard(self.dir, self.store, NOW)
        self.assertTrue(ok)

    def test_the_clock_floor_applies_when_the_day_is_unknown(self):
        out = self.dir / "20260806T090000Z"
        out.mkdir(parents=True)
        (out / "report.md").write_text("x")
        (out / "meta.json").write_text(json.dumps({"written": "20260806T090000Z"}))
        ok, why = report.guard(self.dir, self.store, NOW)
        self.assertFalse(ok)
        self.assertIn("floor", why)

    def test_the_clock_floor_lifts_after_the_interval(self):
        out = self.dir / "20260801T000000Z"
        out.mkdir(parents=True)
        (out / "report.md").write_text("x")
        (out / "meta.json").write_text(json.dumps({"written": "20260801T000000Z"}))
        ok, _ = report.guard(self.dir, self.store, NOW)
        self.assertTrue(ok)

    def test_the_floor_is_one_day(self):
        self.assertEqual(report.MIN_INTERVAL, timedelta(days=1))

    def test_an_unsettled_day_is_not_an_advance(self):
        # Every run writes its own date into the store whether or not GitHub
        # counted it, so comparing newest days made the guard pass on an
        # empty bucket - a fresh report over identical data, every night.
        covered = {"repos": {"a": {"clones": {"2026-08-05": [1, 1],
                                              "2026-08-06": [0, 0],
                                              "2026-08-07": [0, 0]}, "views": {}}},
                   "fetches_ingested": ["20260807T120000Z"]}   # settles through 08-05
        self._write("2026-08-05", "20260806T090000Z")
        ok, why = report.guard(self.dir, covered, NOW)
        self.assertFalse(ok)
        self.assertIn("2026-08-05", why)

    def test_a_settled_day_beyond_the_last_report_advances(self):
        advanced = {"repos": {"a": {"clones": {"2026-08-04": [1, 1],
                                               "2026-08-05": [3, 1],
                                               "2026-08-06": [0, 0]}, "views": {}}},
                    "fetches_ingested": ["20260807T120000Z"]}
        self._write("2026-08-04", "20260806T090000Z")
        ok, why = report.guard(self.dir, advanced, NOW)
        self.assertTrue(ok)
        self.assertIn("2026-08-05", why)

    def test_a_day_revised_after_it_was_reported_reopens_the_report(self):
        # GitHub kept adding to a day the last report already described. The
        # store is now right and the report is now wrong, and no new day will
        # ever arrive to trigger a rewrite of it.
        before = {"repos": {"a": {"clones": {"2026-08-05": [1, 1]}, "views": {}}},
                  "fetches_ingested": ["20260807T120000Z"]}
        after = {"repos": {"a": {"clones": {"2026-08-05": [9, 4]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}
        self._write("2026-08-05", "20260806T090000Z",
                    window_digest=report.window_digest(before, "2w"))
        ok, why = report.guard(self.dir, after, NOW)
        self.assertTrue(ok)
        self.assertIn("revised", why)

    def test_an_unrevised_window_stays_closed(self):
        store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}
        self._write("2026-08-05", "20260806T090000Z",
                    window_digest=report.window_digest(store, "2w"))
        ok, why = report.guard(self.dir, store, NOW)
        self.assertFalse(ok)
        self.assertIn("Nothing new", why)

    def test_one_repo_backfilling_is_enough_to_mark_it_stale(self):
        # The account total can stay put while a repo's day is corrected;
        # the digest is per repo so that still counts as a revision.
        before = {"repos": {"a": {"clones": {"2026-08-05": [5, 2]}, "views": {}},
                            "b": {"clones": {"2026-08-05": [5, 2]}, "views": {}}},
                  "fetches_ingested": ["20260807T120000Z"]}
        after = {"repos": {"a": {"clones": {"2026-08-05": [8, 2]}, "views": {}},
                           "b": {"clones": {"2026-08-05": [2, 2]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}
        self.assertNotEqual(report.window_digest(before, "2w"),
                            report.window_digest(after, "2w"))

    def test_a_report_with_no_digest_recorded_is_not_reopened_forever(self):
        # Reports written before this existed carry no digest. Absence must
        # read as "cannot tell", not as "changed".
        store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}
        self._write("2026-08-05", "20260806T090000Z")
        ok, _ = report.guard(self.dir, store, NOW)
        self.assertFalse(ok)

    def test_a_pre_settling_report_does_not_suppress_the_next_one(self):
        # Upgrading changed what `latest_day` means. Reports written before
        # this recorded the store's newest day, which is the fetch's own
        # uncounted day — on this account two days ahead of what that report
        # actually covered. Comparing the two names resolves the wrong way
        # and gags the report for exactly the cycles in which the store is
        # gaining the settled days worth reporting.
        store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1],
                                            "2026-08-06": [0, 0],
                                            "2026-08-07": [0, 0]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}   # settles through 08-05
        self._write("2026-08-07", "20260806T090000Z", settled=False)
        ok, why = report.guard(self.dir, store, NOW)
        self.assertTrue(ok)
        self.assertIn("predates", why)

    def test_a_settled_report_is_still_compared_normally(self):
        # The migration branch must not swallow the ordinary refusal.
        store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1]}, "views": {}}},
                 "fetches_ingested": ["20260807T120000Z"]}
        self._write("2026-08-05", "20260806T090000Z")
        ok, _ = report.guard(self.dir, store, NOW)
        self.assertFalse(ok)

    def test_meta_names_both_days_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1],
                                                "2026-08-07": [0, 0]}, "views": {}}},
                     "fetches_ingested": ["20260807T120000Z"]}
            out = report.write_report("body\n", Path(tmp), store, "2w", None, NOW)
            meta = json.loads((out / "meta.json").read_text())
            self.assertEqual(meta["latest_day"], "2026-08-05")
            self.assertEqual(meta["store_latest_day"], "2026-08-07")

    def test_a_corrupt_meta_does_not_block_forever(self):
        out = self.dir / "20260806T090000Z"
        out.mkdir(parents=True)
        (out / "report.md").write_text("x")
        (out / "meta.json").write_text("{ not json")
        ok, _ = report.guard(self.dir, self.store, NOW)
        self.assertTrue(ok)


class WriteTests(unittest.TestCase):
    def test_the_path_is_keyed_by_runtime_and_carries_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = {"repos": {"a": {"clones": {"2026-08-06": [1, 1]}, "views": {}}}}
            out = report.write_report("body\n", Path(tmp), store, "2w", None, NOW)
            self.assertEqual(out.name, NOW.strftime(report.STAMP_FMT))
            meta = json.loads((out / "meta.json").read_text())
            self.assertEqual(meta["latest_day"], "2026-08-06")
            self.assertEqual(meta["provenance"], "measured")
            self.assertEqual((out / "report.md").read_text(), "body\n")

    def test_meta_records_the_day_the_report_covers(self):
        # meta's latest_day is what the next cycle's guard compares against,
        # so it has to be the day the windows ended on, not the fetch day.
        with tempfile.TemporaryDirectory() as tmp:
            store = {"repos": {"a": {"clones": {"2026-08-05": [1, 1],
                                                "2026-08-06": [0, 0],
                                                "2026-08-07": [0, 0]}, "views": {}}},
                     "fetches_ingested": ["20260807T120000Z"]}
            out = report.write_report("body\n", Path(tmp), store, "2w", None, NOW)
            meta = json.loads((out / "meta.json").read_text())
            self.assertEqual(meta["latest_day"], "2026-08-05")
            self.assertEqual(meta["window_digest"],
                             report.window_digest(store, "2w"))
            self.assertEqual(meta["settle_hours"], 36)


if __name__ == "__main__":
    unittest.main()


class DispatcherTests(unittest.TestCase):
    """Every command the help advertises must actually dispatch.

    `report` shipped in the usage text with no matching `case` arm, so
    `catnip report` printed "unknown command" and then listed itself as
    available — the same defect as a footer offering a key that does
    nothing.
    """

    def test_every_advertised_command_has_a_case_arm(self):
        import re
        script = (Path(__file__).resolve().parents[1] / "bin" / "catnip").read_text()
        usage = script.split("USAGE", 1)[1].split("USAGE", 1)[0]
        advertised = set()
        for line in usage.splitlines():
            m = re.match(r"^  ([a-z-]+)(?:\s{2,}|\s+[A-Z])", line)
            if m:
                advertised.add(m.group(1))
        cases = set()
        for m in re.finditer(r"^\s*([a-z|-]+)\)", script, re.M):
            cases.update(part for part in m.group(1).split("|"))
        missing = sorted(c for c in advertised if c not in cases)
        self.assertEqual(missing, [], f"advertised but never dispatched: {missing}")
