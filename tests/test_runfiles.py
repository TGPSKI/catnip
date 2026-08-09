#!/usr/bin/env python3
"""Reading a run directory's raw payloads.

Two things this module exists to stop happening again: four copies of the
raw-filename rule that had already drifted from the fetcher's, and four
places that each had to remember to drop the days GitHub is still counting.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catnip import runfiles  # noqa: E402
from catnip.config import slug_for  # noqa: E402

RUN = "20260809T120000Z"   # settles through 2026-08-07


def _write(root, repo, metric, days):
    raw = root / RUN / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    body = {"count": sum(c for _, c in days), "uniques": len(days),
            metric: [{"timestamp": f"{d}T00:00:00Z", "count": c, "uniques": 1}
                     for d, c in days]}
    (raw / f"repo_{slug_for(repo)}_{metric}.json").write_text(json.dumps(body))
    listing = raw / "org_repos.json"
    known = json.loads(listing.read_text()) if listing.is_file() else []
    if not any(r["name"] == repo for r in known):
        known.append({"name": repo})
    listing.write_text(json.dumps(known))
    return root / RUN


class RunFilesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_daily_stops_at_the_settled_day(self):
        run = _write(self.root, "alpha", "clones",
                     [("2026-08-06", 5), ("2026-08-07", 4),
                      ("2026-08-08", 1), ("2026-08-09", 0)])
        days = [r["timestamp"][:10] for r in runfiles.daily(run, "alpha", "clones")]
        self.assertEqual(days, ["2026-08-06", "2026-08-07"])

    def test_totals_are_the_window_not_the_settled_days(self):
        # GitHub reports these alongside the series, for its own window. The
        # funnel divides paths by them and paths have no daily grain, so
        # re-deriving them from settled days would invent a number GitHub
        # never gave.
        run = _write(self.root, "alpha", "views",
                     [("2026-08-06", 5), ("2026-08-09", 7)])
        self.assertEqual(runfiles.totals(run, "alpha", "views"), (12, 2))

    def test_a_repo_name_needing_sanitizing_still_resolves(self):
        # The four traffic analyses inlined slug_for without its re.sub, so a
        # name carrying anything outside [A-Za-z0-9_.-] resolved to a file the
        # fetcher never wrote: no error, no rows, repo silently absent.
        name = "weird name+v2"
        self.assertNotEqual(slug_for(name),
                            name.replace("/", "_").replace("-", "--"))
        run = _write(self.root, name, "clones", [("2026-08-06", 3)])
        self.assertEqual(len(runfiles.daily(run, name, "clones")), 1)
        self.assertIn(name, [r["name"] for r in runfiles.repo_list(run)])

    def test_a_missing_or_unreadable_payload_is_empty_not_an_error(self):
        run = _write(self.root, "alpha", "clones", [("2026-08-06", 1)])
        self.assertEqual(runfiles.daily(run, "ghost", "clones"), [])
        self.assertEqual(runfiles.totals(run, "ghost", "views"), (0, 0))
        (run / "raw" / f"repo_{slug_for('alpha')}_views.json").write_text("{ not json")
        self.assertEqual(runfiles.daily(run, "alpha", "views"), [])

    def test_a_run_dir_without_a_stamp_keeps_every_day(self):
        # Renaming or copying a run out of the data dir removes the only
        # record of when it was read. Degrade to showing what it holds,
        # exactly as a store with no fetch stamps does.
        run = _write(self.root, "alpha", "clones",
                     [("2026-08-06", 5), ("2026-08-09", 1)])
        moved = self.root / "some-copy"
        run.rename(moved)
        self.assertEqual(len(runfiles.daily(moved, "alpha", "clones")), 2)


if __name__ == "__main__":
    unittest.main()
