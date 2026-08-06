#!/usr/bin/env python3
"""No view may draw into the header or the footer.

The framework's `_put` clips to the terminal and swallows `curses.error` —
which is what keeps a one-column-too-wide row from crashing the viewer, and
also what lets a view quietly scribble over the footer instead of failing
loudly. Clipping protects the process, not the layout.

So the layout invariant gets its own test. Rows 0–1 are the header and the
view strip, the last row is the footer, and everything a view draws must
land strictly between them. This was caught in production by reading the
frames of a demo GIF: the history view's stars-per-month chart sized itself
without reserving a row for its own x-axis labels, and rendered
`[q]uit 24-05load  [1-926-04=]view` along the bottom of the screen.

Rendering here goes through a fake `put` that records (row, col, text), so
the assertion is on the character grid and no terminal is involved.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import make_run  # noqa: E402

from catnip import history  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402
from catnip.ui import (  # noqa: E402
    TIMEFRAMES,
    VIEWS,
    AnalyticsData,
    AnalyticsTUI,
)

# Ordinary windows, the documented 60x16 floor, and the short/wide shape
# that leaves a third chart with room for bars but not for their labels.
SIZES = [(16, 60), (18, 200), (24, 80), (34, 130), (40, 100), (50, 200)]


class FakeCurses:
    """Just enough curses for a render: attributes are ints, colors no-ops."""
    A_BOLD = 1
    A_DIM = 2
    A_REVERSE = 4
    A_UNDERLINE = 8
    A_NORMAL = 0
    KEY_UP = 259
    KEY_DOWN = 258
    KEY_LEFT = 260
    KEY_RIGHT = 261
    KEY_ENTER = 343
    KEY_BACKSPACE = 263

    class error(Exception):
        pass

    @staticmethod
    def color_pair(n):
        return 0


class FakeScreen:
    def __init__(self, rows, cols):
        self._rows, self._cols = rows, cols

    def getmaxyx(self):
        return self._rows, self._cols


def build_tui(data, rows, cols, view):
    """An AnalyticsTUI wired for rendering only — no terminal involved."""
    app = AnalyticsTUI.__new__(AnalyticsTUI)
    app.curses = FakeCurses
    app.stdscr = FakeScreen(rows, cols)
    app.data = data
    app.view = view
    app.timeframe = "2w"
    # One definition of the per-view defaults, shared with the real app.
    app.init_view_state()
    app.writes = []
    app._put = lambda y, x, text, attr=0: app.writes.append((y, x, str(text)))
    return app


class LayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            # The history store must exist, and must carry star events: the
            # history view's third chart only renders when stars_by_month is
            # populated, and that chart is where the overflow lives. Without
            # this ingest the view stops after two charts and the test passes
            # against the very bug it was written for.
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)
        assert cls.data.history.get("stars_by_month"), \
            "fixture must populate stars_by_month or the layout test is vacuous"

    def chrome(self, rows, cols, view, timeframe="2w"):
        """The (row, col, text) writes the header and footer legitimately make.

        Per-view: the row-1 view strip highlights the active view and
        abbreviates the others to fit, so its exact text depends on `view`;
        the footer names the current timeframe, so it depends on that too.
        """
        app = build_tui(self.data, rows, cols, view)
        app.timeframe = timeframe
        app._render_header(cols)
        app._render_footer(rows, cols)
        return set(app.writes)

    def test_no_view_draws_into_the_header_or_footer(self):
        for rows, cols in SIZES:
            for view in VIEWS:
                allowed = self.chrome(rows, cols, view)
                app = build_tui(self.data, rows, cols, view)
                app.render(rows, cols)
                for write in app.writes:
                    y, _x, text = write
                    if not text.strip() or write in allowed:
                        continue
                    self.assertGreaterEqual(
                        y, 2,
                        f"{view} at {cols}x{rows} drew into the header (row {y}): {text!r}")
                    self.assertLess(
                        y, rows - 1,
                        f"{view} at {cols}x{rows} drew on the footer row "
                        f"(row {y} of {rows}): {text!r}")

    def test_epoch_traffic_reserves_its_axis_label_row(self):
        # The specific regression, swept across every height: at 34 rows the
        # stars-per-month chart had exactly enough space for its bars and
        # none for the month labels underneath them. The screen it lived on
        # is now the traffic view at the 'epoch' timeframe.
        for rows in range(16, 46):
            app = build_tui(self.data, rows, 130, "traffic")
            app.timeframe = "epoch"
            app._render_epoch_view(rows - 3, 130)
            drawn = [y for y, _x, t in app.writes if t.strip()]
            if not drawn:
                continue
            self.assertLess(max(drawn), rows - 1,
                            f"epoch traffic at 130x{rows} reached row {max(drawn)} "
                            f"(footer is row {rows - 1})")

    def test_no_view_draws_into_the_footer_at_any_timeframe(self):
        # Every view now respects t/T, so every view has to be checked at
        # every stop on the cycle rather than only at the default one.
        for rows, cols in ((24, 80), (40, 100), (50, 200)):
            for view in VIEWS:
                for tf in TIMEFRAMES:
                    app = build_tui(self.data, rows, cols, view)
                    app.timeframe = tf
                    allowed = self.chrome(rows, cols, view, tf)
                    app.render(rows, cols)
                    for y, _x, text in app.writes:
                        if not text.strip() or (y, _x, text) in allowed:
                            continue
                        self.assertLess(
                            y, rows - 1,
                            f"{view}@{tf} at {cols}x{rows} drew on the footer "
                            f"(row {y}): {text!r}")

    def test_drilldown_stays_inside_the_frame(self):
        repo = self.data.repos[0]["repo_name"]
        for rows, cols in SIZES:
            app = build_tui(self.data, rows, cols, "table")
            app.drilldown = repo
            app._render_drilldown(rows - 3, cols)
            for y, _x, text in app.writes:
                if not text.strip():
                    continue
                self.assertGreaterEqual(
                    y, 2, f"drilldown at {cols}x{rows} drew into the header: {text!r}")
                self.assertLess(
                    y, rows - 1,
                    f"drilldown at {cols}x{rows} drew on the footer: {text!r}")

    def test_overlay_stays_inside_the_frame(self):
        for rows, cols in SIZES:
            for view in VIEWS:
                app = build_tui(self.data, rows, cols, view)
                app.overlay = True
                app._render_overlay(rows, cols)
                for y, _x, text in app.writes:
                    if not text.strip():
                        continue
                    self.assertGreaterEqual(y, 2, f"overlay/{view} at {cols}x{rows}")
                    self.assertLess(y, rows, f"overlay/{view} at {cols}x{rows}")


if __name__ == "__main__":
    unittest.main()
