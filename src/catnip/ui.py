#!/usr/bin/env python3
"""Curses-based interactive terminal UI over one analyzed catnip run.

Layout (fixed, non-overlapping regions, top to bottom):

    row 0        header: title | timeframe | metric | view
    rows 2..N    main view content
    row N        stats line
    last row     footer with keybindings

Everything that knows what a repository is lives here; everything that
knows what a terminal is lives in catnip.tui (vendored from pane). That
split is why the drawing layer can be re-vendored without touching a
single view.

A third layer sits between them: `catnip.derive` holds every windowed
computation, reading only the durable daily store. This module draws;
it does not decide what a number means. The practical test is that any
score on screen can be explained by the `[?]` overlay, whose text lives
next to the formula that produces it — a view whose numbers cannot be
explained from inside the TUI is a defect, and `derive.DERIVATIONS` is
where that stops being true.
"""
import argparse
import json
import sys
from collections import defaultdict
from contextlib import suppress
from datetime import date, timedelta
from pathlib import Path

from catnip import derive
from catnip.tui.charts import bar_chart
from catnip.tui.fmt import (
    compact_num as _compact_num,
)
from catnip.tui.fmt import (
    short_date as _short_date,
)
from catnip.tui.fmt import (
    sparkline as _sparkline,
)
from catnip.tui.fmt import (
    to_float as _float,
)
from catnip.tui.fmt import (
    to_int as _int,
)
from catnip.tui.framework import (
    TuiApp,
    curses_main,
    read_csv,
)
from catnip.tui.grids import diverging_bars, heatmap, ramp_glyph, scrollbar
from catnip.tui.interact import RowCursor

# Slot 2 was 'top' — repos ranked by age and stars, criteria that answered
# nothing the table did not answer better; its useful remnants are now
# sortable columns there. 'history' was slot '-' and duplicated 'traffic'
# with a longer window, so it became the 'epoch' stop on the timeframe
# cycle instead. Every other view kept its digit: muscle memory is a real
# cost and renumbering ten views to add one is not worth paying it.
# Slot 5 was 'freq' — weekly commit bars across every repo, which answered
# "how much did I type" and never "did any of it matter". Its data is not
# lost: commit-days are what the attribution view uses as causes, and the
# report skill still reads the weekly CSV.
VIEWS = ('traffic', 'audience', 'table', 'lang', 'attribution', 'deltas',
         'anomaly', 'profile', 'correlation', 'funnel')
VIEW_KEYS = '1234567890'

# Views whose rows are repos, and which therefore carry a cursor and open
# the drilldown on [enter].
# 'traffic' is here for its two top lists, which are the only repo names on
# the landing screen; [tab] chooses which list the cursor is in.
REPO_ROW_VIEWS = ('traffic', 'audience', 'table', 'attribution', 'deltas',
                  'anomaly', 'profile', 'funnel')

TIMEFRAMES = ('1d', '1w', '2w', 'all', 'epoch')

#: Repo scope, cycled with `o`. 'owned' leads: it is what the operator
#: means by "my repos" nearly every time they open this.
SCOPES = ('owned', 'all', 'forks')

# Trailing days per timeframe, owned by derive so the windows the views
# label and the windows the maths uses cannot drift apart.
DAY_WINDOW = derive.WINDOW_DAYS
# Glyphs used inside f-strings. Python 3.10 rejects a backslash escape in
# an f-string expression and 3.10 is this project's floor, so they are
# named here rather than written inline.
DELTA, UP_MARK, EM_DASH, ELLIPSIS = '\u0394', '\u25b2', '\u2014', '\u2026'
UP_ARROW, DOWN_ARROW = '\u2191', '\u2193'
BLOCK = '\u2588'

TF_LABEL = {'1d': 'last day', '1w': 'last 7d', '2w': 'last 14d',
            'all': 'all history', 'epoch': 'store epoch'}


class AnalyticsData:
    """Loads one run's analysis CSVs, plus the two cross-run stores.

    The per-run CSVs live beside the run; totals and history are
    account-wide and outlive any single run. Both store paths are passed
    in (the CLI resolves them from config) and fall back to the layout
    catnip writes — <data>/runs/<id> next to <data>/stats — so pointing
    the viewer at a run directory copied somewhere else still works.

    `fetch_dir` may be None. Runs are pruned; the store is not, so after
    retention has swept every run the durable daily store is still a
    complete answer for the views derived from it. Store-only mode loads
    those and leaves the per-run CSVs empty rather than refusing to open —
    the alternative is a viewer that goes dark on exactly the data catnip
    works hardest to keep.
    """

    def __init__(self, fetch_dir=None, stats_file=None, history_file=None):
        self.fetch_dir = Path(fetch_dir).resolve() if fetch_dir else None
        data_dir = self.fetch_dir.parent.parent if self.fetch_dir else None
        if stats_file:
            self.stats_file = Path(stats_file)
        else:
            self.stats_file = (data_dir / 'stats' / 'totals.json' if data_dir
                               else Path('totals.json'))
        if history_file:
            self.history_file = Path(history_file)
        else:
            self.history_file = (data_dir / 'stats' / 'history' / 'traffic_daily.json'
                                 if data_dir else Path('traffic_daily.json'))
        self.analysis_dir = self.fetch_dir / 'analysis' if self.fetch_dir else None
        self.store_only = self.fetch_dir is None
        self.reload()

    def _read_csv(self, fname):
        if self.analysis_dir is None:
            return []
        return read_csv(self.analysis_dir / fname)

    def reload(self):
        self._cache = {}
        self.repos = self._read_csv('github_repos.csv')
        self.stats = self._read_csv('github_stats_by_repo.csv')
        self.languages = self._read_csv('github_languages.csv')
        self.code_freq = self._read_csv('github_code_frequency.csv')
        self.prs = self._read_csv('github_pull_requests.csv')
        self.releases = self._read_csv('github_releases.csv')
        self.lang_dist = self._read_csv('github_lang_distribution.csv')
        self.top_repos_csv = self._read_csv('github_top_repos.csv')
        self.traffic_timeseries = self._read_csv('github_traffic_timeseries.csv')
        self.traffic_paths = self._read_csv('github_traffic_paths.csv')
        self.referrers = self._read_csv('github_traffic_referrers.csv')
        self.release_assets = self._read_csv('github_release_assets.csv')

        # Deep traffic analysis data
        self.anomaly_data = self._read_csv('traffic_anomaly.csv')
        self.profile_data = self._read_csv('traffic_cloner_profile.csv')
        self.funnel_data = self._read_csv('traffic_funnel.csv')
        self.correlation_data = self._read_csv('traffic_correlation.csv')

        self.totals = {}
        if self.stats_file.is_file():
            try:
                with self.stats_file.open('r') as fh:
                    self.totals = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self.totals = {}

        # Persistent history store (survives fetch-dir pruning).
        self.history = {}
        if self.history_file.is_file():
            try:
                with self.history_file.open('r') as fh:
                    self.history = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self.history = {}
        self.history_all = {'clones': {}, 'views': {}}
        for repo, metrics in (self.history.get('repos') or {}).items():
            if not repo:
                continue
            for metric in ('clones', 'views'):
                for day, val in (metrics.get(metric) or {}).items():
                    count = val[0] if isinstance(val, list) and val else 0
                    self.history_all[metric][day] = (
                        self.history_all[metric].get(day, 0) + count)

        self._totals = {
            'total_stars': sum(_int(r, 'stars') for r in self.repos),
            'total_forks': sum(_int(r, 'forks') for r in self.repos),
            'total_clones': sum(_int(r, 'total_clones') for r in self.repos),
            'total_views': sum(_int(r, 'total_views') for r in self.repos),
        }
        self._total_repos = len(self.repos)
        # Missing status tolerated: non-archived counts as active.
        self._active_count = sum(
            1 for r in self.repos
            if (r.get('status') or ('archived' if r.get('archived') == 'True' else 'active')) == 'active')

        if self.store_only:
            # No repos CSV to sum. The account-wide numbers are exactly what
            # totals.json already holds, so read them rather than showing zeros
            # under a header that claims to describe the whole account.
            for key in self._totals:
                if self.totals.get(key) is not None:
                    self._totals[key] = self.totals[key]
            self._total_repos = (self.totals.get('total_repos')
                                 or len(self.history.get('repos') or {}))
            self._active_count = max(
                0, self._total_repos - _int(self.totals, 'archived_repos'))

        self._build_traffic_series()

    def _build_traffic_series(self):
        """Build account-wide daily clone/view series and per-repo daily buckets
        from the tidy time-series CSV. Falls back to the older fixed-column
        clones/views CSVs when an old fetch has no time-series file."""
        self._repo_view_buckets = {}   # repo -> [(ts, count), ...] oldest first
        self._repo_clone_buckets = {}

        tmp = {'views': defaultdict(list), 'clones': defaultdict(list)}
        for row in self.traffic_timeseries:
            metric = row.get('metric', '')
            name = row.get('repo_name', '')
            if metric not in tmp or not name:
                continue
            tmp[metric][name].append(
                (row.get('timestamp', ''), _int(row, 'count')))
        for name, buckets in tmp['views'].items():
            self._repo_view_buckets[name] = sorted(buckets)
        for name, buckets in tmp['clones'].items():
            self._repo_clone_buckets[name] = sorted(buckets)

        # Trim trailing days that have zero account-wide activity in *both* metrics —
        # GitHub reports the fetch day (and sometimes the one before) as empty
        # because the counts haven't settled, which would make "last day" read 0.
        combined = defaultdict(int)
        for buckets in list(self._repo_view_buckets.values()) + list(self._repo_clone_buckets.values()):
            for ts, c in buckets:
                combined[ts] += c
        last_active = max((ts for ts, c in combined.items() if c > 0), default=None)
        if last_active is not None:
            for store in (self._repo_view_buckets, self._repo_clone_buckets):
                for name in list(store):
                    store[name] = [b for b in store[name] if b[0] <= last_active]

        # Same rule as _windowed_metric: the store is the source of truth for
        # daily series whenever it exists, so the charts and the top lists
        # cannot disagree about what a day contained.
        self.daily_views = self.history_series('views') or self._org_series(self._repo_view_buckets)
        self.daily_clones = self.history_series('clones') or self._org_series(self._repo_clone_buckets)

    @staticmethod
    def _org_series(repo_buckets):
        totals = defaultdict(int)
        for name, buckets in repo_buckets.items():
            if not name:
                continue
            for ts, c in buckets:
                totals[ts] += c
        return [{'ts': ts, 'label': _short_date(ts), 'count': c}
                for ts, c in sorted(totals.items())]

    @staticmethod
    def _windowed(repo_buckets, n):
        """{repo: total} over the trailing n *days*, one shared window for all
        repos. Slicing each repo's own buckets[-n:] instead would give every
        repo a different date range — GitHub's per-repo series start and end on
        different days — so a repo last seen two weeks ago still landed in
        'last day'."""
        if n is not None:
            latest = max((ts[:10] for buckets in repo_buckets.values()
                          for ts, _ in buckets), default=None)
            if latest is None:
                return {}
            cutoff = (date.fromisoformat(latest) - timedelta(days=n - 1)).isoformat()
        out = {}
        for name, buckets in repo_buckets.items():
            if not name:
                continue
            out[name] = sum(c for ts, c in buckets
                            if n is None or ts[:10] >= cutoff)
        return out

    def _repo_history_buckets(self, metric, field=0):
        """Per-repo daily buckets from the history store (survives fetch-dir
        pruning); {} when no store exists. field selects the stored pair —
        0 = raw count, 1 = uniques."""
        out = {}
        for repo, metrics in (self.history.get('repos') or {}).items():
            if not repo:
                continue
            days = (metrics.get(metric) or {})
            if days:
                out[repo] = sorted(
                    (d, v[field] if isinstance(v, list) and len(v) > field else 0)
                    for d, v in days.items())
        return out

    def _windowed_metric(self, metric, csv_buckets, timeframe):
        """Windowed per-repo totals, read from the durable store by preference.

        The run CSVs hold GitHub's rolling window, which loses its left edge
        every day; the store is append-only and max-merged. Reading the store
        for *every* timeframe (not just 'all') is what makes a window mean the
        same thing on day 1 and day 60. The CSVs remain the fallback for a run
        that has not been ingested yet.
        """
        n = DAY_WINDOW.get(timeframe, 14)
        return self._windowed(self._repo_history_buckets(metric) or csv_buckets, n)

    def windowed_views(self, timeframe):
        """{repo: views} summed over the trailing window for this timeframe."""
        return self._windowed_metric('views', self._repo_view_buckets, timeframe)

    def windowed_clones(self, timeframe):
        """{repo: clones} summed over the trailing window for this timeframe."""
        return self._windowed_metric('clones', self._repo_clone_buckets, timeframe)

    def history_series(self, metric):
        """Org daily series for one metric from the history store (oldest
        first); [] when no store exists."""
        days = self.history_all.get(metric, {})
        return [{'ts': d, 'label': _short_date(d), 'count': c}
                for d, c in sorted(days.items())]

    # ---- derived metrics -----------------------------------------------
    #
    # Every one of these reads the durable store through catnip.derive and
    # is memoized per (kind, timeframe). render() runs on every keypress,
    # and the residualized pair scan is O(active repos squared) — without a
    # cache, holding `j` down would recompute it forty times a second.
    # `reload()` drops the cache, so `r` still picks up new data.

    def _derived(self, kind, timeframe, fn):
        key = (kind, timeframe)
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def window_days(self, timeframe):
        return self._derived('window', timeframe,
                             lambda: derive.window(self.history, timeframe))

    def deltas(self, timeframe):
        return self._derived('deltas', timeframe,
                             lambda: derive.deltas(self.history, timeframe))

    def audience(self, timeframe):
        return self._derived('audience', timeframe,
                             lambda: derive.audience(self.history, timeframe))

    def intent(self, timeframe):
        return self._derived(
            'intent', timeframe,
            lambda: derive.intent(self.history, timeframe,
                                  audience_rows=self.audience(timeframe)))

    def anomalies(self, timeframe):
        return self._derived('anomalies', timeframe,
                             lambda: derive.anomalies(self.history, timeframe))

    def coupled(self, timeframe):
        return self._derived('coupled', timeframe,
                             lambda: derive.coupled(self.history, timeframe))

    def attribution(self, timeframe):
        return self._derived('attribution', timeframe,
                             lambda: derive.attribution(self.history, timeframe))

    def funnel_depth(self):
        return self._derived('funnel_depth', '-',
                             lambda: derive.funnel_depth(self.funnel_data))

    def events(self, repo, days):
        return derive.events_in(self.history, repo, days)

    def forked_repos(self):
        """Names GitHub reports as forks, from the newest run's repo CSV.

        Empty when there is no run: a store on its own cannot know, and
        guessing from names would be worse than saying so.
        """
        if 'forks' not in self._cache:
            self._cache['forks'] = {r.get('repo_name', '') for r in self.repos
                                    if r.get('is_fork') == 'True'}
        return self._cache['forks']

    def knows_forks(self):
        """Whether fork status is available at all (a pre-is_fork run says no)."""
        return bool(self.repos) and any('is_fork' in r for r in self.repos)

    def has_store(self):
        return bool((self.history or {}).get('repos'))


class AnalyticsTUI(TuiApp):
    def __init__(self, stdscr, data, timeframe='2w', view='traffic'):
        super().__init__(stdscr)
        self.data = data
        self.timeframe = timeframe if timeframe in TIMEFRAMES else '2w'
        self.view = view if view in VIEWS else 'traffic'
        self.init_view_state()

    def init_view_state(self):
        """Per-view UI state, in one place.

        Defined as a method because the offline layout tests build an app
        without running __init__ (there is no terminal to attach to), and a
        second copy of these defaults in the test harness drifts the moment
        a view gains a mode — which it did, twice.
        """
        self.search = ''
        self.table_sort = 0       # index into TABLE_SORT_KEYS
        self.table_status = 0     # index into the statuses actually present
        self.profile_sort = 0     # index into PROFILE_SORT_KEYS
        # Hidden by default on both classifying views. Two thirds of a
        # personal account is repos with under ten unique cloners+visitors;
        # showing them first buries the eleven repos that can actually be
        # described under sixty that cannot.
        self.profile_hide_low = True   # 'l' shows the unrankable repos
        self.anomaly_filter = 0   # 0=all, 1=extreme, 2=significant, 3=minor
        self.funnel_filter = 0    # index into funnel categories (0=all)
        # The funnel is two tables with different questions, so they get
        # different sorts and their own cursors; [tab] chooses which one
        # j/k and s act on.
        self.funnel_pane = 0      # 0 = the repo grid, 1 = top pages
        self.funnel_sort = 0      # index into FUNNEL_SORT_KEYS
        self.pages_sort = 0       # index into PAGES_SORT_KEYS
        self.audience_sort = 0    # index into AUDIENCE_SORT_KEYS
        self.traffic_pane = 0     # 0 = top views list, 1 = top clones
        self.audience_hide_low = True   # 'l' shows the unclassifiable repos
        # Sort direction per view. Every sorted view shows an arrow, because
        # "sort: score" over a column whose smallest value is on top reads
        # as a bug whatever the reason for it.
        self.sort_flip = dict.fromkeys(
            (*REPO_ROW_VIEWS, 'anomaly_events', 'funnel_pages'), False)
        self.deltas_hide_zero = True   # 'z' un-collapses the unchanged rows
        self.deltas_sort = 0      # index into DELTAS_SORT_KEYS
        self.drilldown_momentum = None  # repo name for the slope detail
        self.drilldown_finding = None   # an attribution row, for its detail
        # One cursor per view, so switching away and back lands on the row
        # you were reading rather than at the top of the list.
        self.cursors = {v: RowCursor() for v in REPO_ROW_VIEWS}
        # The account-event list is a second cursor on the anomaly screen:
        # its rows are days, not repos, so it cannot share one.
        self.cursors['anomaly_events'] = RowCursor()
        # Correlation rows are pairs, not repos, so they cannot share the
        # repo cursor either.
        self.cursors['correlation_pairs'] = RowCursor()
        self.cursors['funnel_pages'] = RowCursor()
        self.anomaly_pane = 1     # 0 = account events, 1 = the repo x day grid
        self.event_sort = 0       # index into EVENT_SORT_KEYS
        # First visible day in the grid, or None to pin to the newest. A
        # 54-day window cannot show 54 labelled columns, and squeezing it
        # to one character per day with a label every third column gives
        # marks nobody can date.
        self.anomaly_day_offset = None
        self._anomaly_visible = 0
        # Global repo scope. A fork's commits, languages and traffic belong
        # to a project you did not write; mixing them into "your languages"
        # or "most commits" answers a question nobody asked. Global rather
        # than per-view because the answer is the same on every screen.
        self.scope = 0            # index into SCOPES
        self.drilldown = None     # repo name, or None for the account views
        self.drilldown_event = None   # an account-event day, or None
        self.drilldown_pair = None    # (repo_a, repo_b), or None
        self.overlay = False      # [?] derivation overlay
        self.scroll = 0

    def init_extra_pairs(self):
        self.curses.init_pair(7, self.curses.COLOR_MAGENTA, -1)

    def _bar_chart(self, top, series, max_x, plot_h, color, title=None, bar_w=None,
                   max_bar_w=14, bin_unit=''):
        """Draw a vertical bar chart of [{'label','count'}] from row `top`.

        Returns the row index just below the chart (after the x-axis labels).
        max_bar_w raises the shared 6-col cap so charts with few bars (history
        months, traffic days) fill a wide terminal (plan 13); the shared lib
        still shrinks bars to fit when there are many, and aggregates adjacent
        buckets (annotated in the title with bin_unit) past that."""
        curses = self.curses
        return bar_chart(
            self._put, curses, top, series, plot_h, max_x,
            title=title, title_attr=curses.color_pair(6) | curses.A_BOLD,
            axis_attr=curses.color_pair(6), color=color,
            bar_w=bar_w, max_bar_w=max_bar_w, value_labels=True, label_fit=True,
            bin_unit=bin_unit,
        )

    def _hbar(self, value, max_value, width):
        """Shared horizontal bar (plan 08 phase 6)."""
        if max_value <= 0 or width <= 0:
            return ''
        return '█' * max(0, min(width, int(value / max_value * width)))

    def scope_label(self):
        return SCOPES[self.scope]

    def in_scope(self, name):
        """Whether a repo passes the global owned/all/forks filter.

        Fork status comes from the newest run. With no run — or a run
        predating the is_fork column — nothing is filtered, because
        "we cannot tell" must not silently become "not a fork".
        """
        scope = SCOPES[self.scope]
        if scope == 'all' or not self.data.knows_forks():
            return True
        forked = name in self.data.forked_repos()
        return forked if scope == 'forks' else not forked

    def _filtered(self, rows, key='repo_name'):
        """Apply the / search filter and the global repo scope."""
        rows = [r for r in rows if self.in_scope(r.get(key, '') or '')]
        if not self.search:
            return rows
        needle = self.search.lower()
        return [r for r in rows if needle in (r.get(key, '') or '').lower()]

    def _prompt_search(self, max_y, max_x):
        curses = self.curses
        prompt = '/'
        self._put(max_y - 1, 0, (prompt + ' ' * (max_x - 2))[: max_x - 1])
        curses.echo()
        with suppress(curses.error):
            curses.curs_set(1)
        try:
            raw = self.stdscr.getstr(max_y - 1, 1, 40)
            self.search = raw.decode('utf-8', 'replace').strip()
        except curses.error:
            self.search = ''
        finally:
            curses.noecho()
            with suppress(curses.error):
                curses.curs_set(0)
        self.scroll = 0

    # ---- cursor and drilldown ------------------------------------------

    def _footer_sort(self):
        """'column ↑/↓' for the footer, whichever view is up."""
        if self.view == 'audience':
            name, _k, natural = self.AUDIENCE_SORT_KEYS[self.audience_sort]
        elif self.view == 'profile':
            name, _k, natural = self.PROFILE_SORT_KEYS[self.profile_sort]
        elif self.view == 'table':
            name = self.TABLE_SORT_KEYS[self.table_sort][0]
            natural = name != 'name'
        else:
            return ''
        return f'{name}{self.sort_arrow(self._sort_desc(natural))}'

    def sort_arrow(self, desc):
        return DOWN_ARROW if desc else UP_ARROW

    def _sort_desc(self, natural_desc):
        """Whether to sort descending, after the view's [S] flip."""
        return natural_desc != self.sort_flip.get(self.view, False)

    def cursor(self, view=None):
        """The RowCursor for a view; a scratch one for views without rows."""
        return self.cursors.get(view or self.view) or RowCursor()

    def _cursor_rows(self):
        """Repo names for the current view, in the order it renders them.

        This is the one place the cursor and the renderer have to agree.
        Each view builds its row list through a helper that both this and
        its `_render_*` call, so a sort or a filter can never leave [enter]
        opening a different repo than the one under the highlight.
        """
        view = self.view
        if view == 'traffic':
            top_v, top_c = self._traffic_lists()
            return [name for name, _v in (top_v if self.traffic_pane == 0 else top_c)]
        if view == 'attribution':
            return [r['repo'] for r in self._attribution_rows()]
        if view == 'audience':
            return [r['repo'] for r in self._audience_rows()]
        if view == 'table':
            return [r.get('repo_name', '') for r in self._table_rows()]
        if view == 'deltas':
            return [r['repo'] for r in self._delta_rows()]
        if view == 'anomaly':
            return [name for name, _cells in self._anomaly_rows()]
        if view == 'profile':
            return [r['repo'] for r in self._profile_rows()]
        if view == 'funnel':
            return [r['repo'] for r in self._funnel_repo_rows()]
        return []

    def _page(self, max_y=None):
        """Rows of list body visible at the moment — the cursor's page size."""
        if max_y is None:
            max_y, _ = self.stdscr.getmaxyx()
        return max(1, max_y - 9)

    def selected_repo(self):
        rows = self._cursor_rows()
        if not rows:
            return None
        cur = self.cursor().clamp(len(rows), self._page())
        return rows[cur.index]

    def handle_key(self, key):
        curses = self.curses
        # Escape unwinds one layer at a time: overlay, then drilldown, then
        # the search filter. Quitting from three screens deep because the
        # top layer swallowed the key is the way this kind of stack annoys
        # people.
        if key == 27:
            if self.overlay:
                self.overlay = False
                return False
            if self.drilldown_finding:
                self.drilldown_finding = None
                return False
            if self.drilldown_momentum:
                self.drilldown_momentum = None
                return False
            if self.drilldown_pair:
                self.drilldown_pair = None
                return False
            if self.drilldown_event:
                self.drilldown_event = None
                return False
            if self.drilldown:
                self.drilldown = None
                return False
            if self.search:
                self.search = ''
                return False
            # Pane focus is a layer too. [space]/[tab] move INTO a pane, so
            # escape has to move out of one — it is the key everyone reaches
            # for, whatever the footer advertises. Without this it fell
            # through to the framework's quit and closed the app from a
            # screen the operator was still reading. Caught by a demo take:
            # the recorder pressed escape to leave the funnel's pages pane
            # and the TUI exited mid-scene.
            if self.view == 'funnel' and self.funnel_pane:
                self.funnel_pane = 0
                return False
            if self.view == 'anomaly' and self.anomaly_pane == 0:
                self.anomaly_pane = 1
                return False
            if self.view == 'traffic' and self.traffic_pane:
                self.traffic_pane = 0
                self.cursor('traffic').reset()
                return False
        if key == ord('?'):
            self.overlay = not self.overlay
            return False
        if self.overlay:
            # The overlay is modal by design: it explains the view beneath
            # it, so changing that view while it is up is meaningless.
            # Lowercase q dismisses one layer, capital Q leaves the app —
            # otherwise the only way out of two stacked layers is to guess
            # how many times to press escape.
            if key == ord('Q'):
                return True
            if key == ord('q'):
                self.overlay = False
            return False
        # [space] toggles: it opens the highlighted repo and, from inside,
        # closes it again. [enter] only opens — a key that also exits is
        # useful, but a key you press to confirm should never be the key
        # that undoes the thing you confirmed.
        if key in (curses.KEY_ENTER, 10, 13, ord(' ')):
            if (self.drilldown_finding or self.drilldown_momentum
                    or self.drilldown_pair or self.drilldown_event
                    or self.drilldown):
                if key == ord(' '):
                    self.drilldown_finding = None
                    self.drilldown_momentum = None
                    self.drilldown_pair = None
                    self.drilldown_event = None
                    self.drilldown = None
                return False
            if self.view == 'attribution' and key == ord(' '):
                # [space] opens the FINDING; [enter] still opens the repo.
                # A row is a claim about one day, and the thing to inspect
                # is the claim, not the repo it happens to name.
                rows = self._attribution_rows()
                if rows:
                    cur = self.cursor('attribution').clamp(len(rows), self._page())
                    self.drilldown_finding = rows[cur.index]
                return False
            if self.view == 'deltas' and key == ord(' '):
                # [space] asks "which way is this going"; [enter] still
                # opens the generic repo drilldown.
                repo = self.selected_repo()
                if repo:
                    self.drilldown_momentum = repo
                return False
            if self.view == 'funnel' and key == ord(' '):
                # Down into this repo's pages, and back out again. The
                # grid and the page list are one question at two depths,
                # so space walks between them; [enter] still opens the
                # full repo drilldown.
                self.funnel_pane = 1 - self.funnel_pane
                self.cursors['funnel_pages'].reset()
                return False
            if self.view == 'correlation':
                pairs = self.data.coupled(self.timeframe)['pairs']
                if pairs:
                    cur = self.cursors['correlation_pairs'].clamp(len(pairs),
                                                                  self._page())
                    self.drilldown_pair = (pairs[cur.index]['a'], pairs[cur.index]['b'])
                return False
            if self.view == 'anomaly' and self.anomaly_pane == 0:
                events = self._anomaly_events()
                if events:
                    cur = self.cursors['anomaly_events'].clamp(len(events),
                                                               self._page())
                    self.drilldown_event = events[cur.index]['day']
                return False
            repo = self.selected_repo()
            if repo:
                self.drilldown = repo
            return False
        if self.drilldown_finding:
            if key == ord('Q'):
                return True
            if key in (ord('q'), ord('h'), curses.KEY_LEFT,
                       curses.KEY_BACKSPACE, 127, 8):
                self.drilldown_finding = None
                return False
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                rows = self._attribution_rows()
                if rows:
                    delta = 1 if key in (curses.KEY_DOWN, ord('j')) else -1
                    cur = self.cursor('attribution').move(delta, len(rows),
                                                          self._page())
                    self.drilldown_finding = rows[cur.index]
                return False
            if key == ord('r'):
                self.data.reload()
            return False
        if self.drilldown_momentum:
            if key == ord('Q'):
                return True
            if key in (ord('q'), ord('h'), curses.KEY_LEFT,
                       curses.KEY_BACKSPACE, 127, 8):
                self.drilldown_momentum = None
                return False
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                rows = self._cursor_rows()
                if rows:
                    delta = 1 if key in (curses.KEY_DOWN, ord('j')) else -1
                    cur = self.cursor('deltas').move(delta, len(rows), self._page())
                    self.drilldown_momentum = rows[cur.index]
                return False
            if key in (ord('t'), ord('T')):
                step = 1 if key == ord('t') else -1
                self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) + step)
                                            % len(TIMEFRAMES)]
                return False
            if key == ord('r'):
                self.data.reload()
            return False
        if self.drilldown_pair:
            if key == ord('Q'):
                return True
            if key in (ord('q'), ord('h'), curses.KEY_LEFT,
                       curses.KEY_BACKSPACE, 127, 8):
                self.drilldown_pair = None
                return False
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                pairs = self.data.coupled(self.timeframe)['pairs']
                if pairs:
                    delta = 1 if key in (curses.KEY_DOWN, ord('j')) else -1
                    cur = self.cursors['correlation_pairs'].move(
                        delta, len(pairs), self._page())
                    self.drilldown_pair = (pairs[cur.index]['a'], pairs[cur.index]['b'])
                return False
            if key == ord('r'):
                self.data.reload()
            return False
        if self.drilldown_event:
            if key == ord('Q'):
                return True
            if key in (ord('q'), ord('h'), curses.KEY_LEFT,
                       curses.KEY_BACKSPACE, 127, 8):
                self.drilldown_event = None
                return False
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                events = self._anomaly_events()
                if events:
                    delta = 1 if key in (curses.KEY_DOWN, ord('j')) else -1
                    cur = self.cursors['anomaly_events'].move(
                        delta, len(events), self._page())
                    self.drilldown_event = events[cur.index]['day']
                return False
            if key in (ord('t'), ord('T')):
                step = 1 if key == ord('t') else -1
                self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) + step)
                                            % len(TIMEFRAMES)]
                # The window moved; this day may no longer be in it.
                days = self.data.window_days(self.timeframe)
                if self.drilldown_event not in days:
                    self.drilldown_event = None
                return False
            if key == ord('r'):
                self.data.reload()
                return False
            return False
        if self.drilldown:
            if key == ord('Q'):
                return True
            if key in (ord('q'), ord('h'), curses.KEY_LEFT,
                       curses.KEY_BACKSPACE, 127, 8):
                self.drilldown = None
                return False
            if key == ord('t'):
                self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) + 1)
                                            % len(TIMEFRAMES)]
                return False
            if key == ord('T'):
                self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) - 1)
                                            % len(TIMEFRAMES)]
                return False
            if key == ord('r'):
                self.data.reload()
                return False
            # Step through the same list the drilldown was opened from, so
            # a repo can be compared with its neighbour without going back.
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
                rows = self._cursor_rows()
                if rows:
                    delta = 1 if key in (curses.KEY_DOWN, ord('j')) else -1
                    cur = self.cursor().move(delta, len(rows), self._page())
                    self.drilldown = rows[cur.index]
                return False
            # Everything else is inert while a repo is open: the account
            # views' sorts and filters have no meaning on one repo, and
            # silently changing the screen behind the drilldown is worse
            # than doing nothing.
            return False
        if super().handle_key(key):
            return True
        if key == ord('r'):
            self.data.reload()
        elif key == ord('v'):
            self.view = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)]
            self.scroll = 0
        elif key == ord('V'):
            idx = (VIEWS.index(self.view) - 1) % len(VIEWS)
            self.view = VIEWS[idx]
            self.scroll = 0
        elif 0 <= key < 256 and chr(key) in VIEW_KEYS:
            idx = VIEW_KEYS.index(chr(key))
            if idx < len(VIEWS):
                self.view = VIEWS[idx]
                self.scroll = 0
        elif key == ord('t'):
            self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) + 1) % len(TIMEFRAMES)]
        elif key == ord('T'):
            self.timeframe = TIMEFRAMES[(TIMEFRAMES.index(self.timeframe) - 1) % len(TIMEFRAMES)]
        elif key == ord('s') and self.view == 'deltas':
            self.deltas_sort = (self.deltas_sort + 1) % len(self.DELTAS_SORT_KEYS)
            self.cursor('deltas').reset()
        elif key == ord('s') and self.view == 'funnel':
            if self.funnel_pane:
                self.pages_sort = (self.pages_sort + 1) % len(self.PAGES_SORT_KEYS)
                self.cursors['funnel_pages'].reset()
            else:
                self.funnel_sort = (self.funnel_sort + 1) % len(self.FUNNEL_SORT_KEYS)
                self.cursor('funnel').reset()
        elif key == ord('S') and self.view == 'funnel':
            which = 'funnel_pages' if self.funnel_pane else 'funnel'
            self.sort_flip[which] = not self.sort_flip.get(which, False)
        elif key == ord('s') and self.view == 'anomaly' and self.anomaly_pane == 0:
            self.event_sort = (self.event_sort + 1) % len(self.EVENT_SORT_KEYS)
            self.cursors['anomaly_events'].reset()
        elif key == ord('S') and self.view == 'anomaly' and self.anomaly_pane == 0:
            self.sort_flip['anomaly_events'] = not self.sort_flip.get(
                'anomaly_events', False)
            self.cursors['anomaly_events'].reset()
        elif key == ord('S') and self.view in REPO_ROW_VIEWS:
            self.sort_flip[self.view] = not self.sort_flip.get(self.view, False)
            self.scroll = 0
        elif key == ord('s'):
            if self.view == 'profile':
                self.profile_sort = (self.profile_sort + 1) % len(self.PROFILE_SORT_KEYS)
                self.scroll = 0
            elif self.view == 'table':
                self.table_sort = (self.table_sort + 1) % len(self.TABLE_SORT_KEYS)
                self.scroll = 0
            elif self.view == 'audience':
                self.audience_sort = (self.audience_sort + 1) % len(self.AUDIENCE_SORT_KEYS)
                self.scroll = 0
        elif key == ord('\t') and self.view == 'traffic':
            self.traffic_pane = 1 - self.traffic_pane
            self.cursor('traffic').reset()
        elif key == ord('\t') and self.view == 'anomaly':
            self.anomaly_pane = 1 - self.anomaly_pane
        elif key == ord('\t') and self.view == 'funnel':
            self.funnel_pane = 1 - self.funnel_pane
            self.cursors['funnel_pages'].reset()
        elif (self.view == 'anomaly'
              and key in (ord('h'), ord('l'), curses.KEY_LEFT, curses.KEY_RIGHT)):
            days = self.data.window_days(self.timeframe)
            visible = getattr(self, '_anomaly_visible', len(days)) or len(days)
            last = max(0, len(days) - visible)
            step = -3 if key in (ord('h'), curses.KEY_LEFT) else 3
            base = last if self.anomaly_day_offset is None else self.anomaly_day_offset
            self.anomaly_day_offset = max(0, min(base + step, last))
        elif key == ord('o'):
            self.scope = (self.scope + 1) % len(SCOPES)
            for cur in self.cursors.values():
                cur.reset()
            self.scroll = 0
        elif key == ord('z') and self.view == 'deltas':
            self.deltas_hide_zero = not self.deltas_hide_zero
            self.scroll = 0
        elif key in (ord('f'), ord('F')):
            if self.view == 'anomaly':
                self.anomaly_filter = ((self.anomaly_filter + 1)
                                      % len(self.ANOMALY_SEVERITIES))
                self.scroll = 0
            elif self.view == 'table':
                self.table_status = (self.table_status + 1) % len(self._table_statuses())
                self.scroll = 0
            elif self.view == 'funnel':
                self.funnel_filter = (self.funnel_filter + 1) % (len(self._funnel_categories()) + 1)
                self.scroll = 0
        elif key == ord('l') and self.view == 'profile':
            self.profile_hide_low = not self.profile_hide_low
            self.scroll = 0
        elif key == ord('l') and self.view == 'audience':
            self.audience_hide_low = not self.audience_hide_low
            self.scroll = 0
        elif key in (curses.KEY_UP, ord('k'), curses.KEY_DOWN, ord('j'),
                     ord('g'), ord('G')):
            self._move(key)
        elif key == ord('/'):
            max_y, max_x = self.stdscr.getmaxyx()
            self._prompt_search(max_y, max_x)
        return False

    def _move(self, key):
        """j/k/g/G — a cursor on repo-row views, plain scroll elsewhere.

        Both paths keep `self.scroll` authoritative for drawing, so the
        views that have no notion of a selected repo (lang) are
        unaffected by the drilldown existing at all.
        """
        curses = self.curses
        max_y, _ = self.stdscr.getmaxyx()
        page = self._page(max_y)
        if self.view == 'anomaly' and self.anomaly_pane == 0:
            events = self._anomaly_events()
            cur = self.cursors['anomaly_events']
            if key in (curses.KEY_UP, ord('k')):
                cur.move(-1, len(events), page)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur.move(1, len(events), page)
            elif key == ord('g'):
                cur.home(len(events), page)
            else:
                cur.end(len(events), page)
            return
        if self.view == 'funnel' and self.funnel_pane == 1:
            repo = (self._funnel_repo_rows() or [{}])[
                self.cursor('funnel').index if self._funnel_repo_rows() else 0].get('repo')
            total = len(self._funnel_page_rows(repo)) if repo else 0
            cur = self.cursors['funnel_pages']
            if key in (curses.KEY_UP, ord('k')):
                cur.move(-1, total, page)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur.move(1, total, page)
            elif key == ord('g'):
                cur.home(total, page)
            else:
                cur.end(total, page)
            return
        if self.view == 'correlation':
            pairs = self.data.coupled(self.timeframe)['pairs']
            cur = self.cursors['correlation_pairs']
            if key in (curses.KEY_UP, ord('k')):
                cur.move(-1, len(pairs), page)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur.move(1, len(pairs), page)
            elif key == ord('g'):
                cur.home(len(pairs), page)
            else:
                cur.end(len(pairs), page)
            self.scroll = cur.scroll
            return
        if self.view in REPO_ROW_VIEWS:
            rows = self._cursor_rows()
            cur = self.cursor()
            if key in (curses.KEY_UP, ord('k')):
                cur.move(-1, len(rows), page)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur.move(1, len(rows), page)
            elif key == ord('g'):
                cur.home(len(rows), page)
            else:
                cur.end(len(rows), page)
            self.scroll = cur.scroll
            return
        bound = self._scroll_bound()
        if key in (curses.KEY_UP, ord('k')):
            self.scroll = max(0, self.scroll - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            # Clamp: unbounded scrolling ran the list off the top and left the
            # view blank with no indication of how far back 'k' had to go.
            self.scroll = min(self.scroll + 1, max(0, bound - 1))
        elif key == ord('g'):
            self.scroll = 0
        else:
            self.scroll = max(0, bound - max(1, max_y - 8))

    def _no_run_note(self, what):
        """Why a per-run view is empty, and what would actually fix it.

        Telling someone to `catnip analyze` when there is no run on disk
        sends them to a command that answers "no runs found". These views
        read the newest run's CSVs, and the run — unlike the store — is
        the part retention deletes.
        """
        if self.data.store_only:
            return [
                f'No run on disk, so there is no {what}.',
                '',
                'The durable store answers traffic, audience, deltas, anomalies,',
                'intent and coupling — this view is not one of those: it reads a',
                "run's CSVs, and runs are what `catnip prune` deletes.",
                '',
                'Run  catnip run  to collect one.',
            ]
        return [f'No {what} in the newest run.',
                '', 'Run  catnip analyze  to rebuild it.']

    def _put_lines(self, y, lines, max_x, indent=3):
        for i, line in enumerate(lines):
            self._put(y + i, indent, line[: max_x - indent - 1], self.curses.A_DIM)
        return y + len(lines)

    #: How a selected row is drawn. NOT a full-width inverse bar: these
    #: views end in bars, sparklines and heatmap cells, and reverse video
    #: across them destroys exactly the shape the row exists to show. The
    #: name cell carries the highlight and the right edge carries a marker,
    #: so the selection is legible at both ends of a wide row without
    #: touching anything in between.
    SELECT_MARK = '\u25c0'

    def _scrollbar(self, top, height, total, offset, max_x):
        """A scrollbar at the right edge; nothing when the list fits."""
        # max_x - 2, not max_x - 1: _put rejects any x >= max_x - 1, so a
        # bar at the last column is silently dropped and the operator gets
        # no overflow indication at all.
        scrollbar(self._put, self.curses, top, height, max_x - 2, total, offset,
                  attr=self.curses.color_pair(6))

    def _mark_selected(self, y, max_x, selected):
        """Flag the right edge of a selected row."""
        if selected:
            # Left of the scrollbar column, which owns max_x - 2.
            self._put(y, max_x - 4, self.SELECT_MARK,
                      self.curses.color_pair(5) | self.curses.A_BOLD)

    def _traffic_lists(self):
        """(top views, top clones) for the current window — the same lists
        the traffic view draws and the cursor indexes into."""
        tf = self.timeframe
        wv = self.data.windowed_views(tf)
        wc = self.data.windowed_clones(tf)
        return (sorted(wv.items(), key=lambda kv: -kv[1])[:8],
                sorted(wc.items(), key=lambda kv: -kv[1])[:8])

    def _lang_rows(self, all_langs=False):
        """Language distribution for the repos in scope.

        The account-wide CSV is pre-aggregated per language and cannot be
        filtered by repo, so anything other than "all" is recomputed from
        the per-repo language bytes. A fork carries upstream's whole
        codebase; leaving them in makes "your languages" a description of
        other people's projects.
        """
        if SCOPES[self.scope] == 'all' or not self.data.knows_forks():
            dist = self.data.lang_dist
        else:
            totals, repos = defaultdict(int), defaultdict(set)
            for row in self.data.languages:
                name = row.get('repo_name', '')
                if not self.in_scope(name):
                    continue
                lang = row.get('language', '')
                if not lang:
                    continue
                totals[lang] += _int(row, 'bytes')
                repos[lang].add(name)
            grand = sum(totals.values()) or 1
            dist = [{'language': lang,
                     'total_bytes': str(b),
                     'total_bytes_pct': f'{b / grand * 100:.2f}',
                     'repo_count': str(len(repos[lang]))}
                    for lang, b in sorted(totals.items(), key=lambda kv: -kv[1])]
        if all_langs:
            return dist
        return [r for r in dist if _float(r, 'total_bytes_pct') >= 0.1]

    def _funnel_categories(self):
        return sorted({r.get('category', '') for r in self.data.funnel_data
                       if r.get('category')})

    def _scroll_bound(self):
        """Approximate number of rendered lines for the current view, for g/G."""
        d = self.data
        if self.view == 'lang':
            return len(d.lang_dist)
        if self.view == 'attribution':
            return len(self._attribution_rows())
        if self.view == 'correlation':
            return len(self.data.coupled(self.timeframe)['pairs'])
        if self.view in REPO_ROW_VIEWS:
            return len(self._cursor_rows())
        return len(d.repos)

    def render(self, max_y, max_x):
        self._render_header(max_x)
        self._render_footer(max_y, max_x)

        avail = max_y - 3
        if avail <= 0:
            return

        if self.drilldown_finding:
            self._render_finding_detail(avail, max_x)
        elif self.drilldown_momentum:
            self._render_momentum_detail(avail, max_x)
        elif self.drilldown_pair:
            self._render_pair_detail(avail, max_x)
        elif self.drilldown_event:
            self._render_event_detail(avail, max_x)
        elif self.drilldown:
            self._render_drilldown(avail, max_x)
        else:
            self._render_view(avail, max_x)
        if self.overlay:
            self._render_overlay(max_y, max_x)

    def _render_view(self, avail, max_x):
        if self.view == 'traffic':
            self._render_traffic_view(avail, max_x)
        elif self.view == 'audience':
            self._render_audience_view(avail, max_x)
        elif self.view == 'table':
            self._render_table_view(avail, max_x)
        elif self.view == 'lang':
            self._render_lang_view(avail, max_x)
        elif self.view == 'attribution':
            self._render_attribution_view(avail, max_x)
        elif self.view == 'deltas':
            self._render_deltas_view(avail, max_x)
        elif self.view == 'anomaly':
            self._render_anomaly_view(avail, max_x)
        elif self.view == 'profile':
            self._render_profile_view(avail, max_x)
        elif self.view == 'correlation':
            self._render_correlation_view(avail, max_x)
        elif self.view == 'funnel':
            self._render_funnel_view(avail, max_x)

    def _render_overlay(self, max_y, max_x):
        """The [?] derivation panel: how this view's numbers were computed.

        Drawn over the view rather than replacing it, so the figures being
        explained stay in peripheral view. The text comes from
        derive.DERIVATIONS, which lives beside the formulas themselves —
        an overlay maintained separately from the code it describes starts
        lying within one release.
        """
        curses = self.curses
        lines = derive.derivation('drilldown' if self.drilldown else self.view)
        width = min(max_x - 4, max(len(line) for line in lines) + 4)
        height = min(max_y - 4, len(lines) + 4)
        top = max(2, (max_y - height) // 2)
        left = max(1, (max_x - width) // 2)
        border = curses.color_pair(6) | curses.A_BOLD
        self._put(top, left, '┌' + '─' * (width - 2) + '┐', border)
        for i in range(1, height - 1):
            self._put(top + i, left, '│' + ' ' * (width - 2) + '│', border)
        self._put(top + height - 1, left, '└' + '─' * (width - 2) + '┘', border)
        for i, line in enumerate(lines[: height - 3]):
            attr = (curses.color_pair(2) | curses.A_BOLD) if i == 0 else 0
            if line.startswith('  ') and ('=' in line or line.strip().startswith('|')):
                attr = curses.color_pair(3)
            self._put(top + 1 + i, left + 2, line[: width - 4], attr)
        hint = ' [?] or [esc] closes '
        if len(hint) < width - 2:
            self._put(top + height - 1, left + 2, hint, self.curses.A_DIM)

    def _render_header(self, max_x):
        curses = self.curses
        # 'github' as a fallback reads like an account name and is not one.
        # totals.json can legitimately lack an owner (a run with no manifest
        # rebuilds it empty), and the durable store knows it independently.
        owner = (self.data.totals.get('owner')
                 or (self.data.history or {}).get('owner')
                 or '(owner unset)')
        search_tag = f'  /{self.search}' if self.search else ''
        # Never claim a scope the data cannot support. A run predating the
        # is_fork column leaves fork status unknown, in_scope() correctly
        # filters nothing, and a bare "[owned]" in the header then asserts
        # a filter that is not being applied — which is worse than showing
        # no filter at all, because the numbers look curated.
        if self.scope_label() == 'all':
            scope_tag = ''
        elif self.data.knows_forks():
            scope_tag = f'  [{self.scope_label()}]'
        else:
            scope_tag = f'  [{self.scope_label()}: needs catnip analyze]'
        text = f' catnip  [{owner}]  [{self.timeframe}]{scope_tag}{search_tag}'
        self._put(0, 0, text.ljust(max_x - 1), curses.color_pair(6) | self.curses.A_BOLD)
        # View strip: digit-key jump targets with the active view highlighted.
        # The full strip is ~100 cols, so shorten the inactive names to fit
        # rather than letting the last views get clipped off the right edge.
        x = 1
        for tag, view in self._view_strip(max_x):
            attr = curses.color_pair(5) if view == self.view else self.curses.A_DIM
            self._put(1, x, tag, attr)
            x += len(tag) + 1

    def _view_strip(self, max_x):
        """[(tag, view)] for the header strip, shortened to fit max_x."""
        for cut in (None, 6, 4, 3):
            tags = [(f'{VIEW_KEYS[i]}:{v if cut is None or v == self.view else v[:cut]}', v)
                    for i, v in enumerate(VIEWS)]
            if sum(len(t) + 1 for t, _ in tags) + 1 <= max_x - 1:
                return tags
        # Too narrow for even a shortened strip: name the active view only.
        i = VIEWS.index(self.view)
        return [(f'{VIEW_KEYS[i]}:{self.view} ({i + 1}/{len(VIEWS)}, v/V cycles)',
                 self.view)]

    def _render_traffic_view(self, avail, max_x):
        curses = self.curses
        tf = self.timeframe
        if tf == 'epoch':
            return self._render_epoch_view(avail, max_x)
        win = TF_LABEL.get(tf, 'last 14d')

        # Windowed traffic for this timeframe (t/T cycles). The per-repo
        # windowed totals come from _traffic_lists, so the cursor and the
        # drawn lists index the same ranking.
        n = DAY_WINDOW.get(tf, 14)
        if n is None:
            # 'all' reads the history store's account-wide series (>14 days once
            # ingested); falls back to the fetch's own window.
            views_window = self.data.history_series('views') or self.data.daily_views
            clones_window = self.data.history_series('clones') or self.data.daily_clones
        else:
            views_window = self.data.daily_views[-n:]
            clones_window = self.data.daily_clones[-n:]
        sum_v = sum(b['count'] for b in views_window)
        sum_c = sum(b['count'] for b in clones_window)

        y = 2
        # Two stacked daily bar charts. Heights adapt to available vertical space;
        # drop the clones chart to a sparkline when the terminal is short.
        two_charts = avail >= 30
        plot_h = max(3, min(7, (avail - 20) // (2 if two_charts else 1)))
        y = self._bar_chart(
            y, views_window, max_x, plot_h, curses.color_pair(2),
            title=f'Daily Views  ({win}: {_compact_num(sum_v)})', bin_unit='d')
        y += 1
        if two_charts:
            y = self._bar_chart(
                y, clones_window, max_x, plot_h, curses.color_pair(1),
                title=f'Daily Clones  ({win}: {_compact_num(sum_c)})', bin_unit='d')
            y += 1
        else:
            spark = _sparkline([b['count'] for b in clones_window])
            self._put(y, 1, f'Daily Clones ({win}: {_compact_num(sum_c)})  {spark}',
                      curses.color_pair(6) | self.curses.A_BOLD)
            y += 2

        # Two side-by-side top lists, windowed to the timeframe. They are
        # the only repo names on the landing screen, so they carry the
        # cursor too: [tab] moves between the lists, [enter] opens the
        # highlighted repo. A ranked list you cannot act on is a dead end.
        col2 = max_x // 2
        top_v, top_c = self._traffic_lists()
        focus = self.traffic_pane
        cur = self.cursor('traffic')
        cur.clamp(len(top_v if focus == 0 else top_c), max(1, len(top_v)))
        head = curses.color_pair(6) | self.curses.A_BOLD
        self._put(y, 1, f'Top Views ({win})', head)
        self._put(y, col2, f'Top Clones ({win})', head)
        self._put(y, col2 - 12 if focus == 0 else max_x - 12, '[tab]',
                  curses.color_pair(5))
        for pane, (rows, x) in enumerate(((top_v, 3), (top_c, col2 + 2))):
            for i, (name, val) in enumerate(rows):
                selected = pane == focus and i == cur.index
                marker = '>' if selected else ' '
                attr = curses.color_pair(5) if selected else 0
                self._put(y + 1 + i, x - 1,
                          f'{marker}{i+1:>2}. {name[:18]:<18} {_compact_num(val):>6}',
                          attr)
        y += max(len(top_v), len(top_c)) + 2

        tt = self.data._totals.get('total_stars', 0)
        tf_forks = self.data._totals.get('total_forks', 0)
        # Current-CSV sums cover GitHub's trailing ~14-day window only; true
        # all-time comes from the deduped rolling totals (plan 08 phase 5).
        tc_14d = self.data._totals.get('total_clones', 0)
        tv_14d = self.data._totals.get('total_views', 0)
        tc_all = self.data.totals.get('total_clones')
        tv_all = self.data.totals.get('total_views')
        alltime = (f'  all-time: {_compact_num(tc_all)}c/{_compact_num(tv_all)}v'
                   if tc_all is not None else '')
        # Uniques approximate non-owner traffic (GitHub's API can't exclude the
        # repo owner, so raw counts are inflated by your own visits) — plan 13.
        uniq = self.data.totals.get('uniques_14d') or {}
        uc, uv = uniq.get('clones'), uniq.get('views')
        uniq_str = (f'  uniq14d: {_compact_num(uc)}c/{_compact_num(uv)}v'
                    if uc is not None else '')
        if y < avail + 2:
            self._put(y, 1,
                      f'Repos: {self.data._total_repos}  Active: {self.data._active_count}  '
                      f'Stars: {tt:,}  Forks: {tf_forks:,}  '
                      f'14d: {_compact_num(tc_14d)}c/{_compact_num(tv_14d)}v{uniq_str}{alltime}',
                      self.curses.color_pair(5) | self.curses.A_BOLD)
            if y + 1 < avail + 2 and uc is not None:
                self._put(y + 1, 1, 'uniques ≈ distinct visitors (excludes your own '
                          'repeat traffic, which inflates raw counts)', self.curses.A_DIM)

    # ---- A. audience: human vs fetcher ---------------------------------

    # Every numeric column sorts biggest-first; only names go A-Z. Baking
    # a per-column direction meant [s] silently changed the ORDER as well
    # as the column — score opened ascending, so a 0.00 sat at the top of
    # a list sorted "by score". Direction is [S]'s job and nothing else's.
    AUDIENCE_SORT_KEYS = (
        ('score', lambda r: r['score'], True),
        ('ratio', lambda r: r['ratio'], True),
        ('burst', lambda r: (r['burst'] if r['burst'] is not None else -1), True),
        ('clones', lambda r: r['clones'], True),
        ('name', lambda r: r['repo'].lower(), False),
    )

    AUDIENCE_COLORS = {'audience': 1, 'crawler': 4, 'mixed': 3, 'low-signal': 0}

    def _audience_rows(self):
        label, key, natural = self.AUDIENCE_SORT_KEYS[self.audience_sort]
        desc = self._sort_desc(natural)
        rows = [r for r in self.data.audience(self.timeframe).values()
                if self.in_scope(r['repo'])]
        if self.audience_hide_low:
            rows = [r for r in rows if r['label'] != 'low-signal']
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r['repo'].lower()]
        rows.sort(key=key, reverse=desc)
        # Classified repos first, unclassifiable ones beneath. A repo with
        # seven data points has not earned a place in the ordering, but
        # dropping it silently invites "where did it go".
        return ([r for r in rows if r['label'] != 'low-signal']
                + [r for r in rows if r['label'] == 'low-signal'])

    def _render_audience_view(self, avail, max_x):
        """Who is fetching this repo — a person, or a machine?

        The question the live data raised: one repo took 541 clones from
        three unique visitors in an afternoon while another took 448 from
        127. Both look like "adoption" in a clone count, and only one is.
        """
        curses = self.curses
        sort_label = self.AUDIENCE_SORT_KEYS[self.audience_sort][0]
        rows = self._audience_rows()
        win = TF_LABEL.get(self.timeframe, 'window')
        self._put(2, 1, f'Audience — human vs fetcher  ({win})  [sort: {sort_label}]',
                  curses.color_pair(6) | self.curses.A_BOLD)
        low_tag = 'low hidden' if self.audience_hide_low else 'l=hide low'
        self._put(3, 1, f'ratio = uniq_cloners / uniq_visitors, log scale.  [{low_tag}]  '
                  '[?] explains every component.', self.curses.A_DIM)
        x = 1
        for name in ('audience', 'mixed', 'crawler', 'low-signal'):
            pair = self.AUDIENCE_COLORS[name]
            self._put(4, x, '\u2588',
                      curses.color_pair(pair) if pair else self.curses.A_DIM)
            self._put(4, x + 2, name, self.curses.A_DIM)
            x += len(name) + 5
        self._put(4, x + 2, f'(low-signal = fewer than {derive.AUDIENCE_MIN_SIGNAL} '
                  f'unique cloners + visitors: not enough to classify)',
                  self.curses.A_DIM)
        if not rows:
            self._put(6, 3, 'No repo has traffic in this window.', self.curses.A_DIM)
            return

        name_w = min(24, max(12, max_x - 62))
        header = (f'  {"repo":<{name_w}} {"score":>6} {"class":<9} {"ratio":>7} '
                  f'{"burst":>6} {"depth":>6} {"ref":>4}  clones/views')
        self._put(6, 3, header, curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 7
        cur = self.cursor('audience').clamp(len(rows), page)
        self.scroll = cur.scroll
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            pair = self.AUDIENCE_COLORS.get(row['label'], 0)
            color = curses.color_pair(pair) if pair else self.curses.A_DIM
            selected = idx == cur.index
            burst = f"{row['burst']:.2f}" if row['burst'] is not None else '  -'
            depth = f"{row['depth']:.1f}" if row['depth'] is not None else '  -'
            refs = (str(row['referrer_diversity'])
                    if row['referrer_diversity'] is not None else '-')
            marker = '>' if selected else ' '
            self._put(8 + i, 3, f'{marker} {row["repo"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else color)
            self._put(8 + i, 3 + 2 + name_w,
                      f' {row["score"]:>6.2f} '
                      f'{row["label"]:<11} {row["ratio"]:>7.2f} {burst:>6} {depth:>6} '
                      f'{refs:>4}  {_compact_num(row["clones"])}c/'
                      f'{_compact_num(row["views"])}v', color)
            self._mark_selected(8 + i, max_x, selected)
        self._scrollbar(8, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)
        # Say what could not be computed rather than letting a dash read as
        # a zero: a withheld component is missing evidence, not a low score.
        missing = sorted({m for r in rows for m in r['missing']})
        if missing and 9 + min(page, len(rows)) < avail + 2:
            self._put(9 + min(page, len(rows)), 3,
                      f'withheld for some repos (see [?]): {", ".join(missing)}',
                      self.curses.A_DIM)

    # Sortable columns, three of them derived. The retired 'top' view
    # ranked repos by age and stars on a screen of their own; those are
    # columns, not screens, and momentum/audience/depth are the questions
    # its criteria were reaching for and never asked.
    # No 'score' key: it sorted by the run's own traffic_score, a column
    # this view does not show and cannot show without a run. Sorting by an
    # invisible number is indistinguishable from not sorting.
    TABLE_SORT_KEYS = (
        ('clones', lambda r: r['clones']),
        ('momentum', lambda r: r['momentum']),
        ('audience', lambda r: r['audience']),
        ('depth', lambda r: (r['depth'] is not None, r['depth'] or 0)),
        ('views', lambda r: r['views']),
        ('stars/uv', lambda r: (r['stars_per_uv'] is not None, r['stars_per_uv'] or 0)),
        ('name', lambda r: r['repo_name'].lower()),
    )

    #: The filter cycle is built from the statuses actually present, not
    #: from this list. With no run on disk every row is 'store', and a
    #: hard-coded cycle walked the operator through three consecutive
    #: screens reading "No repos match" with no hint that the filter, not
    #: the data, was empty.
    TABLE_STATUS = ('all', 'active', 'stale', 'archived')

    def _table_rows(self):
        return self._table_rows_filtered()

    def _table_rows_raw(self):
        """Repo rows joined with the derived columns.

        The CSV supplies identity and status; the store supplies every
        number that moves. A repo present in the store but pruned from the
        newest run still gets a row — the store outlives runs, and a table
        that hides what the store remembers is a table that shrinks when
        retention runs.
        """
        deltas = self.data.deltas(self.timeframe)
        audience = self.data.audience(self.timeframe)
        depth = self.data.funnel_depth()
        days = self.data.window_days(self.timeframe)
        rows = []
        seen = set()
        for r in self._filtered(self.data.repos):
            name = r.get('repo_name', '')
            seen.add(name)
            rows.append(self._table_row(name, r, deltas, audience, depth, days))
        for name in derive.repo_names(self.data.history):
            if name in seen or name not in deltas or not self.in_scope(name):
                continue
            if self.search and self.search.lower() not in name.lower():
                continue
            rows.append(self._table_row(name, {}, deltas, audience, depth, days))
        return rows

    def _table_rows_filtered(self):
        rows = self._table_rows_raw()
        status_filter = self._table_status(rows)
        if status_filter == 'with traffic':
            rows = [r for r in rows if r['clones'] or r['views']]
        elif status_filter != 'all':
            rows = [r for r in rows if r['status'] == status_filter]
        label, key = self.TABLE_SORT_KEYS[self.table_sort]
        return sorted(rows, key=key, reverse=self._sort_desc(label != 'name'))

    def _table_statuses(self, rows=None):
        """The filter cycle: traffic first, then 'all', then real statuses.

        'with traffic' leads because it answers the question the table is
        usually open for. Ninety rows of which sixty read 0 clones and 0
        views is a directory listing, not an account view.
        """
        if rows is None:
            rows = self._table_rows_raw()
        present = {r['status'] for r in rows}
        ordered = [s for s in self.TABLE_STATUS[1:] if s in present]
        ordered += sorted(present - set(self.TABLE_STATUS))
        return ['with traffic', 'all', *ordered]

    def _table_status(self, rows=None):
        options = self._table_statuses(rows)
        return options[self.table_status % len(options)]

    def _table_row(self, name, csv_row, deltas, audience, depth, days):
        d = deltas.get(name) or {}
        a = audience.get(name) or {}
        f = depth.get(name) or {}
        uv = derive.total(self.data.history, name, 'views', days, derive.UNIQ)
        stars = _int(csv_row, 'stars')
        return {
            'repo_name': name,
            # 'no run': active/stale/archived comes from a run's repo CSV,
            # and there is no run — the repo was renamed or deleted, or
            # retention swept the run. The store outlives runs, so the row
            # survives with its traffic intact and its status unknown.
            # 'store' was the first word here and nobody could tell what it
            # claimed; the status column should say what is not known, not
            # where the row came from.
            'status': csv_row.get('status') or ('unknown' if csv_row else 'no run'),
            'score': float(csv_row.get('traffic_score', 0) or 0),
            'clones': (d.get('clones') or {}).get('cur', 0),
            'views': (d.get('views') or {}).get('cur', 0),
            'momentum': (d.get('clones') or {}).get('delta') or 0,
            'comparable': d.get('comparable', False),
            'audience': a.get('score', 0.0),
            'audience_label': a.get('label'),
            # None, not 0.0, when the input is absent entirely: a repo with
            # no path data has not been measured as shallow, and a repo whose
            # run has been pruned has not been measured as unstarred. Same
            # rule the audience composite follows.
            'depth': f.get('depth_ratio') if f else None,
            'stars': stars if csv_row else None,
            'stars_per_uv': (stars / max(uv, 1)) if csv_row else None,
        }

    def _render_table_view(self, avail, max_x):
        curses = self.curses
        rows = self._table_rows()
        status_filter = self._table_status()
        name = self.TABLE_SORT_KEYS[self.table_sort][0]
        sort_label = f'{name} {self.sort_arrow(self._sort_desc(name != "name"))}'
        win = TF_LABEL.get(self.timeframe, 'window')
        self._put(2, 1, f'Repo Table  [{len(rows)}] {status_filter}  ({win})  '
                  f'[sort: {sort_label}]', curses.color_pair(6) | self.curses.A_BOLD)
        note = ('momentum and audience follow t/T; depth is rolling 14d. '
                '[?] explains each column.')
        if self.data.store_only:
            note = ('no run on disk, so status/depth/stars are unknown ("no run") '
                    '\u2014 traffic columns come from the durable store.')
        self._put(3, 1, note, self.curses.A_DIM)
        if not rows:
            self._put(5, 3, 'No repos match.', self.curses.A_DIM)
            return

        name_w = max(10, min(28, max_x - 60))
        self._put(5, 3,
                  f'  {"repo":<{name_w}} {"clones":>7} {"views":>7} {"moment":>7} '
                  f'{"aud":>5} {"depth":>6} {"st/uv":>6}  status',
                  curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 6
        cur = self.cursor('table').clamp(len(rows), page)
        self.scroll = cur.scroll
        for i, r in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            selected = idx == cur.index
            base = 0
            marker = '>' if selected else ' '
            self._put(7 + i, 3, f'{marker} {r["repo_name"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else 0)
            self._put(7 + i, 3 + 2 + name_w,
                      f' {_compact_num(r["clones"]):>7} '
                      f'{_compact_num(r["views"]):>7} ', 0)
            x = 3 + 2 + name_w + 1 + 8 + 8
            mom = f'{r["momentum"]:>+7d}' if r['comparable'] else '    n/a'
            self._put(7 + i, x, mom, base or self._delta_color(
                r['momentum'] if r['comparable'] else None))
            aud_pair = self.AUDIENCE_COLORS.get(r['audience_label'], 0)
            self._put(7 + i, x + 8, f'{r["audience"]:>5.2f}',
                      base or (curses.color_pair(aud_pair) if aud_pair
                               else self.curses.A_DIM))
            depth = ('     -' if r['depth'] is None else f'{r["depth"]:>6.2f}')
            self._put(7 + i, x + 14, depth,
                      base or (curses.color_pair(1) if (r['depth'] or 0) >= 1.0
                               else self.curses.A_DIM))
            st_uv = ('     -' if r['stars_per_uv'] is None
                     else f'{r["stars_per_uv"]:>6.2f}')
            self._put(7 + i, x + 21, st_uv, base or self.curses.A_DIM)
            self._put(7 + i, x + 29, f'[{r["status"]:>8}]',
                      base or self._status_color(r['status']))
            self._mark_selected(7 + i, max_x, selected)
        self._scrollbar(7, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)

    def _status_color(self, status):
        curses = self.curses
        if status == 'active':
            return curses.color_pair(1)
        if status == 'archived':
            return curses.color_pair(4)
        return self.curses.A_DIM

    def _render_lang_view(self, avail, max_x):
        curses = self.curses
        # Only languages >= 0.1% of the codebase; the long <0.1% tail is noise.
        dist = self._lang_rows()
        hidden = len(self._lang_rows(all_langs=True)) - len(dist)
        title = 'Language Distribution  (by bytes, >=0.1%)'
        if hidden:
            title += f'  [{hidden} smaller languages hidden]'
        self._put(2, 1, title, curses.color_pair(6) | self.curses.A_BOLD)
        self._put(4, 3, f'{"language":<18} {"share":<25} {"bytes":>12}  repos',
                  curses.color_pair(2) | self.curses.A_BOLD)
        scroll = self.scroll
        if not dist:
            self._put_lines(6, self._no_run_note('language data'), max_x)
            return
        # Fixed readable palette (skip pair 5 = black-on-cyan reverse video).
        palette = [curses.color_pair(1), curses.color_pair(2), curses.color_pair(3),
                   curses.color_pair(6), curses.color_pair(7), curses.color_pair(4)]
        max_bytes = max((_int(r, 'total_bytes') for r in dist), default=1) or 1
        # Bar fills the width left after the fixed language + numeric columns.
        bar_w = max(10, max_x - 50)
        for i, r in enumerate(dist[scroll:scroll + avail - 5]):
            idx = scroll + i
            lang = r.get('language', '')[:18]
            tb = _int(r, 'total_bytes')
            pct = _float(r, 'total_bytes_pct')
            rc = r.get('repo_count', '')
            bar = self._hbar(tb, max_bytes, bar_w)
            color = palette[idx % len(palette)] if idx < 6 else self.curses.A_DIM
            self._put(6 + i, 3, f'{lang:<18} {bar:<{bar_w}} {tb:>12,} ({pct:>5.2f}%) {rc:>3}', color)

    ATTRIBUTION_TIER_COLOR = {
        'direct': 1, 'coupled': 2, 'account': 3, 'dip': 0,
        'unexplained': 4, 'no-effect': 0,
    }

    def _attribution_rows(self):
        rows = self.data.attribution(self.timeframe)['rows']
        rows = [r for r in rows if self.in_scope(r['repo'])]
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r['repo'].lower()]
        return rows

    def _render_attribution_view(self, avail, max_x):
        """What moved, and what plausibly caused it.

        A timeline rather than an anomaly table, so it says something on a
        store one run old: GitHub returns a repo's whole release history on
        the first fetch, and "here is what you shipped and what happened
        after" needs no variance, no previous window and no aligned days.
        The statistical tiers appear as the store deepens.
        """
        curses = self.curses
        result = self.data.attribution(self.timeframe)
        rows = self._attribution_rows()
        win = TF_LABEL.get(self.timeframe, 'window')
        self._put(2, 1, f'Attribution \u2014 what moved, and what caused it  ({win})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, f'A release or push explains movement for '
                  f'{derive.ATTRIBUTION_LAG} day(s) after it. [?] explains each tier.',
                  self.curses.A_DIM)
        x = 1
        for tier in ('direct', 'coupled', 'account', 'unexplained', 'no-effect'):
            pair = self.ATTRIBUTION_TIER_COLOR[tier]
            self._put(4, x, BLOCK,
                      curses.color_pair(pair) if pair else self.curses.A_DIM)
            self._put(4, x + 2, tier, self.curses.A_DIM)
            x += len(tier) + 5
        if not result['coupling_available']:
            self._put(4, x, f'(coupled tier needs {derive.COUPLE_MIN_DAYS} days '
                      f'of store)', self.curses.A_DIM)

        if not rows:
            self._put_lines(6, [
                'Nothing has moved and nothing shipped in this window.',
                '',
                'Widen the window with t/T, or run  catnip run  to collect more.',
                'This view fills in as soon as there is a release, a push, or a',
                'day whose traffic departs from that repo\'s own normal.',
            ], max_x)
            return

        name_w = min(22, max(12, max_x // 6))
        self._put(6, 3, f'  {"when":<6} {"repo":<{name_w}} {"cause":<8} '
                  f'{"detail":<24} {"effect":<22} {"tier":<11} via',
                  curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 6
        cur = self.cursor('attribution').clamp(len(rows), page)
        self.scroll = cur.scroll
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            selected = idx == cur.index
            pair = self.ATTRIBUTION_TIER_COLOR[row['tier']]
            color = curses.color_pair(pair) if pair else self.curses.A_DIM
            if row['tier'] == 'no-effect':
                effect = 'no measurable change'
            elif row['median'] is None:
                effect = f'{row["metric"]} {row["value"]}'
            else:
                effect = (f'{row["metric"]} {row["median"]:.0f} \u2192 {row["value"]}'
                          f'  z{row["z"]:+.0f}')
            via = ''
            if row['via']:
                via = f'{row["via"][0][:18]} r={row["via"][1]:+.2f}'
            lag = f' +{row["lag"]}d' if row.get('lag') else ''
            self._put(8 + i, 3, f'{">" if selected else " "} {_short_date(row["day"]):<6} '
                      f'{row["repo"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else color)
            self._put(8 + i, 3 + 2 + 7 + name_w,
                      f'{(row["cause"] or "-"):<8} {(row["detail"] or "")[:24]:<24} '
                      f'{effect:<22} {row["tier"] + lag:<11} {via}', color)
            self._mark_selected(8 + i, max_x, selected)
        self._scrollbar(8, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)

    def _windowed_pr_counts(self):
        """{repo: {'opened': n, 'merged': n}} for PRs whose created_at / merged_at
        falls in the current timeframe window (plan 13: PR data folded into the
        deltas view). 'all' timeframe = no date bound."""
        n = DAY_WINDOW.get(self.timeframe, 14)
        cutoff = None
        if n is not None:
            newest = None
            for r in self.data.prs:
                for k in ('created_at', 'merged_at', 'closed_at'):
                    d = _short_date(r.get(k, '') or '')
                    if d and (newest is None or r.get(k, '') > newest):
                        newest = r.get(k, '')
            if newest:
                from datetime import datetime, timedelta, timezone
                try:
                    end = datetime.fromisoformat(newest.replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    end = datetime.now(timezone.utc)
                cutoff = (end - timedelta(days=n)).isoformat()
        counts = {}
        for r in self.data.prs:
            name = r.get('repo_name', '')
            if not name:
                continue
            created = r.get('created_at', '') or ''
            merged = r.get('merged_at', '') or ''
            in_open = created and (cutoff is None or created >= cutoff)
            in_merge = merged and (cutoff is None or merged >= cutoff)
            if not (in_open or in_merge):
                continue
            c = counts.setdefault(name, {'opened': 0, 'merged': 0})
            if in_open:
                c['opened'] += 1
            if in_merge:
                c['merged'] += 1
        return counts

    DELTAS_SORT_KEYS = (
        ('movement', lambda r: (abs(r['clones']['delta'] or r['clones']['cur'])
                                + abs(r['views']['delta'] or r['views']['cur'])), True),
        ('clones', lambda r: r['clones']['cur'], True),
        ('views', lambda r: r['views']['cur'], True),
        ('\u0394clones', lambda r: (r['clones']['delta'] or 0), True),
        ('\u0394views', lambda r: (r['views']['delta'] or 0), True),
        ('rate', lambda r: r['clones']['rate'], True),
        ('name', lambda r: r['repo'].lower(), False),
    )

    def _delta_rows(self):
        """Rows for the deltas view, sorted by the size of the movement.

        Replaces a diff of two rolling-total snapshots. Those totals lose
        their oldest day every night, so their difference mixed "what
        happened" with "what aged out" and produced confident negatives
        for repos where nothing had happened at all. These come from the
        daily store and respect the timeframe selector, which the old view
        ignored outright.
        """
        rows = [r for r in self.data.deltas(self.timeframe).values()
                if self.in_scope(r['repo'])]
        prc = self._windowed_pr_counts()
        for row in rows:
            row['prs'] = prc.get(row['repo'], {'opened': 0, 'merged': 0})
        if self.deltas_hide_zero:
            # "Nothing this window" means nothing happened IN it. A repo
            # whose only number is a negative delta against a busier
            # fortnight ago is not activity; it was filling the screen with
            # rows whose current column read 0.
            rows = [r for r in rows
                    if (r['clones']['cur'] or r['views']['cur']
                        or r['prs']['opened'] or r['prs']['merged'])]
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r['repo'].lower()]
        label, key, natural = self.DELTAS_SORT_KEYS[self.deltas_sort]
        return sorted(rows, key=key, reverse=self._sort_desc(natural))

    def _render_deltas_view(self, avail, max_x):
        curses = self.curses
        rows = self._delta_rows()
        win = TF_LABEL.get(self.timeframe, 'window')
        n = len(self.data.window_days(self.timeframe)) or 1
        self._put(2, 1, f'Deltas — windowed change and rate  ({win})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        comparable = rows[0]['comparable'] if rows else False
        note = (f'\u0394 = this {n}d window minus the previous {n}d, from the daily store.'
                if comparable else
                f'No previous {n}d window in the store yet — showing totals and rates only.')
        self._put(3, 1, note + '  [z] toggles unchanged rows.', self.curses.A_DIM)
        if not rows:
            self._put(5, 3, 'Nothing moved in this window.', self.curses.A_DIM)
            return

        name_w = min(24, max(12, max_x - 68))
        spark_w = max(6, min(14, n))
        self._put(5, 3,
                  f'  {"repo":<{name_w}} {"clones":<{spark_w}} {"cur":>6} {DELTA:>7} '
                  f'{"/day":>6}  {"views":>6} {DELTA:>7}  {"PR+":>4}{"PRm":>4}',
                  curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 6
        cur = self.cursor('deltas').clamp(len(rows), page)
        self.scroll = cur.scroll
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            c, v = row['clones'], row['views']
            spark = _sparkline(c['series'][-spark_w:])
            cd = c['delta']
            vd = v['delta']
            cd_s = f'{cd:>+7d}' if cd is not None else '    n/a'
            vd_s = f'{vd:>+7d}' if vd is not None else '    n/a'
            selected = idx == cur.index
            marker = '>' if selected else ' '
            base = 0
            self._put(7 + i, 3, f'{marker} {row["repo"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else 0)
            self._put(7 + i, 3 + 2 + name_w,
                      f' {spark:<{spark_w}} {_compact_num(c["cur"]):>6} ', 0)
            x = 3 + 2 + name_w + 1 + spark_w + 1 + 6 + 1
            self._put(7 + i, x, cd_s,
                      base if idx == cur.index else self._delta_color(cd))
            self._put(7 + i, x + 8, f'{c["rate"]:>6.1f}', base or self.curses.A_DIM)
            self._put(7 + i, x + 16, f'{_compact_num(v["cur"]):>6}', base)
            self._put(7 + i, x + 23, vd_s,
                      base if idx == cur.index else self._delta_color(vd))
            po, pm = row['prs']['opened'], row['prs']['merged']
            self._put(7 + i, x + 31,
                      f'{po:>4}{pm:>4}',
                      base or (curses.color_pair(1) if pm else self.curses.A_DIM))
            self._mark_selected(7 + i, max_x, selected)
        self._scrollbar(7, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)

    def _delta_color(self, value):
        curses = self.curses
        if value is None:
            return self.curses.A_DIM
        if value > 0:
            return curses.color_pair(1)
        if value < 0:
            return curses.color_pair(4)
        return self.curses.A_DIM

    # 'material' is the default and the useful one. On a personal account
    # most repos sit at zero for a fortnight, so a single clone scores an
    # enormous |Z| and every one of them earns a row of one faint mark —
    # twenty rows of noise around the six that matter.
    ANOMALY_SEVERITIES = ('material', 'all', 'extreme', 'significant', 'minor')
    SEVERITY_PAIR = {'extreme': 4, 'significant': 2, 'minor': 3}

    # Severity leads, because ranking by breadth alone put a seven-repo
    # wave of |Z|~11 above the four-repo release day that peaked at 81.6.
    # Both orderings answer a real question, so both are a keypress away.
    EVENT_SORT_KEYS = (
        ('severity', lambda e: e['peak'], True),
        ('repos', lambda e: len(e['repos']), True),
        ('date', lambda e: e['day'], True),
    )

    def _anomaly_grid(self):
        return self.data.anomalies(self.timeframe)

    def _anomaly_events(self):
        """[(day, repos)] — biggest first, all of them.

        These were three fixed lines under the title, ranked by recency,
        which silently dropped the largest event in the window. An account
        event is the unit the operator actually acts on ("what did I ship
        on the 30th, and what did it move"), so it gets a list of its own.
        """
        grid = self._anomaly_grid()
        days = grid['days']
        out = []
        for day, repos in grid['campaigns'].items():
            i = days.index(day) if day in days else -1
            peak = 0.0
            for repo, cells in grid['rows']:
                if repo not in repos or i < 0 or i >= len(cells):
                    continue
                cell = cells[i]
                if cell and cell['material']:
                    peak = max(peak, abs(cell['z']))
            out.append({'day': day, 'repos': repos, 'peak': peak,
                        'severity': derive.severity(peak) or 'minor'})
        name, key, natural = self.EVENT_SORT_KEYS[self.event_sort]
        desc = natural != self.sort_flip.get('anomaly_events', False)
        # Ties broken by the other two axes, so the order is total and the
        # list does not reshuffle under the cursor between renders.
        return sorted(out, key=lambda e: (key(e), len(e['repos']), e['day']),
                      reverse=desc)

    def _event_detail(self, day):
        """Everything known about one account event: which repos moved, by
        how much, and what shipped that day."""
        grid = self._anomaly_grid()
        if day not in grid['days']:
            return None
        i = grid['days'].index(day)
        members = []
        for repo, cells in grid['rows']:
            cell = cells[i] if i < len(cells) else None
            if not cell or not cell['material']:
                continue
            events = self.data.events(repo, [day])
            members.append({
                'repo': repo, 'cell': cell,
                'releases': [tag for _d, tag in events['releases']],
                'pushes': events['pushes'].get(day, 0),
            })
        members.sort(key=lambda m: -abs(m['cell']['z']))
        return {'day': day, 'members': members,
                'campaign': set(grid['campaigns'].get(day, ()))}

    def _anomaly_rows(self, with_peaks=False):
        """(repo, cells) rows, hottest first, after the severity filter.

        Ordering by peak |z| is what makes this readable at all: the table
        this replaced interleaved every repo's every anomalous day, so
        finding the one repo that mattered meant reading several hundred
        rows in no particular order.
        """
        grid = self._anomaly_grid()
        want = self.ANOMALY_SEVERITIES[self.anomaly_filter]
        rows = []
        for repo, cells in grid['rows']:
            if not self.in_scope(repo):
                continue
            kept = cells
            if want not in ('all', 'material'):
                kept = [c if (c and derive.severity(c['z']) == want) else None
                        for c in cells]
            material = [c for c in kept if c and c['material']]
            if want == 'material':
                if not material:
                    continue
                kept = [c if (c and c['material']) else None for c in kept]
            peak = max((abs(c['z']) for c in kept if c), default=0.0)
            if want not in ('all', 'material') and not peak:
                continue
            worst = max((c for c in kept if c), key=lambda c: abs(c['z']), default=None)
            rows.append((repo, kept, peak, bool(material), worst))
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r[0].lower()]
        rows.sort(key=lambda r: (-r[3], -r[2]))
        if with_peaks:
            return [(repo, cells, peak, worst) for repo, cells, peak, _m, worst in rows]
        return [(repo, cells) for repo, cells, _p, _m, _w in rows]

    def _render_anomaly_events(self, y, avail, max_x):
        """The account-event pane: its own cursor, its own scroll, [space]
        into a detail view. Ranked by how many repos moved together."""
        curses = self.curses
        events = self._anomaly_events()
        focused = self.anomaly_pane == 0
        if not events:
            self._put(y, 3, 'No day where three or more repos moved together.',
                      self.curses.A_DIM)
            return y + 2
        # Focus grows the pane but never past a third of the screen: the
        # grid is the point of this view, and an events list that pushes it
        # to "1-21/27" has answered a smaller question by hiding a bigger
        # one.
        cap = max(3, (avail - 6) // 3) if focused else 4
        rows_shown = min(len(events), cap)
        cur = self.cursors['anomaly_events'].clamp(len(events), rows_shown)
        name, _k, natural = self.EVENT_SORT_KEYS[self.event_sort]
        arrow = self.sort_arrow(natural != self.sort_flip.get('anomaly_events', False))
        head = f'Account events ({len(events)})  [sort: {name} {arrow}]'
        self._put(y, 1, head, curses.color_pair(3) | self.curses.A_BOLD)
        if focused:
            self._put(y, len(head) + 3, '[s]ort  [space] opens  [tab] to the grid',
                      curses.color_pair(5))
        else:
            self._put(y, len(head) + 3, '[tab] to focus', self.curses.A_DIM)
        y += 1
        for i, ev in enumerate(events[cur.scroll:cur.scroll + rows_shown]):
            if y >= avail:
                break
            day, repos = ev['day'], ev['repos']
            idx = cur.scroll + i
            selected = focused and idx == cur.index
            marker = '>' if selected else ' '
            more = ELLIPSIS if len(repos) > 5 else ''
            # A block in the severity of the worst repo in the event, so the
            # list can be read for "which of these mattered" without opening
            # any of them.
            self._put(y, 1, BLOCK,
                      curses.color_pair(self.SEVERITY_PAIR[ev['severity']]))
            self._put(y, 3, f'{marker} {_short_date(day)} {UP_MARK} {len(repos):>2} repos'
                      f' {ev["peak"]:>6.1f}',
                      curses.color_pair(5) if selected
                      else curses.color_pair(3) | self.curses.A_BOLD)
            self._put(y, 30, f'{EM_DASH} {", ".join(repos[:5])}{more}'[: max_x - 32],
                      self.curses.A_DIM if not selected else 0)
            if selected:
                self._mark_selected(y, max_x, True)
            y += 1
        if len(events) > rows_shown:
            self._scrollbar(y - rows_shown, rows_shown, len(events), cur.scroll, max_x)
            self._put(y, 3, f'{len(events) - rows_shown} more '
                      f'{"(tab, then j/k)" if not focused else "(j/k)"}',
                      self.curses.A_DIM)
            y += 1
        return y + 2

    def _render_event_detail(self, avail, max_x):
        """One account event, in full: which repos moved, by how much, and
        what shipped that day.

        The question a spike raises is never "was there a spike" — it is
        "what did I do on the 30th". This is the screen that answers it.
        """
        curses = self.curses
        detail = self._event_detail(self.drilldown_event)
        day = self.drilldown_event
        self._put(2, 1, f' account event {_short_date(day)} ',
                  curses.color_pair(5) | self.curses.A_BOLD)
        self._put(2, max_x - 30, '[space/esc] back  [j/k] next',
                  self.curses.A_DIM)
        x = 1
        for name, pair in (('extreme', 4), ('significant', 2), ('minor', 3)):
            self._put(4, x, BLOCK, curses.color_pair(pair))
            self._put(4, x + 2, name, self.curses.A_DIM)
            x += len(name) + 5
        if not detail or not detail['members']:
            self._put(4, 3, 'Nothing material on this day in the selected window.',
                      self.curses.A_DIM)
            return
        members = detail['members']
        causes = [m for m in members if m['releases'] or m['pushes']]
        has_events = bool((self.data.history or {}).get('events'))
        cause_line = (f'{len(causes)} of them shipped something that day.'
                      if has_events else
                      'Release and push data was never collected for this store, '
                      'so the cause column is blank rather than empty.')
        self._put(3, 1, f'{len(members)} repo(s) departed from their own normal; '
                  f'{cause_line}', self.curses.A_DIM)

        name_w = min(28, max(12, max_x - 62))
        self._put(6, 3, f'  {"repo":<{name_w}} {"metric":>7} {"dir":>6} {"Z":>8} '
                  f'{"value":>8} {"median":>8}  cause',
                  curses.color_pair(2) | self.curses.A_BOLD)
        for i, m in enumerate(members[: avail - 8]):
            cell = m['cell']
            sev = derive.severity(cell['z']) or 'minor'
            color = curses.color_pair(self.SEVERITY_PAIR.get(sev, 3))
            self._put(8 + i, 1, BLOCK, color)
            cause = ''
            if m['releases']:
                cause = 'release ' + ', '.join(m['releases'])
            elif m['pushes']:
                cause = f'{m["pushes"]} commit(s)'
            self._put(8 + i, 3,
                      f'  {m["repo"][:name_w]:<{name_w}} {cell["metric"]:>7} '
                      f'{cell["dir"]:>6} {cell["z"]:>8.1f} {cell["value"]:>8} '
                      f'{cell["median"]:>8.0f}  {cause}'[: max_x - 4], color)
        y = 8 + min(len(members), avail - 8) + 1
        if y < avail:
            total = sum(m['cell']['value'] for m in members)
            if causes:
                tail = ''
            elif has_events:
                tail = ('  No release or push that day \u2014 this wave came from '
                        'somewhere else.')
            else:
                tail = '  Run  catnip run  to start recording causes.'
            self._put(y, 3, f'{total} events across {len(members)} repos on this day.'
                      + tail, self.curses.A_DIM)

    def _render_anomaly_view(self, avail, max_x):
        curses = self.curses
        grid = self._anomaly_grid()
        rows = self._anomaly_rows(with_peaks=True)
        days = grid['days']
        campaigns = grid['campaigns']
        sev = self.ANOMALY_SEVERITIES[self.anomaly_filter]
        win = TF_LABEL.get(self.timeframe, 'window')
        hidden = len(grid['rows']) - len(rows)
        self._put(2, 1, f'Anomalies \u2014 repo x day  ({win})  [filter: {sev}]',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Cell = the day this repo departed from its own normal. '
                  'C/V spike, c/v dip \u2014 clones or views. [?] for the formula.',
                  self.curses.A_DIM)
        x = 1
        for name, pair in (('extreme', 4), ('significant', 2), ('minor', 3)):
            self._put(4, x, BLOCK, curses.color_pair(pair))
            self._put(4, x + 2, name, self.curses.A_DIM)
            x += len(name) + 5
        # The immaterial glyph needs naming wherever it can appear. It is
        # not a severity — it is a day that is statistically extreme for a
        # repo whose baseline is zero, and moving by one clone is not an
        # event however large the z-score says it is.
        gloss = (f'moved < {derive.Z_MIN_VALUE} from its median '
                 f'(shown, never counted)')
        self._put(4, x, ramp_glyph(0.3), self.curses.A_DIM)
        self._put(4, x + 2, gloss, self.curses.A_DIM)
        # Advance past the text that was actually written, not past a
        # guess at its width: the guess put the hidden-repos note on top of
        # the tail of this one ("...never co56 repo(s) with nothing...").
        x += 2 + len(gloss) + 4
        if hidden:
            note = (f'{hidden} repo(s) with nothing material hidden '
                    f'\u2014 [f] to show')
            if x + len(note) < max_x - 1:
                self._put(4, x, note, self.curses.A_DIM)
            else:
                self._put(5, 3, note, self.curses.A_DIM)

        if not days:
            self._put(6, 3, 'No daily store yet \u2014 run  catnip history  first.',
                      self.curses.A_DIM)
            return
        if not rows:
            self._put(6, 3, f'No {sev} anomalies in this window.', self.curses.A_DIM)
            return

        campaign_days = {day: set(repos) for day, repos in campaigns.items()}
        # Blank rows between blocks. Legend, event heading, event rows, grid
        # heading and grid rows were five kinds of line stacked with no
        # separation, which reads as one wall rather than three tables.
        y = self._render_anomaly_events(6, avail, max_x)

        # Geometry. One character per day put fourteen columns in fourteen
        # columns, which left room for two date labels out of fourteen and
        # no way to tell which day a mark belonged to. Give each day as much
        # width as the terminal allows, up to a full date, so the header
        # sits over its own column.
        name_w = min(22, max(10, max_x // 5))
        peak_w = 13
        grid_room = max_x - (3 + name_w + 1) - peak_w - 2
        # A column is legible when it is as wide as its date. If every day
        # fits at that width, show them all; otherwise show a window of
        # them and scroll horizontally, rather than shrinking the columns
        # until the labels have to be dropped.
        LEGIBLE_W = 5
        max_visible = max(1, grid_room // (LEGIBLE_W + 1))
        if len(days) <= max_visible:
            cell_w = max(1, min(LEGIBLE_W, grid_room // max(1, len(days)) - 1))
            gap = 1 if cell_w * len(days) + len(days) <= grid_room else 0
            day_offset, visible_days = 0, days
        else:
            cell_w, gap = LEGIBLE_W, 1
            last = len(days) - max_visible
            day_offset = (last if self.anomaly_day_offset is None
                          else max(0, min(self.anomaly_day_offset, last)))
            self.anomaly_day_offset = day_offset
            visible_days = days[day_offset:day_offset + max_visible]
        # The key handler has no terminal to measure, so record how wide the
        # window turned out to be; without it, h/l clamp against the wrong
        # bound and the grid appears not to scroll at all.
        self._anomaly_visible = len(visible_days)

        page = max(1, avail + 2 - y - 2)
        cur = self.cursor('anomaly').clamp(len(rows), page)
        self.scroll = cur.scroll
        severity_pair = self.SEVERITY_PAIR

        def cell_at(r, c):
            cells = rows[r][1]
            i = c + day_offset
            return cells[i] if i < len(cells) else None

        def glyph_for(value, r, c):
            cell = cell_at(r, c)
            if not cell:
                return ' '
            letter = 'c' if cell['metric'] == 'clones' else 'v'
            if cell['dir'] == 'spike':
                letter = letter.upper()
            return letter if cell['material'] else ramp_glyph(0.3)

        def attr_for(value, r, c):
            cell = cell_at(r, c)
            if not cell:
                return 0
            if not cell['material']:
                return self.curses.A_DIM
            i = c + day_offset
            day = days[i] if i < len(days) else ''
            if rows[r][0] in campaign_days.get(day, ()):  # folded into an event
                return self.curses.A_DIM
            pair = severity_pair.get(derive.severity(cell['z']) or '', 0)
            return curses.color_pair(pair) if pair else 0

        labels = [_short_date(d) for d in visible_days]
        grid_rows = [(repo, [1.0 if cell else 0.0
                             for cell in cells[day_offset:day_offset + len(visible_days)]])
                     for repo, cells, _p, _w in rows]
        end = heatmap(
            self._put, self.curses, y, max_x - peak_w,
            rows=grid_rows, col_labels=labels, label_w=name_w,
            cell_w=cell_w, gap=gap, glyph_for=glyph_for, attr_for=attr_for,
            header_attr=curses.color_pair(2), label_attr=0,
            scroll=cur.scroll, height=page, header_gap=1,
            cursor=cur.index, cursor_attr=curses.color_pair(5))
        self._scrollbar(y + 2, page, len(rows), cur.scroll, max_x)

        # A per-row summary, so a row still answers "how bad, and when"
        # even when the terminal is too narrow for legible columns. It sits
        # just past the last day rather than pinned to the right edge —
        # thirty columns of gap between a mark and its magnitude makes the
        # eye do work the layout should have done.
        grid_end = 3 + name_w + 1 + len(visible_days) * (cell_w + gap)
        px = min(max_x - peak_w, grid_end + 2)
        self._put(y, px, 'peak |Z| day', curses.color_pair(2) | self.curses.A_BOLD)
        for i, (_repo, _cells, peak, worst) in enumerate(rows[cur.scroll:cur.scroll + page]):
            if not worst:
                continue
            pair = severity_pair.get(derive.severity(worst['z']) or '', 0)
            attr = (curses.color_pair(5) if cur.scroll + i == cur.index
                    else (curses.color_pair(pair) if pair else self.curses.A_DIM))
            day = days[_cells.index(worst)] if worst in _cells else ''
            self._put(y + 2 + i, px, f'{peak:>6.1f} {_short_date(day):>5}', attr)
        self.scroll_indicator(2, max_x, len(rows), page)
        if end <= avail + 1:
            hint = ('[space] opens the selected repo; per-day detail lives '
                    'there, not here.')
            if len(visible_days) < len(days):
                first, last_d = visible_days[0], visible_days[-1]
                hint = (f'days {_short_date(first)}..{_short_date(last_d)} of '
                        f'{len(days)}  [h/l or \u2190/\u2192 to scroll]   ' + hint)
            self._put(end, 3, hint[: max_x - 5], self.curses.A_DIM)

    PROFILE_SORT_KEYS = (
        ('score', lambda r: r['score'], True),
        ('cloners', lambda r: r['uniq_cloners'], True),
        ('name', lambda r: r['repo'].lower(), False),
    )

    INTENT_COLORS = {'developer': 1, 'tooling': 3, 'reference': 6, 'low-signal': 0}

    def _profile_rows(self):
        label, key, natural = self.PROFILE_SORT_KEYS[self.profile_sort]
        desc = self._sort_desc(natural)
        rows = [r for r in self.data.intent(self.timeframe).values()
                if self.in_scope(r['repo'])]
        if self.profile_hide_low:
            rows = [r for r in rows if r['label'] != 'low-signal']
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r['repo'].lower()]
        # Ranked repos first, low-signal beneath: a repo with nine data
        # points has not earned a place in the ordering, but hiding it
        # outright invites the question of where it went.
        rows.sort(key=key, reverse=desc)
        return ([r for r in rows if r['label'] != 'low-signal']
                + [r for r in rows if r['label'] == 'low-signal'])

    def _render_profile_view(self, avail, max_x):
        curses = self.curses
        name, _key, natural = self.PROFILE_SORT_KEYS[self.profile_sort]
        sort_label = f'{name} {self.sort_arrow(self._sort_desc(natural))}'
        rows = self._profile_rows()
        win = TF_LABEL.get(self.timeframe, 'window')
        low_tag = 'low hidden' if self.profile_hide_low else 'l=hide low'
        self._put(2, 1, f'Clone Intent  ({win})  [sort: {sort_label}]  [{low_tag}]',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'score = (uniq_cloners + 1) / (uniq_visitors + 2) \u2014 smoothed '
                  'clones per visitor, on UNIQUES. [?] for thresholds.',
                  self.curses.A_DIM)
        if not rows:
            self._put(5, 3, 'No repo has traffic in this window.', self.curses.A_DIM)
            return

        name_w = min(24, max(12, max_x - 58))
        bar_w = max(8, max_x - name_w - 48)
        self._put(5, 3, f'  {"repo":<{name_w}} {"score":>7} {"intent":<11} '
                  f'{"uc":>5}{"uv":>5}  bar',
                  curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 6
        cur = self.cursor('profile').clamp(len(rows), page)
        self.scroll = cur.scroll
        # Scale bars to the ranked repos only; one fetcher fleet at 23.0
        # would otherwise flatten every real repo to a single cell.
        ranked = [r['score'] for r in rows if r['label'] != 'low-signal']
        top = max(ranked[:1] or [1.0], default=1.0)
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            pair = self.INTENT_COLORS.get(row['label'], 0)
            color = curses.color_pair(pair) if pair else self.curses.A_DIM
            selected = idx == cur.index
            marker = '>' if selected else ' '
            bar = self._hbar(min(row['score'], top), top, bar_w)
            self._put(7 + i, 3, f'{marker} {row["repo"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else color)
            self._put(7 + i, 3 + 2 + name_w,
                      f' {row["score"]:>7.3f} '
                      f'{row["label"]:<11} {row["uniq_cloners"]:>5}'
                      f'{row["uniq_visitors"]:>5}  {bar}', color)
            if row['conflict']:
                self._put(7 + i, max_x - 13, 'CONFLICT', curses.color_pair(4))
            self._mark_selected(7 + i, max_x, selected)
        self._scrollbar(7, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)
        conflicts = sum(1 for r in rows if r['conflict'])
        if conflicts and 8 + min(page, len(rows)) < avail + 2:
            self._put(8 + min(page, len(rows)), 3,
                      f'{conflicts} repo(s) score developer but classify as crawler '
                      f'in the audience view \u2014 same clones, two readings.',
                      curses.color_pair(4))

    @staticmethod
    def _corr_tier(r):
        """0 strong (|r|>=0.8), 1 moderate (0.5-0.8), 2 weak (<0.5)."""
        a = abs(r)
        if a >= 0.8:
            return 0
        if a >= 0.5:
            return 1
        return 2

    def _render_finding_detail(self, avail, max_x):
        """One attribution row, deep and broad.

        The row says a tier; this says why, and what else was true at the
        time. The tier is a judgement made from thin evidence, and the
        operator should be able to overturn it here — which means showing
        the statistics it rests on, not just restating it larger.
        """
        curses = self.curses
        row = self.drilldown_finding
        tf = self.timeframe
        ctx = derive.finding_context(self.data.history, tf, row)
        last, y = avail + 1, 2
        pair = self.ATTRIBUTION_TIER_COLOR.get(row['tier'], 0)
        tier_attr = curses.color_pair(pair) if pair else self.curses.A_DIM

        self._put(y, 1, f' {row["repo"]} \u2014 {_short_date(row["day"])} ',
                  curses.color_pair(5) | curses.A_BOLD)
        self._put(y, len(row['repo']) + len(row['day']) + 8, row['tier'], tier_attr)
        self._put(y, max_x - 36, '[space/esc] back  [j/k] next finding',
                  self.curses.A_DIM)
        y += 1

        cause = (f'{row["cause"]} {row["detail"]}'.strip() if row['cause']
                 else (row['detail'] or 'no cause recorded in the store'))
        lag = f'  (+{row["lag"]}d after the cause)' if row.get('lag') else ''
        self._put(y, 1, f'cause: {cause}{lag}'[: max_x - 3], curses.color_pair(3))
        y += 2

        # The effect, with the day marked. `peak` puts the marker on the
        # bar itself rather than near it.
        values, days = ctx['values'], ctx['days']
        chart = [{'label': _short_date(d), 'count': v}
                 for d, v in zip(days, values, strict=False)]
        if ctx['index'] is not None and ctx['index'] < len(chart):
            chart[ctx['index']]['peak'] = True
        plot_h = max(3, min(6, (last - 22)))
        if plot_h >= 3 and y + plot_h + 4 < last:
            y = bar_chart(
                self._put, curses, y, chart, plot_h, max_x,
                title=f'daily {ctx["metric"]} \u2014 \u25b2 marks {row["day"]}',
                title_attr=curses.color_pair(6) | curses.A_BOLD,
                axis_attr=curses.color_pair(6), color=curses.color_pair(1),
                peak_attr=curses.color_pair(4) | curses.A_BOLD,
                max_bar_w=6, value_labels=True, label_fit=True,
                clip_ratio=6) + 1

        # Why the tier. Measured numbers, named as such.
        if y < last:
            self._put(y, 1, 'why this tier', curses.color_pair(6) | curses.A_BOLD)
            y += 1
        if y < last and row['tier'] not in ('no-effect',):
            moved = abs(row['value'] - (row['median'] or 0))
            self._put(y, 3, f'median {ctx["median"]:.0f}   MAD {ctx["mad"]:.1f}   '
                      f'meanAD {ctx["mean_ad"]:.2f}   z {row["z"]:+.1f}   '
                      f'moved {moved:.0f} vs floor {ctx["material_floor"]}'
                      f'  \u2192 {"material" if moved >= ctx["material_floor"] else "immaterial"}',
                      0)
            y += 1
        if y < last and ctx['mad'] == 0:
            self._put(y, 3, 'MAD is 0 (a mostly-flat series), so z uses the meanAD '
                      'fallback \u2014 see [?].', self.curses.A_DIM)
            y += 1

        if row['tier'] == 'coupled' and ctx['partner'] and y < last:
            p = ctx['partner']
            removed = abs(p['raw_r']) - abs(p['r'])
            self._put(y, 3, f'borrowed from {p["repo"]}: residual r {p["r"]:+.3f}, '
                      f'raw {p["raw_r"]:+.3f} (wave removed {removed:+.3f})',
                      curses.color_pair(2))
            y += 1
            if y < last:
                self._put(y, 3, f'{p["repo"][:20]:<20} {_sparkline(p["values"])}',
                          curses.color_pair(2))
                y += 1
            if y < last:
                self._put(y, 3, f'{row["repo"][:20]:<20} {_sparkline(values)}'
                          f'   \u2190 inferred, not observed on this repo',
                          self.curses.A_DIM)
                y += 1
        if row['tier'] == 'no-effect' and y < last:
            hist = [h for h in ctx['release_history'] if h['day'] != row['day']]
            if hist:
                usual = derive.median([h['after'] for h in hist])
                self._put(y, 3, f'this repo\'s {len(hist)} earlier release(s) drew a '
                          f'median of {usual:.0f} {ctx["metric"]} in {derive.ATTRIBUTION_LAG + 1} '
                          f'days \u2014 so this one is {"typical" if usual < 3 else "below par"}',
                          self.curses.A_DIM)
            else:
                self._put(y, 3, 'no earlier release in the store to compare against',
                          self.curses.A_DIM)
            y += 1
        y += 1

        # Context: what else was true that day, and what this repo is.
        if y < last:
            self._put(y, 1, 'context', curses.color_pair(6) | curses.A_BOLD)
            y += 1
        if y < last:
            if ctx['campaign']:
                self._put(y, 3, f'account event: {len(ctx["campaign"])} repos moved '
                          f'together \u2014 {", ".join(ctx["campaign"][:6])}'[: max_x - 5],
                          curses.color_pair(3))
            elif ctx['same_day']:
                self._put(y, 3, f'{len(ctx["same_day"])} other repo(s) also moved: '
                          f'{", ".join(ctx["same_day"][:6])}'[: max_x - 5],
                          self.curses.A_DIM)
            else:
                self._put(y, 3, 'no other repo moved that day', self.curses.A_DIM)
            y += 1
        if y < last:
            a, i = ctx['audience'] or {}, ctx['intent'] or {}
            self._put(y, 3, f'audience {a.get("score", 0):.2f} [{a.get("label", "?")}]   '
                      f'intent {i.get("score", 0):.2f} [{i.get("label", "?")}]'
                      + ('   CONFLICT' if i.get('conflict') else ''),
                      curses.color_pair(4) if i.get('conflict') else 0)
            y += 1
        if y < last:
            others = ctx['other_findings']
            if others:
                txt = ', '.join(f'{_short_date(o["day"])} {o["metric"][:1]}'
                                f'{o["z"]:+.0f}' for o in others[:8])
                self._put(y, 3, f'this repo\'s other findings in the window: {txt}'
                          [: max_x - 5], self.curses.A_DIM)
            else:
                self._put(y, 3, 'this repo\'s only finding in the window '
                          '\u2014 a one-off, not a pattern', self.curses.A_DIM)
            y += 1
        if y < last:
            pages = [r for r in self.data.funnel_data
                     if r.get('repo_name') == row['repo']]
            if pages:
                top = max(pages, key=lambda r: _int(r, 'view_count'))
                self._put(y, 3, f'busiest page (rolling 14d): '
                          f'{_int(top, "view_count")}v/{_int(top, "unique_visitors")}u  '
                          f'{(top.get("title") or top.get("path") or "")}'[: max_x - 5],
                          self.curses.A_DIM)
            else:
                self._put(y, 3, 'no path data for this repo in the newest run',
                          self.curses.A_DIM)

    def _render_momentum_detail(self, avail, max_x):
        """One repo's slope: level, day-over-day change, and direction.

        The deltas row says a repo moved by N. It cannot say whether the
        move is still happening, so a repo that spiked once and stopped is
        indistinguishable from one climbing steadily. The first difference
        and the fitted slope are what separate them.
        """
        curses = self.curses
        repo = self.drilldown_momentum
        tf = self.timeframe
        m = derive.momentum(self.data.history, repo, tf)
        days, last = m['days'], avail + 1
        win = TF_LABEL.get(tf, 'window')

        self._put(2, 1, f' {repo} \u2014 momentum ', curses.color_pair(5) | curses.A_BOLD)
        self._put(2, max_x - 34, '[space/esc] back  [j/k] next repo', self.curses.A_DIM)
        if not days:
            self._put(4, 3, 'No daily store yet.', self.curses.A_DIM)
            return

        clones = m['clones']
        slope, accel = clones['slope'], clones['accel']
        direction = 'rising' if slope > 0.05 else 'falling' if slope < -0.05 else 'flat'
        shape = ('accelerating' if accel > 0.05 else
                 'decelerating' if accel < -0.05 else 'steady')
        self._put(3, 1, f'clones {direction} {slope:+.2f}/day, {shape} '
                  f'({accel:+.2f}/day\u00b2)   {win}', curses.color_pair(3))
        if clones['prev_rate'] is not None:
            prev, cur = clones['prev_rate'], clones['rate']
            ratio = (f'{cur / prev:.2f}x' if prev else 'from a standing start')
            self._put(4, 1, f'rate {cur:.1f}/day vs {prev:.1f}/day previous window '
                      f'\u2014 {ratio}', self.curses.A_DIM)
        else:
            self._put(4, 1, 'no previous window in the store yet, so no rate '
                      'comparison', self.curses.A_DIM)

        labels = [_short_date(d) for d in days]
        plot_h = max(3, min(6, (last - 16) // 2))
        y = 6
        y = bar_chart(
            self._put, curses, y,
            [{'label': labels[i], 'count': v} for i, v in enumerate(clones['values'])],
            plot_h, max_x, title=f'Daily clones ({clones["total"]})',
            title_attr=curses.color_pair(6) | curses.A_BOLD,
            axis_attr=curses.color_pair(6), color=curses.color_pair(1),
            max_bar_w=6, value_labels=True, label_fit=True, clip_ratio=6) + 1
        if y + plot_h + 4 <= last:
            y = diverging_bars(
                self._put, curses, y,
                [{'label': labels[i], 'value': v}
                 for i, v in enumerate(clones['diffs'])],
                plot_h + 2, max_x,
                title='Day-over-day change (first derivative)',
                title_attr=curses.color_pair(6) | curses.A_BOLD,
                axis_attr=curses.color_pair(6),
                pos_attr=curses.color_pair(1), neg_attr=curses.color_pair(4),
                fmt=lambda v: f'{v:+d}' if v else '0', max_bar_w=6) + 1

        v = m['views']
        if y < last:
            vdir = ('rising' if v['slope'] > 0.05 else
                    'falling' if v['slope'] < -0.05 else 'flat')
            self._put(y, 1, f'views {vdir} {v["slope"]:+.2f}/day  '
                      f'{_sparkline(v["values"])}  {v["total"]} total',
                      curses.color_pair(2))
            y += 1
        if y < last:
            self._put(y, 1, f'clones {_sparkline(clones["values"])}  '
                      f'{clones["total"]} total', curses.color_pair(1))

    def _render_pair_detail(self, avail, max_x):
        """Two coupled repos, side by side, with the residuals the r is
        actually computed on.

        A correlation table gives a number and no way to check it. This
        shows both daily series, both residual series, and the gap between
        raw and residualized r — which is the whole claim the view makes:
        that what survives is not the account-wide release wave.
        """
        curses = self.curses
        a, b = self.drilldown_pair
        tf = self.timeframe
        days = self.data.window_days(tf)
        result = self.data.coupled(tf)
        pair = next((p for p in result['pairs']
                     if {p['a'], p['b']} == {a, b}), None)
        last = avail + 1

        self._put(2, 1, f' {a} <-> {b} ', curses.color_pair(5) | self.curses.A_BOLD)
        self._put(2, max_x - 32, '[space/esc] back  [j/k] next pair', self.curses.A_DIM)
        if not pair or not days:
            self._put(4, 3, 'This pair is not coupled in the current window.',
                      self.curses.A_DIM)
            return
        lag = ('lag available' if result['lag_available'] else
               f'lag suppressed (needs {derive.LAG_MIN_DAYS} days, have {len(days)})')
        self._put(3, 1, f'residual r {pair["r"]:+.3f}   raw r {pair["raw_r"]:+.3f}   '
                  f'n {pair["n"]} days   {lag}', self.curses.A_DIM)

        # What residualizing did. This is the sentence the view exists for.
        removed = abs(pair['raw_r']) - abs(pair['r'])
        if removed > 0.15:
            verdict = ('most of the raw correlation was the account-wide wave; '
                       'what is left is weaker than it looked')
        elif removed < -0.15:
            verdict = ('these move together MORE once the wave is removed — the '
                       'account trend was masking it')
        else:
            verdict = ('the wave explains little of this; they move together on '
                       'their own')
        self._put(4, 1, f'raw {pair["raw_r"]:+.3f} -> residual {pair["r"]:+.3f}: {verdict}'
                  [: max_x - 3], curses.color_pair(3))

        metric = 'clones'
        sa = derive.series(self.data.history, a, metric, days)
        sb = derive.series(self.data.history, b, metric, days)
        plot_h = min(5, max(3, (last - 20) // 2))
        y = 6
        if plot_h >= 3 and y + 2 * (plot_h + 3) < last:
            for name, ser, color in ((a, sa, curses.color_pair(1)),
                                     (b, sb, curses.color_pair(2))):
                y = bar_chart(
                    self._put, curses, y, self._chart_series(days, ser), plot_h, max_x,
                    title=f'{name} — daily {metric} ({sum(ser)})',
                    title_attr=curses.color_pair(6) | curses.A_BOLD,
                    axis_attr=curses.color_pair(6), color=color,
                    max_bar_w=6, value_labels=True, label_fit=True, bin_unit='d',
                    clip_ratio=6) + 1

        # The residual series: what Pearson actually saw.
        account = derive.account_series(self.data.history, metric, days)
        total = sum(account) or 1
        if y < last:
            self._put(y, 1, 'Residuals — daily minus each repo\'s expected share of '
                      'the account total', curses.color_pair(6) | self.curses.A_BOLD)
            y += 1
        spark_w = max(10, min(48, max_x - 40))
        for name, ser in ((a, sa), (b, sb)):
            if y >= last:
                break
            share = sum(ser) / total
            resid = [ser[i] - share * account[i] for i in range(len(days))]
            self._put(y, 3, f'{name[:22]:<22} {_sparkline([r - min(resid) for r in resid][-spark_w:]):<{spark_w}} '
                      f'share {share * 100:>5.1f}% of account {metric}', 0)
            y += 1

        # Side-by-side identity, so the pair is more than two names.
        aud = self.data.audience(tf)
        ins = self.data.intent(tf)
        y += 1
        if y < last:
            self._put(y, 1, 'Side by side', curses.color_pair(6) | self.curses.A_BOLD)
            y += 1
        col = max_x // 2
        for i, name in enumerate((a, b)):
            x = 3 if i == 0 else col
            ra, ri = aud.get(name) or {}, ins.get(name) or {}
            d = self.data.deltas(tf).get(name) or {}
            lines = [
                name[:34],
                f"clones {(d.get('clones') or {}).get('cur', 0)}  "
                f"views {(d.get('views') or {}).get('cur', 0)}",
                f"audience {ra.get('score', 0):.2f} [{ra.get('label', '?')}]",
                f"intent {ri.get('score', 0):.2f} [{ri.get('label', '?')}]",
            ]
            for j, line in enumerate(lines):
                if y + j < last:
                    self._put(y + j, x, line[: col - 4],
                              curses.color_pair(5) | curses.A_BOLD if j == 0 else 0)

    def _render_correlation_view(self, avail, max_x):
        """Residualized coupling, not raw Pearson.

        Raw r over a fortnight of one account is dominated by release days
        when everything moves together; those 0.99 rows were the launch
        wave correlating with itself and implied no action whatsoever.
        """
        curses = self.curses
        result = self.data.coupled(self.timeframe)
        win = TF_LABEL.get(self.timeframe, 'window')
        self._put(2, 1, f'Coupled Repos \u2014 residualized  ({win})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Pearson r AFTER removing each repo\'s expected share of the '
                  'account\'s daily total. [?] explains why.', self.curses.A_DIM)
        if result['reason']:
            self._put(5, 3, result['reason'].capitalize() + '.', self.curses.A_DIM)
            return
        pairs = result['pairs']
        if not pairs:
            self._put(5, 3, 'No pair moves together once the account-wide wave is '
                      'removed.', self.curses.A_DIM)
            return
        lag_note = ('lag available' if result['lag_available'] else
                    f'lag suppressed \u2014 needs {derive.LAG_MIN_DAYS} aligned days, '
                    f'have {len(result["days"])}')
        self._put(4, 3, lag_note, self.curses.A_DIM)

        name_w = min(20, max(10, (max_x - 40) // 2))
        self._put(5, 3, f'  {"repo A":<{name_w}}     {"repo B":<{name_w}} '
                  f'{"resid r":>9} {"raw r":>8} {"n":>4}',
                  curses.color_pair(2) | self.curses.A_BOLD)
        page = avail - 6
        cur = self.cursors['correlation_pairs'].clamp(len(pairs), page)
        self.scroll = cur.scroll
        for i, pair in enumerate(pairs[cur.scroll:cur.scroll + page]):
            r = pair['r']
            tier = self._corr_tier(r)
            if tier == 0:
                c = curses.color_pair(1) if r > 0 else curses.color_pair(4)
            elif tier == 1:
                c = curses.color_pair(6) if r > 0 else curses.color_pair(3)
            else:
                c = self.curses.A_DIM
            selected = cur.scroll + i == cur.index
            marker = '>' if selected else ' '
            self._put(7 + i, 3, f'{marker} {pair["a"][:name_w]:<{name_w}}',
                      curses.color_pair(5) if selected else c)
            self._put(7 + i, 3 + 2 + name_w,
                      f' <-> {pair["b"][:name_w]:<{name_w}} '
                      f'{r:>9.3f} {pair["raw_r"]:>8.3f} {pair["n"]:>4}', c)
            self._mark_selected(7 + i, max_x, selected)
        self._scrollbar(7, page, len(pairs), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(pairs), page)

    # Category -> a label that fits a narrow column and still reads. The
    # heatmap truncated the real names to four characters ('traf', 'dir_',
    # 'pr_l', 'pr_d'), which is not a header, it is a puzzle.
    FUNNEL_LABELS = {
        'overview': 'home', 'doc_blob': 'docs', 'code_blob': 'code',
        'src_blob': 'src', 'dir_tree': 'tree', 'pr_detail': 'pr', 'pr_list': 'prs',
        'issues': 'issu', 'discussions': 'disc', 'releases': 'rels',
        'actions': 'acts', 'pulse': 'puls', 'traffic_graph': 'graf',
        'forks': 'fork', 'other': 'othr',
    }

    # category -> color pair (grouped by kind of content)
    FUNNEL_COLORS = {
        'code_blob': 1, 'src_blob': 1,           # green  = source code
        'doc_blob': 3,                           # yellow = docs
        'pr_detail': 6, 'pr_list': 6,            # blue   = pull requests
        'dir_tree': 2,                           # cyan   = browsing tree
        'issues': 7, 'discussions': 7,           # magenta = issues/discussion
        'traffic_graph': 4, 'actions': 4,        # red    = insights/CI
        'pulse': 4, 'releases': 4,
    }

    FUNNEL_SORT_KEYS = (
        ('depth', lambda r: (r['depth_ratio'], r['total']), True),
        ('views', lambda r: r['total'], True),
        ('uniq', lambda r: (r['uniq'], r['total']), True),
        ('name', lambda r: r['repo'].lower(), False),
    )

    PAGES_SORT_KEYS = (
        ('views', lambda r: _int(r, 'view_count'), True),
        ('uniq', lambda r: _int(r, 'unique_visitors'), True),
        ('category', lambda r: (r.get('category', ''), -_int(r, 'view_count')), False),
        ('page', lambda r: (r.get('title') or r.get('path') or '').lower(), False),
    )

    def _funnel_page_rows(self, repo):
        """The selected repo's pages, under the pages pane's own sort."""
        rows = [r for r in self.data.funnel_data if r.get('repo_name') == repo]
        name, key, natural = self.PAGES_SORT_KEYS[self.pages_sort]
        desc = natural != self.sort_flip.get('funnel_pages', False)
        return sorted(rows, key=key, reverse=desc)

    def _funnel_repo_rows(self):
        """Per-repo funnel rows, deepest first, after the category filter."""
        rows = [r for r in self.data.funnel_depth().values()
                if self.in_scope(r['repo'])]
        cats = self._funnel_categories()
        if 0 < self.funnel_filter <= len(cats):
            target = cats[self.funnel_filter - 1]
            rows = [r for r in rows if r['categories'].get(target)]
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r['repo'].lower()]
        name, key, natural = self.FUNNEL_SORT_KEYS[self.funnel_sort]
        desc = natural != self.sort_flip.get('funnel', False)
        return sorted(rows, key=key, reverse=desc)

    def _render_funnel_pages(self, y, avail, max_x, repo):
        """The selected repo's actual pages, biggest first.

        The grid says what KIND of page was read; it can never say which
        one. "docs outdrew the landing page 2:1" is the finding, but the
        thing you act on is which document.

        Its own cursor, sort and scrollbar: this table answers a different
        question from the grid above it, so sharing their controls made
        one of them wrong whichever way it was pointed.
        """
        curses = self.curses
        if y >= avail or not repo:
            return y
        focused = self.funnel_pane == 1
        rows = self._funnel_page_rows(repo)
        name, _k, natural = self.PAGES_SORT_KEYS[self.pages_sort]
        arrow = self.sort_arrow(natural != self.sort_flip.get('funnel_pages', False))
        head = f'Top pages — {repo}  [sort: {name} {arrow}]'
        self._put(y, 1, head, curses.color_pair(6) | self.curses.A_BOLD)
        self._put(y, len(head) + 3,
                  '[s]ort  [j/k] scroll' if focused else '[tab] to focus',
                  curses.color_pair(5) if focused else self.curses.A_DIM)
        y += 1
        if not rows:
            self._put(y, 3, 'no page data for this repo in the newest run',
                      self.curses.A_DIM)
            return y + 1
        bar_w = 12
        path_w = max(20, max_x - bar_w - 46)
        self._put(y, 3, f'  {"category":<14} {"views":>6} {"uniq":>5}  {"":<{bar_w}} page',
                  curses.color_pair(2) | self.curses.A_BOLD)
        y += 1
        # Whatever rows are left between here and the footer, and not one
        # more: the panel used to draw until it ran out of data.
        page = max(1, avail - y + 1)
        cur = self.cursors['funnel_pages'].clamp(len(rows), page)
        peak = max((_int(r, 'view_count') for r in rows), default=1) or 1
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            cat = row.get('category', '')
            views = _int(row, 'view_count')
            uniq = _int(row, 'unique_visitors')
            label = (row.get('title') or row.get('path') or '')[:path_w]
            pair = self.FUNNEL_COLORS.get(cat, 0)
            color = curses.color_pair(pair) if pair else self.curses.A_DIM
            selected = focused and cur.scroll + i == cur.index
            # Caret and right-edge marker, never a full-row inverse: the
            # row ends in a bar and a path, and reverse video across them
            # buries both. Same idiom as every other list in the app.
            self._put(y + i, 3,
                      f'{">" if selected else " "} '
                      f'{self.FUNNEL_LABELS.get(cat, cat)[:14]:<14}',
                      curses.color_pair(5) if selected else color)
            self._put(y + i, 3 + 2 + 14,
                      f' {views:>6} {uniq:>5}  '
                      f'{self._hbar(views, peak, bar_w):<{bar_w}} {label}', color)
            self._mark_selected(y + i, max_x, selected)
        self._scrollbar(y, page, len(rows), cur.scroll, max_x)
        if len(rows) > page:
            self.scroll_indicator(y - 2, max_x, len(rows), page)
        return y + min(page, len(rows))

    def _render_funnel_view(self, avail, max_x):
        """Content mix as a row-normalized heatmap, plus a depth leaderboard.

        The previous view mixed 98 repos into one set of category totals,
        so a repo whose documentation outdraws its README 2:1 was invisible
        inside the account-wide aggregate.
        """
        curses = self.curses
        cats = self._funnel_categories()
        filt = ('all' if not 0 < self.funnel_filter <= len(cats)
                else cats[self.funnel_filter - 1])
        rows = self._funnel_repo_rows()
        gname, _gk, gnat = self.FUNNEL_SORT_KEYS[self.funnel_sort]
        garrow = self.sort_arrow(gnat != self.sort_flip.get('funnel', False))
        self._put(2, 1, f'Funnel \u2014 content mix and depth  [f]ilter: {filt}  '
                  f'[sort: {gname} {garrow}]'
                  + ('' if self.funnel_pane else '   [tab] to top pages'),
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Row = one repo, shaded by share of ITS OWN busiest category. '
                  'depth = (docs+code+tree)/home; * = no home views. uniq sums per-page '
                  'uniques, so it over-counts ([?]). Rolling 14d.', self.curses.A_DIM)
        # The ramp was four unexplained fill weights. Naming the steps is
        # the difference between a texture and a measurement.
        x = 1
        self._put(4, x, 'shade:', self.curses.A_DIM)
        x += 7
        for glyph, text in ((ramp_glyph(0.2), 'to 25%'), (ramp_glyph(0.4), 'to 50%'),
                            (ramp_glyph(0.6), 'to 75%'), (ramp_glyph(1.0), 'to 100%')):
            self._put(4, x, glyph * 2, curses.color_pair(2))
            self._put(4, x + 3, text, self.curses.A_DIM)
            x += len(text) + 6
        self._put(4, x, 'of that row\'s largest category   blank = none',
                  self.curses.A_DIM)
        if not rows:
            self._put_lines(5, self._no_run_note('path data'), max_x)
            return

        # Columns are the categories that actually carry traffic, biggest
        # first — the long tail of empty ones is noise in a grid.
        totals = defaultdict(int)
        for row in rows:
            for cat, n in row['categories'].items():
                totals[cat] += n
        columns = [c for c, _n in sorted(totals.items(), key=lambda kv: -kv[1])][:10]
        name_w = min(22, max(10, max_x - 4 * len(columns) - 30))

        self._put(6, 3 + name_w + 1 + 5 * len(columns) + 2, 'depth   views   uniq',
                  curses.color_pair(2) | self.curses.A_BOLD)
        # Reserve the bottom third for the selected repo's actual pages —
        # a category mix says what KIND of page was read, never which one.
        # The pages panel follows the GRID's height with a fixed two-row
        # gap, and the legend sits on the last drawable row.
        #
        # Both halves of that matter. Anchoring the panel to the selected
        # repo's page count made it slide up the screen whenever you moved
        # the cursor to a quieter repo — the layout moving because the data
        # changed. Pinning it to the bottom instead fixed that and left a
        # lake of blank rows between the tables. The grid's row count is
        # constant while the cursor moves, so following it is stable AND
        # tight; it only shifts when a sort or filter genuinely changes how
        # many repos there are.
        legend_row = avail + 1
        GRID_TOP, GAP, MIN_PAGES_BLOCK = 8, 2, 6
        max_grid = max(1, legend_row - MIN_PAGES_BLOCK - GAP - GRID_TOP)
        page = max(1, min(len(rows), max_grid))
        cur = self.cursor('funnel').clamp(len(rows), page)
        self.scroll = cur.scroll

        def attr_for(value, r, c):
            # Colour by category only. Inverting a whole row of the grid to
            # show the selection made that row's cells unreadable, which is
            # the one thing the grid is for; heatmap highlights the row
            # LABEL, and the right edge carries the marker.
            cat = columns[c] if c < len(columns) else ''
            pair = self.FUNNEL_COLORS.get(cat, 0)
            return curses.color_pair(pair) if pair else 0

        # Row-normalized: each repo's mix is comparable to every other's
        # even when its volume is not, which is the whole point of a grid
        # rather than a stack of bars.
        grid_rows = []
        for row in rows:
            peak = max((row['categories'].get(c, 0) for c in columns), default=0) or 1
            grid_rows.append((row['repo'][:name_w],
                              [row['categories'].get(c, 0) / peak for c in columns]))
        end_row = heatmap(
            self._put, self.curses, 6, max_x,
            rows=grid_rows,
            col_labels=[self.FUNNEL_LABELS.get(c, c[:4]) for c in columns],
            label_w=name_w, cell_w=4, gap=1, attr_for=attr_for,
            header_attr=curses.color_pair(2), header_gap=1,
            scroll=cur.scroll, height=page,
            cursor=cur.index, cursor_attr=curses.color_pair(5))

        num_x = 3 + name_w + 1 + 5 * len(columns) + 2
        for i, row in enumerate(rows[cur.scroll:cur.scroll + page]):
            idx = cur.scroll + i
            deep = row['depth_ratio']
            color = (curses.color_pair(1) if deep >= 1.0
                     else curses.color_pair(3) if deep >= 0.5 else self.curses.A_DIM)
            # No overview views at all means the ratio has no denominator.
            # Printing 269.00 implies a measured 269:1; it is really "all
            # of this repo's traffic went past a front door nobody used".
            shown = ('  n/a' if not row['front'] and not row['deep']
                     else f'{deep:>5.2f}' if row['front'] else f'{row["deep"]:>4}*')
            self._put(8 + i, num_x,
                      f'{shown:>5}  {_compact_num(row["total"]):>6} '
                      f'{_compact_num(row["uniq"]):>6}',
                      curses.color_pair(5) if idx == cur.index else color)
            self._mark_selected(8 + i, max_x, idx == cur.index)
        self._scrollbar(8, page, len(rows), cur.scroll, max_x)
        self.scroll_indicator(2, max_x, len(rows), page)
        selected = rows[cur.index]['repo'] if rows else None
        pages_top = GRID_TOP + min(page, len(rows)) + GAP
        self._render_funnel_pages(pages_top, legend_row - 1, max_x, selected)
        end_row = legend_row
        if end_row <= avail + 1:
            legend_x = 3
            for cat in columns:
                pair = self.FUNNEL_COLORS.get(cat, 0)
                text = f'{self.FUNNEL_LABELS.get(cat, cat[:4])}={cat}'
                if legend_x + len(text) + 3 > max_x - 2:
                    break
                self._put(end_row, legend_x, BLOCK,
                          curses.color_pair(pair) if pair else self.curses.A_DIM)
                self._put(end_row, legend_x + 2, text, self.curses.A_DIM)
                legend_x += len(text) + 5

    def _render_epoch_view(self, avail, max_x):
        """The traffic view at the 'epoch' timeframe: the whole store.

        This was a separate view ('history') that duplicated the traffic
        screen with a longer window. Folding it onto the end of the t/T
        cycle removes the duplication and answers the question it always
        raised — "is this the same data as view 1?" — by making it
        literally the same view, one stop further out.
        """
        curses = self.curses
        clones = self.data.history_series('clones')
        views = self.data.history_series('views')
        coverage = self.data.history.get('coverage', [])
        cov = '  '.join(f'{a}→{b}' for a, b in coverage) if coverage else 'n/a'
        self._put(2, 1, f'Traffic — store epoch  (coverage: {cov})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        if not clones and not views:
            self._put(4, 3, 'No history store yet. Run  catnip history  first.',
                      self.curses.A_DIM)
            return
        # Full store, not a trailing slice — the chart bins adjacent days when
        # the window outgrows the terminal, so "since store epoch" is honest.
        #
        # Sizing rule for every chart below. A chart of height h drawn at row
        # `top` occupies top .. top+h+2 — its title, h rows of bars, and then
        # its own x-axis label row — and _bar_chart returns top+h+3. The
        # previous version sized against the remaining space without
        # reserving that label row, and floored the height at 3 when even
        # that much did not fit, so on a short terminal the month labels
        # landed on the footer and rendered `[q]uit 24-05load`. Charts now
        # shrink to fit and are dropped entirely rather than overflowing.
        last = avail + 1  # the last row a view may draw on; the footer owns last+1

        specs = [
            (views, curses.color_pair(2), 'd',
             f'Daily Views since store epoch ({sum(b["count"] for b in views):,} total)'),
            (clones, curses.color_pair(1), 'd',
             f'Daily Clones since store epoch ({sum(b["count"] for b in clones):,} total)'),
        ]

        # Stars-over-time from event timestamps: a full-width bar chart with a
        # month-over-month Δ row beneath it.
        star_series = []
        sbm = self.data.history.get('stars_by_month') or {}
        if sbm:
            months = defaultdict(int)
            for mm in sbm.values():
                for month, cnt in mm.items():
                    months[month] += cnt
            star_series = [{'label': m[2:], 'count': c} for m, c in sorted(months.items())]
            if star_series:
                total = sum(b['count'] for b in star_series)
                specs.append((star_series, curses.color_pair(3), 'mo',
                              f'Stars/month ({total:,} total, since {star_series[0]["label"]})'))

        # The two daily charts share a height — letting the first one take
        # every spare row and squashing the second reads as a rendering bug.
        base = min(7, (last - 9) // 2)
        y, drew_stars = 4, False
        for series, color, unit, title in specs:
            room = last - y - 2  # rows left for bars once title and labels are reserved
            height = min(base, room) if base >= 2 else room
            if height < 2:
                break
            y = self._bar_chart(y, series, max_x, min(height, 7), color,
                                bin_unit=unit, title=title) + 1
            drew_stars = series is star_series

        if drew_stars and y <= last and len(star_series) > 1:
            counts = [b['count'] for b in star_series]
            deltas = [0] + [counts[i] - counts[i - 1] for i in range(1, len(counts))]
            net = counts[-1] - counts[0]
            self._put(y, 1, f'Δ vs prev month: {_sparkline(deltas)}   net {net:+d}',
                      self.curses.A_DIM)

    # ---- the drilldown -------------------------------------------------

    def _chart_series(self, days, values):
        return [{'label': _short_date(d), 'count': v} for d, v in zip(days, values, strict=False)]

    def _render_drilldown(self, avail, max_x):
        """One repo, every derived series, on one screen.

        The primitive the other views launch into. Reading a single repo
        used to mean cross-referencing four screens that each interleaved
        all 98 of them; every table row is now a door.
        """
        curses = self.curses
        repo = self.drilldown
        data = self.data
        tf = self.timeframe
        days = data.window_days(tf)
        win = TF_LABEL.get(tf, 'window')
        last = avail + 1

        aud = data.audience(tf).get(repo) or {}
        ins = data.intent(tf).get(repo) or {}
        events = data.events(repo, days)
        grid = data.anomalies(tf)
        cells = dict(grid['rows']).get(repo) or [None] * len(days)

        label = aud.get('label') or 'no traffic'
        pair = self.AUDIENCE_COLORS.get(label, 0)
        note = ''
        if label == 'low-signal':
            note = (f'  ({aud.get("signal", 0)} unique cloners+visitors, '
                    f'need {derive.AUDIENCE_MIN_SIGNAL} to classify)')
        self._put(2, 1, f' {repo} ', curses.color_pair(5) | self.curses.A_BOLD)
        self._put(2, len(repo) + 4, f'{win}   [{label}]{note}',
                  curses.color_pair(pair) if pair else self.curses.A_DIM)
        self._put(2, max_x - 36, '[space/esc] back  [j/k] next repo',
                  self.curses.A_DIM)

        if not days:
            self._put(4, 3, 'No daily store yet — run  catnip history  first.',
                      self.curses.A_DIM)
            return

        clones = derive.series(data.history, repo, 'clones', days)
        views = derive.series(data.history, repo, 'views', days)

        # Anomaly markers ride the chart's own `peak` support, so the ▲ is
        # positioned by the drawing layer and cannot drift off its bar.
        # clip_ratio matters here for a reason that is easy to miss: the
        # marker is drawn on the row ABOVE the bar, so on the tallest bar —
        # which is usually the anomaly — there is no row for it and it
        # silently disappears. Clipping caps the axis at a robust bound,
        # and the over-cap bar then carries "▲421↑" on the top row instead.
        marked = {i for i, c in enumerate(cells)
                  if c and c['material'] and abs(c['z']) >= derive.Z_SIGNIFICANT}
        view_series = self._chart_series(days, views)
        clone_series = self._chart_series(days, clones)
        for i in marked:
            metric = cells[i]['metric']
            (clone_series if metric == 'clones' else view_series)[i]['peak'] = True

        # A chart drawn at row `top` with height h consumes h + 3 rows:
        # its title, h rows of bars, the axis, and its own x-label row.
        # Sizing against "whatever is left" without reserving that last row
        # is precisely how the old history view printed month labels onto
        # the footer, so the budget is computed up front and the charts are
        # dropped \u2014 not squashed \u2014 when they do not fit.
        MIN_PLOT_H = 3
        y = 4
        # The annotation strip under the clones chart needs its own row (two
        # when the legend does not fit beside the y-axis), reserved here
        # rather than discovered after the charts have taken the space.
        strip = 2 if (events['releases'] or events['pushes'] or marked) else 0
        # Three section breaks below the charts (uniques, funnel, activity).
        room = last - y - strip - 3
        plot_h = min(6, (room - 6) // 2)
        if plot_h >= MIN_PLOT_H:
            y = bar_chart(
                self._put, curses, y, view_series, plot_h, max_x,
                title=f'Daily Views ({win}: {_compact_num(sum(views))})',
                title_attr=curses.color_pair(6) | curses.A_BOLD,
                axis_attr=curses.color_pair(6), color=curses.color_pair(2),
                peak_attr=curses.color_pair(4) | curses.A_BOLD,
                max_bar_w=6, value_labels=True, label_fit=True, bin_unit='d',
                clip_ratio=6)
            geo = {}
            y = bar_chart(
                self._put, curses, y, clone_series, plot_h, max_x,
                title=f'Daily Clones ({win}: {_compact_num(sum(clones))})',
                title_attr=curses.color_pair(6) | curses.A_BOLD,
                axis_attr=curses.color_pair(6), color=curses.color_pair(1),
                peak_attr=curses.color_pair(4) | curses.A_BOLD,
                max_bar_w=6, value_labels=True, label_fit=True, bin_unit='d',
                clip_ratio=6, geometry=geo)
            y = self._render_annotation_strip(y, geo, days, events, marked, max_x)
        else:
            # Too short for charts. Sparklines still carry the shape, and
            # the day of the peak is what the charts were mostly being read
            # for anyway.
            peak_day = days[views.index(max(views))] if any(views) else '-'
            self._put(y, 1, f'views  {_sparkline(views)}  {_compact_num(sum(views))}'
                      f'  peak {peak_day}', curses.color_pair(2))
            peak_day = days[clones.index(max(clones))] if any(clones) else '-'
            self._put(y + 1, 1, f'clones {_sparkline(clones)}  {_compact_num(sum(clones))}'
                      f'  peak {peak_day}', curses.color_pair(1))
            y += 2

        # Uniques track. Sparklines rather than a third chart: the shape
        # is what matters here, and two more axes would cost the funnel.
        uc = derive.series(data.history, repo, 'clones', days, derive.UNIQ)
        uv = derive.series(data.history, repo, 'views', days, derive.UNIQ)
        y += 1  # section break
        if y < last:
            self._put(y, 1, 'Uniques', curses.color_pair(6) | self.curses.A_BOLD)
            y += 1
        # Two fixed columns, each sparkline trimmed to the width that is
        # actually there. At the epoch timeframe these series are 54 days
        # long; laying them out against len(series) ran the second total
        # off the right edge of the terminal.
        spark_w = max(8, min(40, (max_x - 46) // 2))
        for label, values, color in (('uniq cloners ', uc, curses.color_pair(1)),
                                     ('uniq visitors', uv, curses.color_pair(2))):
            if y >= last:
                break
            self._put(y, 3, f'{label} {_sparkline(values[-spark_w:]):<{spark_w}} '
                      f'{sum(values):>6}  (peak {max(values) if values else 0}/day)',
                      color)
            y += 1
        if y < last and aud:
            ratio_note = (f'ratio {aud["ratio"]:.2f} c/v   score {aud["score"]:.2f}   '
                          f'intent {ins.get("score", 0):.2f} [{ins.get("label", "?")}]')
            if ins.get('conflict'):
                ratio_note += '   CONFLICT: developer score, crawler audience'
            self._put(y, 3, ratio_note,
                      curses.color_pair(4) if ins.get('conflict') else self.curses.A_DIM)
            y += 1

        y = self._render_drilldown_funnel(y + 1, last, max_x, repo)
        self._render_drilldown_activity(y + 1, last, max_x, repo, days, events)

    def _render_annotation_strip(self, y, geo, days, events, marked, max_x):
        """One row under the chart carrying what happened and why.

        Anomaly days and their causes share a strip rather than riding the
        bars, because the bar that most needs a marker is the tallest one
        and the drawing layer has no row above it to put the marker on —
        the ▲ was silently dropped from exactly the spike it existed to
        annotate, while the legend went on claiming it was there.

        Geometry comes back from the chart itself, so a mark stays under
        its own bar even when a long window has been binned to fit.
        """
        curses = self.curses
        if not geo.get('n'):
            return y
        binned = max(1, geo.get('binned', 1))
        marks = {}
        for i in sorted(marked):
            if i < len(days):
                marks[i // binned] = (UP_MARK, curses.color_pair(4) | curses.A_BOLD)
        for day, _tag in events['releases']:
            if day in days:
                marks[days.index(day) // binned] = ('R', curses.color_pair(3))
        for day in events['pushes']:
            if day in days:
                i = days.index(day) // binned
                marks.setdefault(i, ('|', self.curses.A_DIM))
        if not marks:
            return y
        row = max(y, geo['label_row'] + 1)
        for i, (glyph, attr) in marks.items():
            if i < geo['n']:
                self._put(row, geo['plot_x'] + i * geo['slot'], glyph, attr)
        legend = []
        if marked:
            legend.append(f'{UP_MARK} |Z|>={derive.Z_SIGNIFICANT:.0f}')
        if events['releases']:
            legend.append(f'R release ({len(events["releases"])})')
        if events['pushes']:
            legend.append(f'| push ({len(events["pushes"])}d)')
        # The legend goes beside the y-axis when it fits there and on its own
        # row when it does not — never both, and never truncated to "▲ |Z|>",
        # which is what clipping it into the axis gutter produced.
        text = '  '.join(legend)
        if len(text) <= geo['plot_x'] - 2:
            self._put(row, 1, text, self.curses.A_DIM)
            return row + 1
        self._put(row + 1, 3, text, self.curses.A_DIM)
        return row + 2

    def _render_drilldown_funnel(self, y, last, max_x, repo):
        curses = self.curses
        row = self.data.funnel_depth().get(repo)
        if y >= last:
            return y
        self._put(y, 1, 'Funnel (rolling 14d, not the selected window)',
                  curses.color_pair(6) | self.curses.A_BOLD)
        y += 1
        if not row:
            note = ('no run on disk, so no path data was collected'
                    if self.data.store_only
                    else 'this repo had no page views in the run\'s window')
            self._put(y, 3, note, self.curses.A_DIM)
            return y + 1
        cats = sorted(row['categories'].items(), key=lambda kv: -kv[1])[:6]
        peak = max((n for _c, n in cats), default=1) or 1
        bar_w = max(6, min(24, max_x - 46))
        for cat, n in cats:
            if y >= last:
                return y
            pair = self.FUNNEL_COLORS.get(cat, 0)
            self._put(y, 3, f'{cat[:14]:<14} {self._hbar(n, peak, bar_w):<{bar_w}} {n:>6}',
                      curses.color_pair(pair) if pair else self.curses.A_DIM)
            y += 1
        if y < last:
            deep = row['depth_ratio']
            color = (curses.color_pair(1) if deep >= 1.0
                     else curses.color_pair(3) if deep >= 0.5 else self.curses.A_DIM)
            self._put(y, 3, f'depth {deep:.2f}  ({row["deep"]} past the front door / '
                      f'{row["front"]} overview)', color)
            y += 1
        return y

    def _render_drilldown_activity(self, y, last, max_x, repo, days, events):
        """PRs, releases and pushes — with 'not collected' kept distinct
        from 'zero'.

        `commit-days 0` is a measurement; `commit-days —` is the absence of
        one. Printing 0 for both invites exactly the question it should be
        answering: an operator reading "0 commits" on a repo they pushed to
        last week has been told something false, not something empty.
        """
        curses = self.curses
        if y >= last:
            return
        self._put(y, 1, 'Activity', curses.color_pair(6) | self.curses.A_BOLD)
        y += 1
        if y >= last:
            return
        has_events = bool((self.data.history or {}).get('events'))
        has_prs = bool(self.data.prs) or not self.data.store_only
        prs = self._windowed_pr_counts().get(repo, {'opened': 0, 'merged': 0})
        pr_txt = (f'PRs opened {prs["opened"]}   merged {prs["merged"]}'
                  if has_prs else 'PRs not collected')
        if has_events:
            commits = sum(events['pushes'].values())
            rels = ', '.join(tag for _day, tag in events['releases']) or 'none'
            ev_txt = (f'commit-days {len(events["pushes"])} ({commits} commits)   '
                      f'releases: {rels}')
        else:
            ev_txt = 'commits/releases not collected (store predates the event log)'
        self._put(y, 3, f'{pr_txt}   {ev_txt}'[: max_x - 5],
                  0 if (has_events and has_prs) else self.curses.A_DIM)
        y += 1
        if not (has_events and has_prs) and y < last:
            self._put(y, 3, 'run  catnip run  to start collecting these',
                      self.curses.A_DIM)
            y += 1
        coupled = derive.coupled_for(self.data.history, self.timeframe, repo)
        if y < last:
            if coupled:
                pairs = '   '.join(f'{c["repo"][:18]} (r={c["r"]:+.2f})' for c in coupled)
                self._put(y, 3, f'coupled: {pairs}'[: max_x - 5], self.curses.A_DIM)
            else:
                self._put(y, 3, 'coupled: nothing survives residualization in this window',
                          self.curses.A_DIM)

    def _render_footer(self, max_y, max_x):
        curses = self.curses
        active = curses.color_pair(5)
        dim = self.curses.A_DIM
        if self.overlay:
            self.render_footer_items(max_y, [('[?]/[esc] close derivation', active)], x=1)
            return
        if self.drilldown_finding:
            self.render_footer_items(max_y, [
                ('[space/esc]back', dim),
                ('[j/k]next finding', dim),
                ('[?]derivation', dim),
                ('[Q]uit', dim),
            ], x=1)
            return
        if self.drilldown_momentum:
            self.render_footer_items(max_y, [
                ('[space/esc]back', dim),
                ('[j/k]next repo', dim),
                (f'[t/T]tf:{self.timeframe}', active if self.timeframe != '2w' else dim),
                ('[?]derivation', dim),
                ('[Q]uit', dim),
            ], x=1)
            return
        if self.drilldown_pair:
            self.render_footer_items(max_y, [
                ('[space/esc]back', dim),
                ('[j/k]next pair', dim),
                (f'[t/T]tf:{self.timeframe}', active if self.timeframe != '2w' else dim),
                ('[?]derivation', dim),
                ('[Q]uit', dim),
            ], x=1)
            return
        if self.drilldown_event:
            self.render_footer_items(max_y, [
                ('[space/esc]back', dim),
                ('[j/k]next event', dim),
                (f'[t/T]tf:{self.timeframe}', active if self.timeframe != '2w' else dim),
                ('[?]derivation', dim),
                ('[Q]uit', dim),
            ], x=1)
            return
        if self.drilldown:
            self.render_footer_items(max_y, [
                ('[space/esc]back', dim),
                ('[j/k]next repo', dim),
                (f'[t/T]tf:{self.timeframe}', active if self.timeframe != '2w' else dim),
                ('[?]derivation', dim),
                ('[r]eload', dim),
                ('[Q]uit', dim),
            ], x=1)
            return
        # Only the keys that do something on THIS screen, in THIS state.
        # A footer offering [enter]repo over an empty list, or [f]ilter when
        # there is one category, is not a legend — it is a set of small
        # lies the operator has to test one at a time.
        rows = self._cursor_rows() if self.view in REPO_ROW_VIEWS else []
        scrollable = bool(rows) or self._scroll_bound() > 1
        tf_applies = self.view != 'funnel'
        items = [('[q]uit', dim), ('[r]eload', dim), ('[1-9,0]view', dim)]
        if tf_applies:
            items.append((f'[t/T]tf:{self.timeframe}',
                          active if self.timeframe != '2w' else dim))
        else:
            items.append((f'[t/T]tf:{self.timeframe} n/a here', dim))
        items.append(('[v/V]cycle', dim))
        if scrollable:
            items.append(('[j/k g/G]move', dim))
        if rows or self.search:
            items.append((f'[/]{self.search or "search"}',
                          active if self.search else dim))
        items.append(('[?]why', dim))
        if self.data.knows_forks():
            items.append((f'[o]{self.scope_label()}',
                          active if self.scope_label() != 'all' else dim))
        if rows and self.view != 'funnel':
            items.append(('[space]repo', active))
        if self.view == 'anomaly' and self.data.anomalies(self.timeframe)['days']:
            items.append((f'[f]ilter:{self.ANOMALY_SEVERITIES[self.anomaly_filter]}',
                          active))
            if self._anomaly_events():
                items.append(('[tab]events' if self.anomaly_pane else '[tab]grid',
                              active))
                if self.anomaly_pane == 0:
                    name, _k, natural = self.EVENT_SORT_KEYS[self.event_sort]
                    flipped = self.sort_flip.get('anomaly_events', False)
                    items.append((f'[s]ort:{name}{self.sort_arrow(natural != flipped)}',
                                  active))
                    items.append(('[S]flip', dim))
        elif self.view == 'profile' and rows:
            items.append((f'[s]ort:{self._footer_sort()}', active))
            items.append(('[S]flip', dim))
            items.append(('[l]ow:hidden' if self.profile_hide_low else '[l]ow:shown', active))
        elif self.view == 'audience' and rows:
            items.append((f'[s]ort:{self._footer_sort()}', active))
            items.append(('[S]flip', dim))
            items.append(('[l]ow:hidden' if self.audience_hide_low else '[l]ow:shown',
                          active))
        elif self.view == 'deltas' and rows:
            name, _k, natural = self.DELTAS_SORT_KEYS[self.deltas_sort]
            items.append((f'[s]ort:{name}{self.sort_arrow(self._sort_desc(natural))}',
                          active))
            items.append(('[S]flip', dim))
            items.append(('[z]zero:hidden' if self.deltas_hide_zero else '[z]zero:shown',
                          active))
        elif self.view == 'table' and rows:
            items.append((f'[s]ort:{self._footer_sort()}', active))
            items.append(('[S]flip', dim))
            statuses = self._table_statuses()
            if len(statuses) > 1:
                items.append((f'[f]ilter:{self._table_status()}', active))
        elif self.view == 'funnel' and rows:
            if self.funnel_pane:
                name = self.PAGES_SORT_KEYS[self.pages_sort][0]
                nat = self.PAGES_SORT_KEYS[self.pages_sort][2]
                flip = self.sort_flip.get('funnel_pages', False)
                items.append((f'[s]ort:pages {name}{self.sort_arrow(nat != flip)}', active))
                items.append(('[space/tab]back to grid', active))
            else:
                name = self.FUNNEL_SORT_KEYS[self.funnel_sort][0]
                nat = self.FUNNEL_SORT_KEYS[self.funnel_sort][2]
                flip = self.sort_flip.get('funnel', False)
                items.append((f'[s]ort:{name}{self.sort_arrow(nat != flip)}', active))
                items.append(('[space/tab]pages', active))
                items.append(('[enter]repo', dim))
            items.append(('[S]flip', dim))
            cats = self._funnel_categories()
            if len(cats) > 1:
                filt = ('all' if not 0 < self.funnel_filter <= len(cats)
                        else cats[self.funnel_filter - 1])
                items.append((f'[f]ilter:{filt}', active))
        elif self.view == 'traffic' and rows:
            items.append(('[tab]list', active))
        self.render_footer_items(max_y, items, x=1)


# ---- text mode ---------------------------------------------------------------

def render_text_view(data, view, timeframe='2w'):
    """Print one view as a plain table.

    The agent-facing surface. It reads the same derive functions the TUI
    does, so `catnip view deltas` and the deltas screen cannot disagree
    about a number — the alternative is two implementations of the same
    formula and a support question about which one is right.
    """
    if view == 'audience':
        rows = sorted(data.audience(timeframe).values(), key=lambda r: r['score'])
        print(f'{"repo":<30} {"score":>6} {"class":<9} {"ratio":>8} {"burst":>6} '
              f'{"depth":>7} {"refs":>5}')
        for r in rows:
            burst = f'{r["burst"]:.2f}' if r['burst'] is not None else '-'
            depth = f'{r["depth"]:.1f}' if r['depth'] is not None else '-'
            refs = r['referrer_diversity'] if r['referrer_diversity'] is not None else '-'
            print(f'{r["repo"][:30]:<30} {r["score"]:>6.2f} {r["label"]:<9} '
                  f'{r["ratio"]:>8.2f} {burst:>6} {depth:>7} {str(refs):>5}')
    elif view == 'table':
        deltas = data.deltas(timeframe)
        audience = data.audience(timeframe)
        depth = data.funnel_depth()
        for name in sorted(deltas, key=lambda n: -(deltas[n]['clones']['cur'])):
            d, a = deltas[name], audience.get(name) or {}
            mom = d['clones']['delta']
            print(f'  {name:<30} clones={d["clones"]["cur"]:>6} '
                  f'views={d["views"]["cur"]:>6} '
                  f'momentum={"n/a" if mom is None else f"{mom:+d}":>7} '
                  f'audience={a.get("score", 0):.2f} '
                  f'depth={(depth.get(name) or {}).get("depth_ratio", 0):.2f}')
    elif view == 'lang':
        for r in data.lang_dist:
            print(f"  {r.get('language', ''):<20} {_int(r, 'total_bytes'):>12,} "
                  f"({r.get('total_bytes_pct', '')}%) repos={r.get('repo_count', '')}")
    elif view == 'attribution':
        result = data.attribution(timeframe)
        for r in result['rows']:
            via = f"  via {r['via'][0]} r={r['via'][1]:+.2f}" if r['via'] else ''
            effect = ('no measurable change' if r['tier'] == 'no-effect'
                      else f"{r['metric']} {r['value']}")
            print(f"  {r['day']}  {r['repo']:<28} {r['tier']:<11} "
                  f"{(r['cause'] or '-'):<8} {(r['detail'] or ''):<26} {effect}{via}")
    elif view == 'deltas':
        rows = data.deltas(timeframe)
        n = len(data.window_days(timeframe)) or 1
        comparable = any(r['comparable'] for r in rows.values())
        print(f'window {n}d, previous window '
              f'{"available" if comparable else "not in the store yet"}')
        for name, r in sorted(rows.items(),
                              key=lambda kv: -abs(kv[1]['clones']['delta'] or 0)):
            c, v = r['clones'], r['views']
            cd = 'n/a' if c['delta'] is None else f'{c["delta"]:+d}'
            vd = 'n/a' if v['delta'] is None else f'{v["delta"]:+d}'
            print(f'  {name:<30} clones={c["cur"]:>6} d={cd:>7} rate={c["rate"]:>6.1f}/d  '
                  f'views={v["cur"]:>6} d={vd:>7}')
    elif view == 'anomaly':
        grid = data.anomalies(timeframe)
        for day, repos in sorted(grid['campaigns'].items()):
            print(f'  {day}  ACCOUNT EVENT: {len(repos)} repos — {", ".join(repos)}')
        for repo, cells in grid['rows']:
            for i, cell in enumerate(cells):
                if cell and cell['material']:
                    print(f'  {repo:<30} {grid["days"][i]} {cell["metric"]:>6} '
                          f'{cell["dir"]:>5} z={cell["z"]:>8.2f} value={cell["value"]}')
    elif view == 'profile':
        for r in sorted(data.intent(timeframe).values(), key=lambda r: -r['score']):
            flag = '  CONFLICT' if r['conflict'] else ''
            print(f'  {r["repo"]:<30} score={r["score"]:>8.3f} [{r["label"]}] '
                  f'uc={r["uniq_cloners"]} uv={r["uniq_visitors"]}{flag}')
    elif view == 'correlation':
        result = data.coupled(timeframe)
        if result['reason']:
            print(f'  {result["reason"]}')
        else:
            print(f'  residualized; lag '
                  f'{"available" if result["lag_available"] else "suppressed"} '
                  f'(n={len(result["days"])})')
            for p in result['pairs'][:50]:
                print(f'  {p["a"]:<25} <-> {p["b"]:<25} r={p["r"]:>7.3f} '
                      f'(raw {p["raw_r"]:>7.3f})')
    elif view == 'funnel':
        for r in sorted(data.funnel_depth().values(), key=lambda r: -r['depth_ratio']):
            top = sorted(r['categories'].items(), key=lambda kv: -kv[1])[:3]
            mix = ' '.join(f'{c}={n}' for c, n in top)
            print(f'  {r["repo"]:<30} depth={r["depth_ratio"]:>6.2f} '
                  f'views={r["total"]:>6}  {mix}')
    elif view == 'why':
        for line in derive.derivation(timeframe):
            print(line)
    else:
        render_text(data, timeframe)


def render_text(data, timeframe):
    """Print everything to stdout — no curses."""
    repos = data.repos
    if not repos and not data.has_store():
        print('No repository data found.')
        print('Run  catnip run  first.')
        return

    owner = (data.totals.get('owner') or (data.history or {}).get('owner')
             or '(owner unset)')
    print(f'catnip  [{owner}]  [{timeframe}]')
    print(f'Repos: {data._total_repos}  Stars: {data._totals["total_stars"]:,}  Forks: {data._totals["total_forks"]:,}')
    alltime = ''
    if data.totals.get('total_clones') is not None:
        alltime = (f'  All-time: {data.totals["total_clones"]:,}c/'
                   f'{data.totals["total_views"]:,}v')
    print(f'Active: {data._active_count}  14d clones: {data._totals["total_clones"]:,}  '
          f'14d views: {data._totals["total_views"]:,}{alltime}')
    print()

    # Honour --timeframe: the CSV totals are GitHub's trailing ~14 days
    # regardless, so ranking by them printed 14d numbers under a [1d] header.
    win = TF_LABEL.get(timeframe, 'last 14d')
    wc = data.windowed_clones(timeframe)
    wv = data.windowed_views(timeframe)
    scores = {r.get('repo_name', ''): float(r.get('traffic_score', 0) or 0) for r in repos}

    top_c = sorted(wc.items(), key=lambda kv: -kv[1])[:10]
    print(f'Top 10 by Clones ({win}):')
    print(f'  {"Rank":>4}  {"Repo":<25}  Clones  Views  Score')
    print(f'  {"----":>4}  {"----":<25}  {"------":>7}  {"------":>7}  {"------":>7}')
    for i, (name, c) in enumerate(top_c, 1):
        print(f'  {i:>4}  {name[:25]:<25}  {c:>6,}  {wv.get(name, 0):>6,}  '
              f'{scores.get(name, 0.0):.4f}')

    print()
    top_v = sorted(wv.items(), key=lambda kv: -kv[1])[:10]
    print(f'Top 10 by Views ({win}):')
    print(f'  {"Rank":>4}  {"Repo":<25}  Views  Clones')
    print(f'  {"----":>4}  {"----":<25}  {"-----":>7}  {"------":>8}')
    for i, (name, v) in enumerate(top_v, 1):
        print(f'  {i:>4}  {name[:25]:<25}  {v:>6,}  {wc.get(name, 0):>6,}')

    print()
    t = data.totals
    if t and 'repo_snapshots' in t:
        rs = t['repo_snapshots']
        deltas = [(n, s.get('stars_delta', 0)) for n, s in rs.items()]
        deltas.sort(key=lambda x: -abs(x[1]))
        if deltas:
            print(f'Rolling snapshots: {len(rs)}')
            print('Top deltas:')
            for name, sd in deltas[:5]:
                marker = '+' if sd > 0 else ''
                print(f'  {name}: stars {marker}{sd}')

def main(argv=None):
    parser = argparse.ArgumentParser(description='Interactive terminal UI for catnip data.')
    parser.add_argument('run_dir', nargs='?',
                        help='Run directory containing analysis/ (default: newest run).')
    parser.add_argument('--config', help='Explicit config file path.')
    parser.add_argument('--text', action='store_true', dest='text_mode',
                        help='Disable curses, print to stdout instead.')
    parser.add_argument('--timeframe', choices=TIMEFRAMES, default='2w',
                        help='Initial timeframe (default: 2w).')
    parser.add_argument('--view', choices=(*VIEWS, 'why'), default='traffic',
                        help='Initial view; with --text, which view to print. '
                             "'why' prints a derivation instead of data.")
    parser.add_argument('--history-file',
                        help='Durable daily store to read (default: from config).')
    parser.add_argument('--stats-file',
                        help='Account totals to read (default: from config).')
    parser.add_argument('--store-only', action='store_true',
                        help='Read only the durable store; ignore run directories.')
    args = parser.parse_args(argv)

    from catnip.config import Config, ConfigError
    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f'catnip: config error: {exc}', file=sys.stderr)
        return 2

    history_file = Path(args.history_file) if args.history_file else cfg.history_file
    stats_file = Path(args.stats_file) if args.stats_file else cfg.stats_file

    run_dir = None if args.store_only else (
        Path(args.run_dir) if args.run_dir else cfg.latest_run())
    if run_dir is not None and not (run_dir / 'analysis').is_dir():
        print(f'catnip: {run_dir} has no analysis/ — run `catnip analyze` first.',
              file=sys.stderr)
        return 1
    if run_dir is None and not Path(history_file).is_file():
        # Neither source exists: this is a fresh install, not a pruned one.
        print('catnip: no runs and no history store. Run `catnip run` first.',
              file=sys.stderr)
        return 1
    if run_dir is None and not args.store_only:
        print(f'catnip: no runs on disk — reading the durable store only '
              f'({history_file}). Per-run views (lang, funnel) will be empty.',
              file=sys.stderr)

    data = AnalyticsData(run_dir, stats_file, history_file)
    if args.text_mode:
        if args.view != 'traffic':
            render_text_view(data, args.view, args.timeframe)
        else:
            render_text(data, args.timeframe)
    else:
        curses_main(lambda scr: AnalyticsTUI(scr, data, args.timeframe, view=args.view))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
