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

    def _write(self, latest_day, written):
        out = self.dir / written
        out.mkdir(parents=True)
        (out / "report.md").write_text("x")
        (out / "meta.json").write_text(json.dumps(
            {"latest_day": latest_day, "written": written}))

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


if __name__ == "__main__":
    unittest.main()
