#!/usr/bin/env python3
"""Cursor, drilldown, overlay, and store-only operation.

The invariant worth a test file of its own: [enter] must open the repo
under the highlight. The cursor and the renderer derive their row order
from the same helper, and this is what keeps that true when a sort, a
filter, or a `/` search reorders the list underneath.
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
from test_tui_layout import FakeCurses, build_tui  # noqa: E402

from catnip import derive, history  # noqa: E402
from catnip.analyze import analyze_github  # noqa: E402
from catnip.ui import REPO_ROW_VIEWS, TIMEFRAMES, AnalyticsData  # noqa: E402


class DrilldownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        run = make_run(cls.root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(cls.root / "runs", cls.root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self, view, rows=40, cols=140):
        return build_tui(self.data, rows, cols, view)

    def test_every_repo_row_view_offers_rows_to_the_cursor(self):
        for view in REPO_ROW_VIEWS:
            app = self.app(view)
            self.assertTrue(app._cursor_rows(), f"{view} exposes no cursor rows")

    def test_enter_opens_the_repo_under_the_highlight(self):
        # Walk the cursor down and check the selection tracks the row the
        # renderer marked, at every position, in every row view.
        for view in REPO_ROW_VIEWS:
            app = self.app(view)
            rows = app._cursor_rows()
            for i in range(len(rows)):
                app.cursor(view).to(i, len(rows), app._page(40))
                self.assertEqual(app.selected_repo(), rows[i],
                                 f"{view} row {i}")

    def test_a_search_that_shrinks_the_list_cannot_strand_the_cursor(self):
        for view in REPO_ROW_VIEWS:
            app = self.app(view)
            app.cursor(view).to(999, len(app._cursor_rows()), 10)
            app.search = "alpha"
            repo = app.selected_repo()
            self.assertIn(repo, app._cursor_rows() + [None], view)

    def test_selection_survives_a_sort_change(self):
        app = self.app("table")
        app.cursor("table").to(1, len(app._cursor_rows()), 10)
        for sort in range(len(app.TABLE_SORT_KEYS)):
            app.table_sort = sort
            self.assertIn(app.selected_repo(), app._cursor_rows())

    def test_drilldown_renders_for_every_repo_at_every_timeframe(self):
        for repo in [r["repo_name"] for r in self.data.repos]:
            for tf in TIMEFRAMES:
                app = self.app("table")
                app.drilldown = repo
                app.timeframe = tf
                app.render(40, 140)
                self.assertTrue(app.writes, f"{repo}@{tf} drew nothing")

    def test_drilldown_of_an_unknown_repo_does_not_raise(self):
        app = self.app("table")
        app.drilldown = "no-such-repo"
        app.render(40, 140)

    def test_overlay_renders_a_derivation_for_every_view(self):
        for view in REPO_ROW_VIEWS:
            app = self.app(view)
            app.overlay = True
            app.render(40, 140)
            text = " ".join(t for _y, _x, t in app.writes)
            headline = derive.derivation(view)[0]
            self.assertIn(headline[:30], text, view)

    def test_escape_unwinds_one_layer_at_a_time(self):
        app = self.app("table")
        app.search, app.drilldown, app.overlay = "alpha", "alpha", True
        app.handle_key(27)
        self.assertFalse(app.overlay)
        self.assertEqual(app.drilldown, "alpha")
        app.handle_key(27)
        self.assertIsNone(app.drilldown)
        self.assertEqual(app.search, "alpha")
        app.handle_key(27)
        self.assertEqual(app.search, "")

    def test_capital_q_quits_from_inside_the_drilldown(self):
        app = self.app("table")
        app.drilldown = "alpha"
        self.assertTrue(app.handle_key(ord("Q")))

    def test_lowercase_q_only_goes_back(self):
        app = self.app("table")
        app.drilldown = "alpha"
        self.assertFalse(app.handle_key(ord("q")))
        self.assertIsNone(app.drilldown)

    def test_account_view_keys_are_inert_while_a_repo_is_open(self):
        app = self.app("table")
        app.drilldown = "alpha"
        before = (app.view, app.table_sort, app.table_status)
        for key in (ord("s"), ord("f"), ord("v"), ord("5")):
            app.handle_key(key)
        self.assertEqual((app.view, app.table_sort, app.table_status), before)

    def test_enter_on_a_view_without_rows_does_nothing(self):
        app = self.app("lang")
        app.handle_key(FakeCurses.KEY_ENTER)
        self.assertIsNone(app.drilldown)


class StoreOnlyTests(unittest.TestCase):
    """The store outlives every run. The viewer has to as well.

    `catnip prune` deletes run directories by design; the durable store is
    the artifact that is never deleted. A viewer that refuses to open
    without a run goes dark on exactly the data the project works hardest
    to keep.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        run = make_run(cls.root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(cls.root / "runs", cls.root / "stats" / "history", "testuser")
        cls.store = cls.root / "stats" / "history" / "traffic_daily.json"
        cls.totals = cls.root / "stats" / "totals.json"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def data(self):
        return AnalyticsData(None, self.totals, self.store)

    def test_loads_with_no_run_directory(self):
        data = self.data()
        self.assertTrue(data.store_only)
        self.assertTrue(data.has_store())
        self.assertEqual(data.repos, [])

    def test_store_derived_views_still_have_data(self):
        data = self.data()
        for tf in TIMEFRAMES:
            self.assertTrue(data.deltas(tf), tf)
            self.assertTrue(data.audience(tf), tf)
            self.assertTrue(data.anomalies(tf)["days"], tf)

    def test_every_view_renders_without_a_run(self):
        data = self.data()
        for view in REPO_ROW_VIEWS + ("traffic", "lang", "freq", "correlation"):
            for tf in ("2w", "epoch"):
                app = build_tui(data, 40, 140, view)
                app.timeframe = tf
                app.render(40, 140)

    def test_table_still_lists_repos_the_run_no_longer_has(self):
        # Rows sourced from the store rather than a run are labelled as
        # such instead of vanishing.
        app = build_tui(self.data(), 40, 140, "table")
        rows = app._table_rows()
        self.assertTrue(rows)
        self.assertTrue(all(r["status"] == "no run" for r in rows))

    def test_status_filter_never_cycles_through_empty_screens(self):
        # With no run on disk every row is 'store', and a hard-coded
        # all/active/stale/archived cycle walked the operator through three
        # consecutive "No repos match" screens with nothing to say the
        # filter was at fault rather than the data.
        app = build_tui(self.data(), 40, 140, "table")
        options = app._table_statuses()
        # 'with traffic' leads: it answers the question the table is open for.
        self.assertEqual(options[0], "with traffic")
        for i in range(len(options)):
            app.table_status = i
            self.assertTrue(app._table_rows(), f"filter {options[i]} matched nothing")

    def test_default_table_filter_hides_silent_repos(self):
        app = build_tui(self.data(), 40, 140, "table")
        self.assertEqual(app._table_status(), "with traffic")
        self.assertTrue(all(r["clones"] or r["views"] for r in app._table_rows()))

    def test_unmeasured_columns_are_dashes_not_zeros(self):
        # A repo with no path data has not been measured as shallow, and a
        # repo with no run has not been measured as unstarred.
        app = build_tui(self.data(), 40, 140, "table")
        row = app._table_rows()[0]
        self.assertIsNone(row["depth"])
        self.assertIsNone(row["stars_per_uv"])
        app.render(40, 140)
        text = " ".join(t for _y, _x, t in app.writes)
        self.assertNotIn("0.00   0.00", text)

    def test_per_run_views_explain_why_they_are_empty(self):
        # "Run catnip analyze first" sends the operator to a command that
        # answers "no runs found".
        for view in ("funnel", "lang"):
            app = build_tui(self.data(), 40, 140, view)
            app.render(40, 140)
            text = " ".join(t for _y, _x, t in app.writes)
            self.assertIn("No run on disk", text, view)
            self.assertIn("catnip run", text, view)
            self.assertNotIn("catnip analyze  to rebuild", text, view)


class TrafficListTests(unittest.TestCase):
    """The landing screen's ranked lists have to be actionable.

    They are the only repo names on view 1, and a ranked list you cannot
    open is a dead end — you read a name and then go hunting for it in
    another view.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self):
        return build_tui(self.data, 40, 140, "traffic")

    def test_traffic_exposes_its_top_list_to_the_cursor(self):
        self.assertTrue(self.app()._cursor_rows())

    def test_tab_switches_between_views_and_clones(self):
        app = self.app()
        app.handle_key(ord("\t"))
        self.assertEqual(app.traffic_pane, 1)
        app.handle_key(ord("\t"))
        self.assertEqual(app.traffic_pane, 0)

    def test_each_pane_indexes_its_own_list(self):
        app = self.app()
        top_v, top_c = app._traffic_lists()
        self.assertEqual(app._cursor_rows(), [n for n, _v in top_v])
        app.traffic_pane = 1
        self.assertEqual(app._cursor_rows(), [n for n, _v in top_c])

    def test_enter_opens_the_highlighted_repo_in_either_pane(self):
        for pane in (0, 1):
            app = self.app()
            app.traffic_pane = pane
            rows = app._cursor_rows()
            for i in range(len(rows)):
                app.cursor("traffic").to(i, len(rows), 8)
                app.handle_key(FakeCurses.KEY_ENTER)
                self.assertEqual(app.drilldown, rows[i], f"pane {pane} row {i}")
                app.drilldown = None

    def test_switching_pane_resets_the_cursor_into_range(self):
        app = self.app()
        rows = app._cursor_rows()
        app.cursor("traffic").to(len(rows) - 1, len(rows), 8)
        app.handle_key(ord("\t"))
        self.assertIn(app.selected_repo(), app._cursor_rows())


class AnomalyAgreementTests(unittest.TestCase):
    """The CSV and the TUI must not disagree about the same day.

    `catnip view anomaly` reads derive; `traffic_anomaly.csv` is written
    by its own module. Two implementations of one formula is a support
    question waiting to happen, so the shared constants are asserted here.
    """

    def test_materiality_floor_matches(self):
        from catnip import derive, traffic_anomaly
        self.assertEqual(traffic_anomaly.MIN_DEVIATION, derive.Z_MIN_VALUE)

    def test_both_score_a_mad_zero_spike_identically(self):
        from catnip import derive
        from catnip import traffic_anomaly as ta
        values = [0] * 11 + [421, 120, 0]
        med, mad, mean_ad = ta.median(values), ta.mad(values), ta.mean_ad(values)
        self.assertAlmostEqual(ta.modified_z_score(421, med, mad, mean_ad),
                               max(derive.modified_z(values)), places=6)

    def test_both_score_an_ordinary_spike_identically(self):
        from catnip import derive
        from catnip import traffic_anomaly as ta
        values = [5, 7, 6, 8, 40, 6, 5, 7, 6, 5, 6, 7, 6, 5]
        med, mad, mean_ad = ta.median(values), ta.mad(values), ta.mean_ad(values)
        self.assertAlmostEqual(ta.modified_z_score(40, med, mad, mean_ad),
                               max(derive.modified_z(values)), places=6)


if __name__ == "__main__":
    unittest.main()


class PairDetailTests(unittest.TestCase):
    """A correlation table gives a number and no way to check it.

    [space] on a pair opens both daily series, both residual series, and
    the raw-vs-residualized gap — which is the entire claim the view
    makes: that what survives is not the account-wide release wave.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self):
        return build_tui(self.data, 40, 150, "correlation")

    def test_space_opens_the_highlighted_pair(self):
        app = self.app()
        pairs = self.data.coupled(app.timeframe)["pairs"]
        if not pairs:
            self.skipTest("fixture has no coupled pairs")
        app.handle_key(ord(" "))
        self.assertEqual(set(app.drilldown_pair), {pairs[0]["a"], pairs[0]["b"]})

    def test_space_and_escape_both_close_it(self):
        app = self.app()
        if not self.data.coupled(app.timeframe)["pairs"]:
            self.skipTest("fixture has no coupled pairs")
        app.handle_key(ord(" "))
        app.handle_key(ord(" "))
        self.assertIsNone(app.drilldown_pair)
        app.handle_key(ord(" "))
        app.handle_key(27)
        self.assertIsNone(app.drilldown_pair)

    def test_capital_q_quits_from_the_pair_view(self):
        app = self.app()
        app.drilldown_pair = ("alpha", "beta-repo")
        self.assertTrue(app.handle_key(ord("Q")))

    def test_pair_view_renders_for_every_pair(self):
        app = self.app()
        for pair in self.data.coupled(app.timeframe)["pairs"]:
            a = self.app()
            a.drilldown_pair = (pair["a"], pair["b"])
            a.render(40, 150)
            text = " ".join(t for _y, _x, t in a.writes)
            self.assertIn("residual r", text)
            self.assertIn("Residuals", text)

    def test_an_uncoupled_pair_says_so_rather_than_faking_a_chart(self):
        app = self.app()
        app.drilldown_pair = ("alpha", "no-such-repo")
        app.render(40, 150)
        text = " ".join(t for _y, _x, t in app.writes)
        self.assertIn("not coupled", text)


class FunnelPagesTests(unittest.TestCase):
    """The grid says what KIND of page was read; only this says which."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_top_pages_follow_the_cursor(self):
        app = build_tui(self.data, 40, 150, "funnel")
        rows = app._funnel_repo_rows()
        for i in range(len(rows)):
            app.cursor("funnel").to(i, len(rows), 10)
            app.writes.clear()
            app.render(40, 150)
            text = " ".join(t for _y, _x, t in app.writes)
            self.assertIn(f"Top pages — {rows[i]['repo']}", text)

    def test_the_shade_ramp_is_named(self):
        app = build_tui(self.data, 40, 150, "funnel")
        app.render(40, 150)
        text = " ".join(t for _y, _x, t in app.writes)
        self.assertIn("shade:", text)
        self.assertIn("to 25%", text)
        self.assertIn("largest category", text)


class FunnelPaneTests(unittest.TestCase):
    """The funnel is two tables asking different questions.

    Sharing one sort and one cursor made whichever pane you were not
    looking at wrong, so [space] walks between them and each keeps its
    own.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self):
        return build_tui(self.data, 40, 150, "funnel")

    def test_space_walks_into_the_pages_pane_and_back(self):
        app = self.app()
        self.assertEqual(app.funnel_pane, 0)
        app.handle_key(ord(" "))
        self.assertEqual(app.funnel_pane, 1)
        app.handle_key(ord(" "))
        self.assertEqual(app.funnel_pane, 0)

    def test_space_on_the_funnel_never_opens_the_repo_drilldown(self):
        app = self.app()
        app.handle_key(ord(" "))
        self.assertIsNone(app.drilldown)

    def test_enter_still_opens_the_repo_drilldown(self):
        app = self.app()
        app.handle_key(FakeCurses.KEY_ENTER)
        self.assertIn(app.drilldown, [r["repo"] for r in app._funnel_repo_rows()])

    def test_each_pane_sorts_independently(self):
        app = self.app()
        grid_before = app.funnel_sort
        app.handle_key(ord("s"))
        self.assertNotEqual(app.funnel_sort, grid_before)
        self.assertEqual(app.pages_sort, 0, "grid sort moved the pages sort")
        app.handle_key(ord(" "))
        app.handle_key(ord("s"))
        self.assertEqual(app.pages_sort, 1)

    def test_pages_sort_by_uniques_differs_from_views(self):
        app = self.app()
        repo = app._funnel_repo_rows()[0]["repo"]
        app.pages_sort = 0
        by_views = [r.get("path") for r in app._funnel_page_rows(repo)]
        app.pages_sort = 1
        by_uniq = [r.get("path") for r in app._funnel_page_rows(repo)]
        self.assertEqual(sorted(by_views), sorted(by_uniq))

    def test_pages_panel_respects_terminal_height(self):
        for rows in range(18, 44):
            app = build_tui(self.data, rows, 150, "funnel")
            app.funnel_pane = 1
            app.render(rows, 150)
            drawn = [y for y, _x, t in app.writes if str(t).strip()]
            self.assertLess(max(drawn), rows,
                            f"funnel drew past the frame at height {rows}")


class SortContractTests(unittest.TestCase):
    """One contract, every sorted view: [s] picks the column, [S] picks the
    direction, and picking a column never silently changes the direction.

    Baking a natural direction into each column meant the audience view
    opened "sorted by score" with 0.00 at the top, and cycling columns
    flipped the order underneath you.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_numeric_columns_open_biggest_first(self):
        for view, keys in (("audience", "AUDIENCE_SORT_KEYS"),
                           ("profile", "PROFILE_SORT_KEYS"),
                           ("funnel", "FUNNEL_SORT_KEYS")):
            app = build_tui(self.data, 40, 150, view)
            for name, _key, natural in getattr(app, keys):
                if name in ("name", "category", "page"):
                    self.assertFalse(natural, f"{view}.{name} should sort A-Z")
                else:
                    self.assertTrue(natural, f"{view}.{name} should open biggest-first")

    def test_capital_s_flips_and_the_flip_survives_a_column_change(self):
        app = build_tui(self.data, 40, 150, "audience")
        first = app._footer_sort()
        app.handle_key(ord("S"))
        flipped = app._footer_sort()
        self.assertNotEqual(first[-1], flipped[-1], "S did not change direction")
        app.handle_key(ord("s"))
        self.assertEqual(app._footer_sort()[-1], flipped[-1],
                         "changing column reset the direction")

    TEXT_COLUMNS = ("name", "category", "page")

    def test_lowercase_s_never_changes_direction_between_numeric_columns(self):
        # Text columns are legitimately A-Z; the contract is that moving
        # between NUMERIC columns keeps the direction you chose.
        for view in ("audience", "profile", "table"):
            app = build_tui(self.data, 40, 150, view)
            arrow = app._footer_sort()[-1]
            for _ in range(6):
                app.handle_key(ord("s"))
                column = app._footer_sort()[:-1].strip()
                if column in self.TEXT_COLUMNS:
                    continue
                self.assertEqual(app._footer_sort()[-1], arrow,
                                 f"{view}: [s] changed direction on {column}")

    def test_audience_opens_with_the_highest_score_first(self):
        app = build_tui(self.data, 40, 150, "audience")
        rows = app._audience_rows()
        ranked = [r["score"] for r in rows if r["label"] != "low-signal"]
        self.assertEqual(ranked, sorted(ranked, reverse=True))


class MomentumViewTests(unittest.TestCase):
    """[space] on a deltas row asks which way it is going."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self):
        return build_tui(self.data, 40, 150, "deltas")

    def test_space_opens_momentum_not_the_generic_drilldown(self):
        app = self.app()
        app.handle_key(ord(" "))
        self.assertIsNotNone(app.drilldown_momentum)
        self.assertIsNone(app.drilldown)

    def test_enter_still_opens_the_generic_drilldown(self):
        app = self.app()
        app.handle_key(FakeCurses.KEY_ENTER)
        self.assertIsNotNone(app.drilldown)
        self.assertIsNone(app.drilldown_momentum)

    def test_space_and_escape_both_close_it(self):
        app = self.app()
        app.handle_key(ord(" "))
        app.handle_key(ord(" "))
        self.assertIsNone(app.drilldown_momentum)
        app.handle_key(ord(" "))
        app.handle_key(27)
        self.assertIsNone(app.drilldown_momentum)

    def test_it_renders_for_every_repo_at_every_timeframe(self):
        # The view body only: render() also draws the header and footer,
        # which own rows 0-1 and the last row by design.
        for repo in [r["repo"] for r in self.app()._delta_rows()]:
            for tf in TIMEFRAMES:
                for rows in (40, 24, 16):
                    app = build_tui(self.data, rows, 150, "deltas")
                    app.drilldown_momentum = repo
                    app.timeframe = tf
                    app._render_momentum_detail(rows - 3, 150)
                    drawn = [y for y, _x, t in app.writes if str(t).strip()]
                    if not drawn:
                        continue
                    self.assertLess(max(drawn), rows - 1,
                                    f"{repo}@{tf}@{rows} drew onto the footer")
                    self.assertGreaterEqual(min(drawn), 2,
                                            f"{repo}@{tf}@{rows} drew into the header")

    def test_deltas_sorts_follow_the_shared_contract(self):
        app = self.app()
        for name, _key, natural in app.DELTAS_SORT_KEYS:
            if name == "name":
                self.assertFalse(natural)
            else:
                self.assertTrue(natural, f"{name} should open biggest-first")
        arrow = app.sort_arrow(app._sort_desc(True))
        app.handle_key(ord("S"))
        self.assertNotEqual(app.sort_arrow(app._sort_desc(True)), arrow)


class FindingDetailTests(unittest.TestCase):
    """[space] on an attribution row opens the finding, not the repo.

    A row is a claim about one day. The thing to inspect is the claim —
    including the statistics the tier rests on, so the operator can
    overturn it rather than take it on trust.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def app(self):
        return build_tui(self.data, 40, 150, "attribution")

    def test_space_opens_the_finding_and_enter_opens_the_repo(self):
        app = self.app()
        app.handle_key(ord(" "))
        self.assertIsNotNone(app.drilldown_finding)
        self.assertIsNone(app.drilldown)
        app.handle_key(ord(" "))
        app.handle_key(FakeCurses.KEY_ENTER)
        self.assertIsNotNone(app.drilldown)
        self.assertIsNone(app.drilldown_finding)

    def test_escape_closes_it(self):
        app = self.app()
        app.handle_key(ord(" "))
        app.handle_key(27)
        self.assertIsNone(app.drilldown_finding)

    def test_j_k_walk_to_the_next_finding(self):
        app = self.app()
        rows = app._attribution_rows()
        if len(rows) < 2:
            self.skipTest("fixture has one finding")
        app.handle_key(ord(" "))
        first = app.drilldown_finding
        app.handle_key(ord("j"))
        self.assertNotEqual(app.drilldown_finding, first)

    def test_it_renders_for_every_row_and_stays_in_frame(self):
        rows = self.app()._attribution_rows()
        self.assertTrue(rows, "fixture produced no attribution rows")
        for row in rows:
            for height in (40, 24, 16):
                app = build_tui(self.data, height, 150, "attribution")
                app.drilldown_finding = row
                app._render_finding_detail(height - 3, 150)
                drawn = [y for y, _x, t in app.writes if str(t).strip()]
                if not drawn:
                    continue
                self.assertLess(max(drawn), height - 1,
                                f"{row['repo']}@{height} drew onto the footer")
                self.assertGreaterEqual(min(drawn), 2, f"{row['repo']}@{height}")

    def test_it_shows_the_statistics_the_tier_rests_on(self):
        app = self.app()
        row = next((r for r in app._attribution_rows()
                    if r["tier"] not in ("no-effect",)), None)
        if row is None:
            self.skipTest("fixture has only no-effect rows")
        app.drilldown_finding = row
        app.render(40, 150)
        text = " ".join(t for _y, _x, t in app.writes)
        self.assertIn("why this tier", text)
        self.assertIn("median", text)
        self.assertIn("floor", text)

    def test_a_borrowed_cause_is_marked_as_inferred(self):
        app = self.app()
        row = next((r for r in app._attribution_rows() if r["tier"] == "coupled"),
                   None)
        if row is None:
            self.skipTest("fixture has no coupled findings")
        app.drilldown_finding = row
        app.render(40, 150)
        text = " ".join(t for _y, _x, t in app.writes)
        self.assertIn("inferred, not observed", text)

    def test_context_is_present_however_thin_the_data(self):
        app = self.app()
        for row in app._attribution_rows():
            a = build_tui(self.data, 40, 150, "attribution")
            a.drilldown_finding = row
            a.render(40, 150)
            text = " ".join(t for _y, _x, t in a.writes)
            self.assertIn("context", text)


class EscapeStackTests(unittest.TestCase):
    """Escape unwinds one layer at a time, and pane focus is a layer.

    [space]/[tab] move INTO a pane, so escape has to move out of one. It
    used to fall through to the framework's quit: pressing escape to leave
    the funnel's pages pane closed the application from a screen the
    operator was still reading. Found by a demo recording, where the TUI
    exited mid-scene and the remaining scripted keys landed at the shell.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_escape_leaves_a_focused_pane_instead_of_quitting(self):
        cases = (
            ("funnel", "funnel_pane", 1, 0),
            ("anomaly", "anomaly_pane", 0, 1),
            ("traffic", "traffic_pane", 1, 0),
        )
        for view, attr, focused, unfocused in cases:
            app = build_tui(self.data, 40, 150, view)
            setattr(app, attr, focused)
            self.assertFalse(app.handle_key(27),
                             f"{view}: escape quit from a focused pane")
            self.assertEqual(getattr(app, attr), unfocused, view)

    def test_escape_still_quits_when_there_is_nothing_to_back_out_of(self):
        app = build_tui(self.data, 40, 150, "table")
        self.assertTrue(app.handle_key(27))

    def test_escape_unwinds_deepest_first(self):
        app = build_tui(self.data, 40, 150, "funnel")
        app.funnel_pane = 1
        app.search = "alpha"
        app.overlay = True
        app.handle_key(27)
        self.assertFalse(app.overlay)
        app.handle_key(27)
        self.assertEqual(app.search, "")
        app.handle_key(27)
        self.assertEqual(app.funnel_pane, 0)
        self.assertTrue(app.handle_key(27))


class RightAlignedHintTests(unittest.TestCase):
    """A detail view's back/next hint must survive to its last character.

    Each call site carried its own `max_x - <literal>` offset, and three of
    the five had drifted past `_put`'s right-edge guard: the attribution
    detail advertised "[j/k] next findin" and the pair detail "next pai".
    Nothing failed — the hint is decoration to every assertion in this file
    — so it shipped into a demo recording. The offset is now derived from
    the string; this test is what stops the literals coming back.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _states(self, cols):
        """(name, app, render) for every view that draws a right-aligned hint."""
        out = []

        app = build_tui(self.data, 40, cols, "attribution")
        app.handle_key(ord(" "))
        out.append(("finding", app, app._render_finding_detail))

        app = build_tui(self.data, 40, cols, "table")
        app.handle_key(FakeCurses.KEY_ENTER)
        out.append(("drilldown", app, app._render_drilldown))

        app = build_tui(self.data, 40, cols, "deltas")
        app.handle_key(ord(" "))
        out.append(("momentum", app, app._render_momentum_detail))

        app = build_tui(self.data, 40, cols, "correlation")
        app.handle_key(ord(" "))
        out.append(("pair", app, app._render_pair_detail))

        app = build_tui(self.data, 40, cols, "anomaly")
        app.anomaly_pane = 0
        app.handle_key(ord(" "))
        out.append(("event", app, app._render_event_detail))
        return out

    def test_no_hint_is_clipped_at_the_right_edge(self):
        # 150 is the demo geometry; the others are ordinary terminals.
        for cols in (150, 120, 100):
            for name, app, render in self._states(cols):
                app.writes = []
                render(37, cols)
                hints = [(x, t) for _y, x, t in app.writes
                         if t.startswith("[space/esc] back")]
                if not hints:
                    continue
                for x, text in hints:
                    self.assertTrue(
                        text.rstrip().endswith(("event", "finding", "repo", "pair")),
                        f"{name} @ {cols}: hint clipped to {text!r}")
                    self.assertLess(x + len(text), cols,
                                    f"{name} @ {cols}: hint runs off the edge")

    def test_a_narrow_terminal_still_places_the_hint_on_screen(self):
        for name, app, render in self._states(60):
            app.writes = []
            render(37, 60)
            for _y, x, _t in app.writes:
                self.assertGreaterEqual(x, 0, name)


class FunnelExplainerTests(unittest.TestCase):
    """The funnel's explainer must end in a whole word at any width.

    At 150 columns it read "... uniq sums per-page uniques, so it ove" —
    _put clipped the sentence mid-word, which reads as a rendering fault
    rather than as a legend. Clauses are dropped whole instead.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        run = make_run(root)
        with redirect_stdout(io.StringIO()):
            analyze_github(run, strict=True)
            history.ingest(root / "runs", root / "stats" / "history", "testuser")
        cls.data = AnalyticsData(run)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _explainer(self, cols):
        app = build_tui(self.data, 40, cols, "funnel")
        app.writes = []
        app._render_funnel_view(37, cols)
        return [t for y, _x, t in app.writes if y == 3 and t.startswith("Row = ")]

    def test_it_is_never_cut_mid_word(self):
        for cols in (200, 150, 120, 100, 80, 60):
            got = self._explainer(cols)
            self.assertEqual(len(got), 1, f"{cols}: explainer missing")
            self.assertTrue(got[0].endswith("."),
                            f"{cols}: clipped to {got[0]!r}")
            self.assertLess(len(got[0]) + 1, cols, cols)

    def test_the_widest_terminal_gets_every_clause(self):
        self.assertIn("over-counts", self._explainer(200)[0])

    def test_the_rolling_window_caveat_survives_the_demo_width(self):
        # It lives in the title precisely so it cannot be dropped: at 150
        # columns it was the first clause the explainer shed.
        app = build_tui(self.data, 40, 150, "funnel")
        app.writes = []
        app._render_funnel_view(37, 150)
        self.assertTrue(any("rolling 14d" in t for _y, _x, t in app.writes),
                        "the funnel no longer says it ignores the timeframe")
