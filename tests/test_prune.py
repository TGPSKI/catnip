#!/usr/bin/env python3
"""Retention rules.

The invariant under test is the one with no recovery path: a run whose
traffic days are not in the history store must never be deleted, no
matter how old it is. GitHub will not serve those days again.
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

from fixtures import write_config  # noqa: E402

from catnip import prune  # noqa: E402


def _stamp(days_ago):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y%m%dT%H%M%SZ")


class PruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.runs = self.root / "runs"
        self.runs.mkdir(parents=True)
        self.history = self.root / "stats" / "history" / "traffic_daily.json"
        self.history.parent.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def add_run(self, days_ago):
        name = _stamp(days_ago)
        (self.runs / name).mkdir()
        return name

    def set_ingested(self, *names):
        self.history.write_text(json.dumps({"fetches_ingested": list(names)}), encoding="utf-8")

    def test_a_run_whose_days_are_still_settling_is_kept(self):
        # Retention of zero days would otherwise delete yesterday's run the
        # moment it was ingested. It is the only on-disk record of days
        # GitHub has not finished counting, and `history --rebuild`
        # reconstructs from surviving runs alone.
        recent = self.add_run(1)
        self.add_run(0)
        self.set_ingested(recent)
        delete, keep = prune.plan(self.runs, self.history, retain_days=0)
        self.assertNotIn(recent, [p.name for p, _ in delete])
        self.assertIn("settling", dict((p.name, why) for p, why in keep)[recent])

    def test_a_run_past_the_settling_window_is_still_prunable(self):
        # The floor must not become a second retention policy.
        old = self.add_run(5)
        self.add_run(0)
        self.set_ingested(old)
        delete, _ = prune.plan(self.runs, self.history, retain_days=0)
        self.assertEqual([p.name for p, _ in delete], [old])

    def test_old_ingested_runs_are_deleted(self):
        old = self.add_run(90)
        self.add_run(0)
        self.set_ingested(old)
        delete, _ = prune.plan(self.runs, self.history, retain_days=30)
        self.assertEqual([p.name for p, _ in delete], [old])

    def test_un_ingested_runs_are_never_deleted(self):
        old = self.add_run(90)
        self.add_run(0)
        self.set_ingested()  # store exists but has nothing
        delete, keep = prune.plan(self.runs, self.history, retain_days=30)
        self.assertEqual(delete, [])
        reason = dict((p.name, why) for p, why in keep)[old]
        self.assertIn("NOT INGESTED", reason)

    def test_missing_history_store_blocks_every_deletion(self):
        old = self.add_run(90)
        self.add_run(0)
        delete, keep = prune.plan(self.runs, self.history, retain_days=30)
        self.assertEqual(delete, [])
        self.assertIn("NOT INGESTED", dict((p.name, w) for p, w in keep)[old])

    def test_newest_run_survives_even_when_ancient(self):
        only = self.add_run(400)
        self.set_ingested(only)
        delete, keep = prune.plan(self.runs, self.history, retain_days=1)
        self.assertEqual(delete, [])
        self.assertIn("newest run", dict((p.name, w) for p, w in keep)[only])

    def test_runs_within_retention_are_kept(self):
        recent = self.add_run(5)
        self.add_run(0)
        self.set_ingested(recent)
        delete, _ = prune.plan(self.runs, self.history, retain_days=30)
        self.assertEqual(delete, [])

    def test_dry_run_is_the_default_and_deletes_nothing(self):
        old = self.add_run(90)
        self.add_run(0)
        self.set_ingested(old)
        cfg = write_config(self.root, CATNIP_RETAIN_DAYS="30")
        with redirect_stdout(io.StringIO()) as buf:
            rc = prune.main(["--config", str(cfg)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.runs / old).is_dir(), "dry run must not delete")
        self.assertIn("would delete", buf.getvalue())

    def test_yes_actually_deletes(self):
        old = self.add_run(90)
        self.add_run(0)
        self.set_ingested(old)
        cfg = write_config(self.root, CATNIP_RETAIN_DAYS="30")
        with redirect_stdout(io.StringIO()):
            prune.main(["--config", str(cfg), "--yes"])
        self.assertFalse((self.runs / old).exists())

    def test_unrecognized_directory_names_are_left_alone(self):
        (self.runs / "backup-copy").mkdir()
        delete, keep = prune.plan(self.runs, self.history, retain_days=1)
        self.assertEqual(delete, [])
        self.assertNotIn("backup-copy", [p.name for p, _ in keep])  # not a run at all


if __name__ == "__main__":
    unittest.main()
