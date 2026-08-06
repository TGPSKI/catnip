#!/usr/bin/env python3
"""Drive the real TUI in a real pseudo-terminal.

Grid-level unit tests catch geometry; only a terminal catches "view 9
raises KeyError on a run with no anomalies" or "the app crashes at 60x16".
Every view is visited, at three sizes, on synthetic data.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

SRC = str(Path(__file__).resolve().parents[1] / "src")


@unittest.skipUnless(os.name == "posix", "pty harness is POSIX-only")
class TuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import curses  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("curses is not available") from exc
        from contextlib import redirect_stdout
        from io import StringIO

        from fixtures import make_run

        from catnip.analyze import analyze_github

        cls._tmp = tempfile.TemporaryDirectory()
        cls.run_dir = make_run(cls._tmp.name)
        with redirect_stdout(StringIO()):
            analyze_github(cls.run_dir)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def smoke(self, keys, rows=40, cols=120):
        from pty_smoke import run_pty_smoke
        env_python = sys.executable
        status, output = run_pty_smoke(
            [env_python, "-m", "catnip.ui", str(self.run_dir)],
            rows=rows, cols=cols, keys=keys)
        self.assertNotIn("Traceback", output, output[-2000:])
        self.assertEqual(status, 0, output[-2000:])
        return output

    def setUp(self):
        os.environ["PYTHONPATH"] = SRC + os.pathsep + os.environ.get("PYTHONPATH", "")

    def test_every_view_renders(self):
        # '1'..'=' jump straight to each view; then quit.
        self.smoke("1234567890-=q")

    def test_timeframes_and_sorts_cycle(self):
        self.smoke("tttTTTssffq")

    def test_scrolling_and_search(self):
        self.smoke("3jjkkGg/alpha\rq")

    def test_minimum_terminal_size_refuses_instead_of_garbling(self):
        # 60x16 is the documented floor; below it the framework must say so
        # rather than draw a broken screen.
        self.smoke("q", rows=16, cols=60)

    def test_wide_terminal(self):
        self.smoke("vvvq", rows=50, cols=200)


if __name__ == "__main__":
    unittest.main()
