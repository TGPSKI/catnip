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
"""
import argparse
import json
import sys
from collections import defaultdict
from contextlib import suppress
from datetime import date, timedelta
from pathlib import Path

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

VIEWS = ('traffic', 'top', 'table', 'lang', 'freq', 'deltas', 'anomaly',
         'profile', 'correlation', 'funnel', 'history')
# Digits jump straight to views (plan 08 phase 6); timeframes moved to t/T.
# PR activity was folded into the deltas view (plan 13) — no standalone 'pr'.
VIEW_KEYS = '1234567890-='
TIMEFRAMES = ('1d', '1w', '2w', 'all')

# Trailing days per timeframe. GitHub's traffic API exposes ~14 daily
# buckets; 'all' reads the history store's account-wide series (weeks/months once
# ingested).
DAY_WINDOW = {'1d': 1, '1w': 7, '2w': 14, 'all': None}
TF_LABEL = {'1d': 'last day', '1w': 'last 7d', '2w': 'last 14d', 'all': 'all history'}


class AnalyticsData:
    """Loads one run's analysis CSVs, plus the two cross-run stores.

    The per-run CSVs live beside the run; totals and history are
    account-wide and outlive any single run. Both store paths are passed
    in (the CLI resolves them from config) and fall back to the layout
    catnip writes — <data>/runs/<id> next to <data>/stats — so pointing
    the viewer at a run directory copied somewhere else still works.
    """

    def __init__(self, fetch_dir, stats_file=None, history_file=None):
        self.fetch_dir = Path(fetch_dir).resolve()
        data_dir = self.fetch_dir.parent.parent
        self.stats_file = Path(stats_file) if stats_file else data_dir / 'stats' / 'totals.json'
        self.history_file = (Path(history_file) if history_file
                             else data_dir / 'stats' / 'history' / 'traffic_daily.json')
        self.analysis_dir = self.fetch_dir / 'analysis'
        self.reload()

    def _read_csv(self, fname):
        return read_csv(self.analysis_dir / fname)

    def reload(self):
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

        self.daily_views = self._org_series(self._repo_view_buckets)
        self.daily_clones = self._org_series(self._repo_clone_buckets)

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

    def _repo_history_buckets(self, metric):
        """Per-repo daily buckets from the history store (survives fetch-dir
        pruning); {} when no store exists."""
        out = {}
        for repo, metrics in (self.history.get('repos') or {}).items():
            if not repo:
                continue
            days = (metrics.get(metric) or {})
            if days:
                out[repo] = sorted(
                    (d, v[0] if isinstance(v, list) and v else 0)
                    for d, v in days.items())
        return out

    def _windowed_metric(self, metric, csv_buckets, timeframe):
        n = DAY_WINDOW.get(timeframe, 14)
        if n is None:
            # 'all' must read the same source the chart does, or the top lists
            # silently report the fetch's ~14-day window under an "all history"
            # heading.
            return self._windowed(self._repo_history_buckets(metric) or csv_buckets, None)
        return self._windowed(csv_buckets, n)

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


class AnalyticsTUI(TuiApp):
    def __init__(self, stdscr, data, timeframe='2w', view='traffic'):
        super().__init__(stdscr)
        self.data = data
        self.timeframe = timeframe if timeframe in TIMEFRAMES else '2w'
        self.view = view if view in VIEWS else 'traffic'
        self.search = ''
        self.table_sort = 0       # 0=score, 1=clones, 2=views, 3=name
        self.table_status = 0     # 0=all, 1=active, 2=stale, 3=archived
        self.profile_sort = 0     # 0=score, 1=ratio, 2=name
        self.profile_hide_low = False  # 'l' toggles the low-signal section
        self.anomaly_filter = 0   # 0=all, 1=extreme, 2=significant, 3=minor
        self.funnel_filter = 0    # index into funnel categories (0=all)
        self.top_criterion = 0    # index into TOP_CRITERIA (plan 13: one per screen)

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

    def _filtered(self, rows, key='repo_name'):
        """Apply the / search filter (substring on repo name)."""
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

    def handle_key(self, key):
        curses = self.curses
        if key == 27 and self.search:
            self.search = ''
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
        elif key in (ord('s'), ord('S')):
            if self.view == 'profile':
                self.profile_sort = (self.profile_sort + 1) % 3
                self.scroll = 0
            elif self.view == 'table':
                self.table_sort = (self.table_sort + 1) % 4
                self.scroll = 0
            elif self.view == 'top':
                self.top_criterion = (self.top_criterion + 1) % len(self.TOP_CRITERIA)
                self.scroll = 0
        elif key in (ord('f'), ord('F')):
            if self.view == 'anomaly':
                self.anomaly_filter = (self.anomaly_filter + 1) % 4
                self.scroll = 0
            elif self.view == 'table':
                self.table_status = (self.table_status + 1) % 4
                self.scroll = 0
            elif self.view == 'funnel':
                self.funnel_filter = (self.funnel_filter + 1) % (len(self._funnel_categories()) + 1)
                self.scroll = 0
        elif key == ord('l') and self.view == 'profile':
            self.profile_hide_low = not self.profile_hide_low
            self.scroll = 0
        elif key in (curses.KEY_UP, ord('k')):
            self.scroll = max(0, self.scroll - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            # Clamp: unbounded scrolling ran the list off the top and left the
            # view blank with no indication of how far back 'k' had to go.
            self.scroll = min(self.scroll + 1, max(0, self._scroll_bound() - 1))
        elif key == ord('g'):
            self.scroll = 0
        elif key == ord('G'):
            # last page, sized to the real terminal rather than MIN_ROWS
            max_y, _ = self.stdscr.getmaxyx()
            self.scroll = max(0, self._scroll_bound() - max(1, max_y - 8))
        elif key == ord('/'):
            max_y, max_x = self.stdscr.getmaxyx()
            self._prompt_search(max_y, max_x)
        return False

    def _funnel_categories(self):
        return sorted({r.get('category', '') for r in self.data.funnel_data
                       if r.get('category')})

    def _scroll_bound(self):
        """Approximate number of rendered lines for the current view, for g/G."""
        d = self.data
        if self.view == 'top':
            return len(self._top_rows(self._active_criterion())[1])
        if self.view == 'lang':
            return len(d.lang_dist)
        if self.view == 'freq':
            return len(d.code_freq[:500])
        if self.view == 'deltas':
            t = d.totals
            return len(t.get('repo_snapshots', {})) if t else 0
        if self.view == 'anomaly':
            return len(self._filtered_anomaly_data())
        if self.view == 'profile':
            return len(d.profile_data)
        if self.view == 'correlation':
            return len(d.correlation_data)
        if self.view == 'funnel':
            return len(self._funnel_lines()[1]) + len(self._funnel_lines()[0]) + 3
        if self.view == 'history':
            return 0
        return len(d.repos)

    def _filtered_anomaly_data(self):
        data = self._filtered(self.data.anomaly_data)
        severities = ('extreme', 'significant', 'minor')
        if self.anomaly_filter == 0:
            return data
        target = severities[self.anomaly_filter - 1]
        return [r for r in data if r.get('anomaly_type') == target]

    def render(self, max_y, max_x):
        self._render_header(max_x)
        self._render_footer(max_y, max_x)

        avail = max_y - 3
        if avail <= 0:
            return

        if self.view == 'traffic':
            self._render_traffic_view(avail, max_x)
        elif self.view == 'top':
            self._render_top_view(avail, max_x)
        elif self.view == 'table':
            self._render_table_view(avail, max_x)
        elif self.view == 'lang':
            self._render_lang_view(avail, max_x)
        elif self.view == 'freq':
            self._render_freq_view(avail, max_x)
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
        elif self.view == 'history':
            self._render_history_view(avail, max_x)

    def _render_header(self, max_x):
        curses = self.curses
        owner = self.data.totals.get('owner') or 'github'
        search_tag = f'  /{self.search}' if self.search else ''
        text = f' catnip  [{owner}]  [{self.timeframe}]{search_tag}'
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
        win = TF_LABEL.get(tf, 'last 14d')

        # Windowed traffic for this timeframe (t/T cycles).
        n = DAY_WINDOW.get(tf, 14)
        wv = self.data.windowed_views(tf)
        wc = self.data.windowed_clones(tf)
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

        # Two side-by-side top lists, windowed to the timeframe.
        col2 = max_x // 2
        top_v = sorted(wv.items(), key=lambda kv: -kv[1])[:8]
        top_c = sorted(wc.items(), key=lambda kv: -kv[1])[:8]
        self._put(y, 1, f'Top Views ({win})', curses.color_pair(6) | self.curses.A_BOLD)
        self._put(y, col2, f'Top Clones ({win})', curses.color_pair(6) | self.curses.A_BOLD)
        for i, (name, val) in enumerate(top_v):
            self._put(y + 1 + i, 3, f'{i+1:>2}. {name[:18]:<18} {_compact_num(val):>6}', 0)
        for i, (name, val) in enumerate(top_c):
            self._put(y + 1 + i, col2 + 2, f'{i+1:>2}. {name[:18]:<18} {_compact_num(val):>6}', 0)
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

    # criterion -> (heading, one-line meaning, unit, hide-zeros)
    TOP_CRITERIA = {
        'stars':        ('Stars', 'GitHub stargazers', 'stars', True),
        'forks':        ('Forks', 'number of forks', 'forks', True),
        'contributors': ('Contributors', 'unique contributors (stats API)', 'people', True),
        'activity':     ('Activity Score', 'blend of stars, forks, commits, contributors (0-1)', '', True),
        'recent':       ('Least Recently Pushed', 'days since last push (larger = more neglected)', 'days', False),
        'age':          ('Oldest Repos', 'repo age', 'days', False),
        'clones':       ('Clones', 'git clones, all-time tracked', 'clones', True),
        'traffic':      ('Traffic Score', 'blend of clones, views, unique visitors (0-1)', '', True),
    }

    TOP_ROW_CAP = 15  # per-criterion cap (plan 08 phase 5)

    def _criteria_keys(self):
        return list(self.TOP_CRITERIA.keys())

    def _active_criterion(self):
        keys = self._criteria_keys()
        return keys[self.top_criterion % len(keys)]

    def _top_rows(self, crit):
        """(meta, rows) for one criterion: meta=(heading,meaning,unit,hide_zero),
        rows=[(name, value)] in CSV rank order, zeros hidden per the criterion,
        capped at TOP_ROW_CAP."""
        meta = self.TOP_CRITERIA[crit]
        hide_zero = meta[3]
        rows = []
        for r in self._filtered(self.data.top_repos_csv):
            if r.get('sort_criteria', '') != crit:
                continue
            value = _float(r, 'value')
            if hide_zero and value == 0:
                continue
            rows.append((r.get('repo_name', ''), value))
            if len(rows) >= self.TOP_ROW_CAP:
                break
        return meta, rows

    def _render_top_view(self, avail, max_x):
        curses = self.curses
        keys = self._criteria_keys()
        crit = self._active_criterion()
        (heading, meaning, unit, _hz), rows = self._top_rows(crit)
        pos = f'{self.top_criterion % len(keys) + 1}/{len(keys)}'
        self._put(2, 1, f'Top Repos — {heading}  ({pos})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, f'{meaning}.  Press [s] to cycle criterion.', self.curses.A_DIM)
        if not rows:
            self._put(5, 3, 'No repos with a value for this criterion.', self.curses.A_DIM)
            return
        unit_lbl = unit or 'score'
        self._put(5, 3, f'  {"#":>3}  {"repo":<26} {unit_lbl:>10}  relative',
                  curses.color_pair(2) | self.curses.A_BOLD)
        max_v = max((v for _, v in rows), default=1) or 1
        # Relative bar fills the width after the rank/name/value columns.
        bar_w = max(10, max_x - 49)
        scroll = self.scroll
        shown = avail - 4
        for i, (name, value) in enumerate(rows[scroll:scroll + shown]):
            rank = scroll + i + 1
            vs = f'{value:.3f}' if not unit else f'{int(value):,}'
            bar = self._hbar(value, max_v, bar_w)
            self._put(6 + i, 3, f'  {rank:>3}. {name[:26]:<26} {vs:>10}  {bar}',
                      curses.color_pair(2) if i == 0 and scroll == 0 else 0)
        self.scroll_indicator(2, max_x, len(rows), shown)

    TABLE_SORT_KEYS = (
        ('score', lambda r: float(r.get('traffic_score', 0) or 0)),
        ('clones', lambda r: _int(r, 'total_clones')),
        ('views', lambda r: _int(r, 'total_views')),
        ('name', lambda r: r.get('repo_name', '').lower()),
    )

    TABLE_STATUS = ('all', 'active', 'stale', 'archived')

    def _render_table_view(self, avail, max_x):
        curses = self.curses
        repos = self._filtered(self.data.repos)
        status_filter = self.TABLE_STATUS[self.table_status]
        if status_filter != 'all':
            repos = [r for r in repos if (r.get('status') or 'unknown') == status_filter]
        sort_label, sort_key = self.TABLE_SORT_KEYS[self.table_sort]
        repos = sorted(repos, key=sort_key, reverse=(sort_label != 'name'))

        # Title row carries the count badge (was colliding with the header
        # columns at narrow widths). Score-color legend sits at the top (plan 13)
        # so it stays visible while scrolling.
        self._put(2, 1, f'Repo Table  [{len(repos)}] {status_filter}',
                  curses.color_pair(6) | self.curses.A_BOLD)
        lx = max(34, len(f'Repo Table  [{len(repos)}] {status_filter}') + 4)
        if lx + 30 < max_x:
            self._put(2, lx, 'score:', self.curses.A_DIM)
            self._put(2, lx + 7, '>0.7', curses.color_pair(1))
            self._put(2, lx + 12, '0.3-0.7', curses.color_pair(3))
            self._put(2, lx + 20, '<0.3', self.curses.A_DIM)
        if not repos:
            self._put(3, 1, 'No repos match.', self.curses.A_DIM)
            return
        # Adaptive columns: name gets whatever the numeric block leaves.
        name_w = max(12, min(40, max_x - 38))
        c_clones = 6 + name_w
        c_views = c_clones + 9
        c_score = c_views + 9
        c_status = c_score + 9
        self._put(3, 1,
                  f'{"#":>3}  {"repo":<{name_w}} {"clones":>8} {"views":>8} {"score":>7}  status',
                  curses.color_pair(2) | self.curses.A_BOLD)

        scroll = self.scroll
        shown = avail - 5
        for i in range(min(shown, len(repos) - scroll)):
            r = repos[scroll + i]
            name = r.get('repo_name', '')[:name_w]
            clones = _int(r, 'total_clones')
            views = _int(r, 'total_views')
            score = float(r.get('traffic_score', 0) or 0)
            status = r.get('status') or 'unknown'
            if score > 0.7:
                color = curses.color_pair(1)
            elif score > 0.3:
                color = curses.color_pair(3)
            else:
                color = self.curses.A_DIM
            if status == 'active':
                s_color = curses.color_pair(1)
            elif status == 'stale':
                s_color = self.curses.A_DIM
            elif status == 'archived':
                s_color = curses.color_pair(4)
            else:
                s_color = 0
            row = scroll + i + 1
            self._put(4 + i, 1, f'{row:>3}. {name:<{name_w}}', 0)
            self._put(4 + i, c_clones, f'{_compact_num(clones):>8}', color)
            self._put(4 + i, c_views, f'{_compact_num(views):>8}', color)
            self._put(4 + i, c_score, f'{score:>7.3f}', color)
            self._put(4 + i, c_status, f'[{status:>8}]', s_color)
        self.scroll_indicator(2, max_x, len(repos), shown)

    def _render_lang_view(self, avail, max_x):
        curses = self.curses
        # Only languages >= 0.1% of the codebase; the long <0.1% tail is noise.
        dist = [r for r in self.data.lang_dist if _float(r, 'total_bytes_pct') >= 0.1]
        hidden = len(self.data.lang_dist) - len(dist)
        title = 'Language Distribution  (by bytes, >=0.1%)'
        if hidden:
            title += f'  [{hidden} smaller languages hidden]'
        self._put(2, 1, title, curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 3, f'{"language":<18} {"share":<25} {"bytes":>12}  repos',
                  curses.color_pair(2) | self.curses.A_BOLD)
        scroll = self.scroll
        if not dist:
            self._put(4, 3, 'No language data.', self.curses.A_DIM)
            return
        # Fixed readable palette (skip pair 5 = black-on-cyan reverse video).
        palette = [curses.color_pair(1), curses.color_pair(2), curses.color_pair(3),
                   curses.color_pair(6), curses.color_pair(7), curses.color_pair(4)]
        max_bytes = max((_int(r, 'total_bytes') for r in dist), default=1) or 1
        # Bar fills the width left after the fixed language + numeric columns.
        bar_w = max(10, max_x - 50)
        for i, r in enumerate(dist[scroll:scroll + avail - 3]):
            idx = scroll + i
            lang = r.get('language', '')[:18]
            tb = _int(r, 'total_bytes')
            pct = _float(r, 'total_bytes_pct')
            rc = r.get('repo_count', '')
            bar = self._hbar(tb, max_bytes, bar_w)
            color = palette[idx % len(palette)] if idx < 6 else self.curses.A_DIM
            self._put(4 + i, 3, f'{lang:<18} {bar:<{bar_w}} {tb:>12,} ({pct:>5.2f}%) {rc:>3}', color)

    def _render_freq_view(self, avail, max_x):
        curses = self.curses
        freq = self.data.code_freq
        if not freq:
            self._put(2, 1, 'Code Frequency (Weekly Commits)',
                      curses.color_pair(6) | self.curses.A_BOLD)
            msg = [
                'No commit-frequency data available.',
                '',
                "GitHub's stats endpoints (commit_activity / code_frequency) return",
                'HTTP 202 while GitHub computes them in the background. fetch-github.sh',
                'now warms these up early and collects them later in the same run, so a',
                'single  make fetch  should populate this. If it stays empty, the repos',
                'genuinely have no commits in the tracked window (or try one more fetch).',
            ]
            for i, line in enumerate(msg):
                self._put(4 + i, 3, line, self.curses.A_DIM)
            return

        # Aggregate commits per ISO week across all repos -> time-series chart.
        by_week = defaultdict(int)
        for r in freq:
            by_week[r.get('week_label', '')] += _int(r, 'commits')
        series = [{'label': wl[-5:], 'count': c}
                  for wl, c in sorted(by_week.items()) if wl]
        total = sum(b['count'] for b in series)
        plot_h = max(3, min(10, avail - 8))
        y = self._bar_chart(
            2, series, max_x, plot_h, curses.color_pair(1), bin_unit='w',
            title=f'Code Frequency — weekly commits across all repos ({total:,} total)')
        # Top repos by commits underneath.
        by_repo = defaultdict(int)
        for r in freq:
            by_repo[r.get('repo_name', '')] += _int(r, 'commits')
        top = sorted(by_repo.items(), key=lambda kv: -kv[1])[:8]
        if top and y + 1 < avail:
            self._put(y + 1, 1, 'Most commits (tracked weeks):',
                      curses.color_pair(2) | self.curses.A_BOLD)
            for i, (name, c) in enumerate(top):
                self._put(y + 2 + i, 3, f'{i+1:>2}. {name[:25]:<25} {c:>6,} commits', 0)

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

    def _render_deltas_view(self, avail, max_x):
        curses = self.curses
        self._put(2, 1, 'Cross-Fetch Deltas + PR Activity',
                  curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Δ since previous fetch snapshot (stars/forks/clones/views); '
                  f'PRs opened/merged in {TF_LABEL.get(self.timeframe, "window")} (t/T cycles).',
                  self.curses.A_DIM)
        t = self.data.totals
        if not t or 'repo_snapshots' not in t:
            self._put(5, 1, 'No rolling totals. Run  make totals  first.', self.curses.A_DIM)
            return
        prc = self._windowed_pr_counts()
        # Header
        self._put(5, 3, f'{"repo":<26}{"Δstars":>8}{"Δforks":>8}{"Δclones":>9}{"Δviews":>9}'
                  f'{"+PRopen":>8}{"+PRmrg":>7}',
                  curses.color_pair(2) | self.curses.A_BOLD)
        scroll = self.scroll
        snap_list = sorted(
            t['repo_snapshots'].items(),
            key=lambda x: (abs(x[1].get('clones_delta', 0)) + abs(x[1].get('views_delta', 0)),
                           abs(x[1].get('stars_delta', 0))),
            reverse=True)
        shown = 0
        for name, snap in snap_list[scroll:]:
            sd = snap.get('stars_delta', 0)
            fd = snap.get('forks_delta', 0)
            cd = snap.get('clones_delta', 0)
            vd = snap.get('views_delta', 0)
            pr = prc.get(name, {'opened': 0, 'merged': 0})
            po, pm = pr['opened'], pr['merged']
            if sd == fd == cd == vd == 0 and po == 0 and pm == 0:
                continue
            color = curses.color_pair(1) if sd > 0 else (curses.color_pair(4) if sd < 0 else 0)
            self._put(6 + shown, 3, f'{name[:25]:<26}', 0)
            self._put(6 + shown, 29, f'{sd:>+8d}', color)
            self._put(6 + shown, 37, f'{fd:>+8d}', 0)
            self._put(6 + shown, 45, f'{cd:>+9d}', curses.color_pair(2) if cd else self.curses.A_DIM)
            self._put(6 + shown, 54, f'{vd:>+9d}', curses.color_pair(2) if vd else self.curses.A_DIM)
            self._put(6 + shown, 63, f'{po:>8d}', curses.color_pair(3) if po else self.curses.A_DIM)
            self._put(6 + shown, 71, f'{pm:>7d}', curses.color_pair(1) if pm else self.curses.A_DIM)
            shown += 1
            if shown >= avail - 4:
                break

    def _render_anomaly_view(self, avail, max_x):
        curses = self.curses
        severities = ('all', 'extreme', 'significant', 'minor')
        title = f'Traffic Anomalies  [filter: {severities[self.anomaly_filter]}]'
        self._put(2, 1, title, curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Z = modified z-score vs the repo\'s median day (|Z|>3 extreme, '
                  '>2 significant, >1 minor). Δ% = size of the jump.', self.curses.A_DIM)
        scroll = self.scroll
        if not self.data.anomaly_data:
            self._put(5, 1, 'No anomaly data. Run  make traffic-anomaly  first.', self.curses.A_DIM)
            return
        data = self._filtered_anomaly_data()
        if not data:
            self._put(5, 1, f'No {severities[self.anomaly_filter]} anomalies.', self.curses.A_DIM)
            return
        severity_colors = {
            'extreme': curses.color_pair(4),
            'significant': curses.color_pair(2),
            'minor': curses.color_pair(3),
        }
        # Severity legend at the top (plan 13) — stays visible while scrolling.
        self._put(4, 3, 'severity: ', self.curses.A_DIM)
        self._put(4, 13, 'extreme', severity_colors['extreme'])
        self._put(4, 22, 'significant', severity_colors['significant'])
        self._put(4, 35, 'minor', severity_colors['minor'])
        name_w = min(20, max(12, max_x - 60))
        self._put(5, 3, f'  {"repo":<{name_w}} {"metric":>7} {"severity":<12} {"dir":>5} '
                  f'{"Z":>7} {"date":>7} {"Δ%":>9}',
                  curses.color_pair(2) | self.curses.A_BOLD)
        shown = avail - 4
        for i, row in enumerate(data[scroll:scroll + shown]):
            name = row.get('repo_name', '')[:name_w]
            metric = row.get('metric', '')
            atype = row.get('anomaly_type', '')
            direction = row.get('direction', '')
            z = _float(row, 'modified_z')
            dev = _float(row, 'deviation_pct')
            when = _short_date(row.get('timestamp', ''))
            c = severity_colors.get(atype, 0)
            self._put(6 + i, 3, f'  {name:<{name_w}} {metric:>7} {atype:<12} {direction:>5} '
                      f'{z:>7.2f} {when:>7} {dev:>+8.0f}%', c)
        self.scroll_indicator(2, max_x, len(data), shown)

    PROFILE_SORT_KEYS = (
        ('score', lambda r: float(r.get('clone_intent_score', 0) or 0), True),
        ('ratio', lambda r: float(r.get('clone_to_view_ratio', 0) or 0), True),
        ('name', lambda r: r.get('repo_name', '').lower(), False),
    )

    def _render_profile_view(self, avail, max_x):
        curses = self.curses
        sort_label, sort_key, sort_desc = self.PROFILE_SORT_KEYS[self.profile_sort]
        low_tag = 'low hidden' if self.profile_hide_low else 'l=hide low'
        title = f'Clone Intent Profiles  [sort: {sort_label}]  [{low_tag}]'
        self._put(2, 1, title, curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, 'Score = clones/views x100 (0-200). Labels: developer >=50, '
                  'tooling >=20, reference <20; low-signal = <10 total traffic.',
                  self.curses.A_DIM)
        scroll = self.scroll
        data = self._filtered(self.data.profile_data)
        if not data:
            self._put(5, 1, 'No profile data. Run  make traffic-profile  first.', self.curses.A_DIM)
            return
        data = sorted(data, key=sort_key, reverse=sort_desc)
        active = [r for r in data if r.get('intent_label') != 'low-signal']
        low = [r for r in data if r.get('intent_label') == 'low-signal']
        # Build a flat entry list with section dividers so scrolling still works.
        entries = []                      # (kind, payload): 'div' str | 'row' dict
        if active:
            entries.append(('div', f'── active signal ({len(active)}) ──'))
            entries += [('row', r) for r in active]
        if low and not self.profile_hide_low:
            entries.append(('div', f'── low signal ({len(low)}) ──'))
            entries += [('row', r) for r in low]
        intent_colors = {
            'developer': curses.color_pair(1),
            'tooling': curses.color_pair(3),
            'reference': curses.color_pair(6),   # normal, no longer dim (plan 13)
            'low-signal': curses.A_DIM,
        }
        name_w = min(20, max(12, max_x - 55))
        bar_w = max(10, max_x - name_w - 36)
        self._put(5, 3, f'  {"repo":<{name_w}} {"score":>6} {"intent":<12} {"c/v":>5}  bar',
                  curses.color_pair(2) | self.curses.A_BOLD)
        shown = avail - 4
        for i, (kind, payload) in enumerate(entries[scroll:scroll + shown]):
            if kind == 'div':
                self._put(6 + i, 3, payload, self.curses.A_DIM)
                continue
            row = payload
            name = row.get('repo_name', '')[:name_w]
            score = _float(row, 'clone_intent_score')
            label = row.get('intent_label', '')
            ratio = _float(row, 'clone_to_view_ratio')
            c = intent_colors.get(label, 0)
            bar = self._hbar(score, 200, bar_w)
            self._put(6 + i, 3, f'  {name:<{name_w}} {score:>6.1f} [{label:<10}] {ratio:>5.2f}  {bar}', c)
        self.scroll_indicator(2, max_x, len(entries), shown)

    @staticmethod
    def _corr_tier(r):
        """0 strong (|r|>=0.8), 1 moderate (0.5-0.8), 2 weak (<0.5)."""
        a = abs(r)
        if a >= 0.8:
            return 0
        if a >= 0.5:
            return 1
        return 2

    def _render_correlation_view(self, avail, max_x):
        curses = self.curses
        self._put(2, 1, 'Cross-Repo Correlations', curses.color_pair(6) | self.curses.A_BOLD)
        self._put(3, 1, "Pearson r of two repos' aligned daily clone series over the "
                  "days both have data (n>=5). +1 move together, -1 opposite; lag = who leads.",
                  self.curses.A_DIM)
        # Strength-tier legend at top (plan 13) — stays visible while scrolling.
        self._put(4, 3, 'strength:', self.curses.A_DIM)
        self._put(4, 13, 'strong>=0.8', curses.color_pair(1))
        self._put(4, 26, 'moderate 0.5-0.8', curses.color_pair(6))
        self._put(4, 44, 'weak<0.5', self.curses.A_DIM)
        scroll = self.scroll
        data = self.data.correlation_data
        if not data:
            self._put(6, 1, 'No correlation data — needs >=2 repos sharing >=5 days of '
                      'traffic. Run  make analyze  first.', self.curses.A_DIM)
            return
        # Tier first (strong > moderate > weak), then |r| desc within tier —
        # weak/"white" pairs always sink to the bottom (plan 13).
        data = sorted(data, key=lambda r: (self._corr_tier(_float(r, 'clone_clone_corr')),
                                           -abs(_float(r, 'clone_clone_corr'))))
        name_w = min(18, max(10, (max_x - 64) // 2))
        self._put(5, 3, f'  {"repo A":<{name_w}}     {"repo B":<{name_w}} '
                  f'{"r_clone":>8} {"r_view":>8} {"n":>4}  lag',
                  curses.color_pair(2) | self.curses.A_BOLD)
        shown = avail - 4
        for i, row in enumerate(data[scroll:scroll + shown]):
            ra = row.get('repo_a', '')[:name_w]
            rb = row.get('repo_b', '')[:name_w]
            cc = _float(row, 'clone_clone_corr')
            vc = _float(row, 'view_view_corr')
            n = row.get('share_days', '') or row.get('both_active_days', '') or ''
            lag = row.get('cross_lag_best', '0')
            lag_dir = row.get('cross_lag_direction', '')
            tier = self._corr_tier(cc)
            if tier == 0:
                c = curses.color_pair(1) if cc > 0 else curses.color_pair(4)
            elif tier == 1:
                c = curses.color_pair(6) if cc > 0 else curses.color_pair(3)
            else:
                c = self.curses.A_DIM
            self._put(6 + i, 3,
                      f'  {ra:<{name_w}} <-> {rb:<{name_w}} {cc:>8.3f} {vc:>8.3f} {str(n):>4}  {lag}({lag_dir})', c)
        self.scroll_indicator(2, max_x, len(data), shown)

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

    def _funnel_lines(self):
        """(category_rows, path_rows) for the funnel view: per-category
        aggregates first, top pages beneath (plan 08 phase 5)."""
        data = self._filtered(self.data.funnel_data)
        cats = self._funnel_categories()
        if self.funnel_filter > 0 and self.funnel_filter <= len(cats):
            target = cats[self.funnel_filter - 1]
            data = [r for r in data if r.get('category') == target]
        agg = {}
        for row in data:
            cat = row.get('category', '') or 'other'
            a = agg.setdefault(cat, {'views': 0, 'pages': 0, 'top': ('', 0)})
            views = _int(row, 'view_count')
            a['views'] += views
            a['pages'] += 1
            if views > a['top'][1]:
                a['top'] = ((row.get('title') or row.get('path') or ''), views)
        cat_rows = sorted(agg.items(), key=lambda kv: -kv[1]['views'])
        path_rows = sorted(data, key=lambda r: _int(r, 'view_count'), reverse=True)
        return cat_rows, path_rows

    def _render_funnel_view(self, avail, max_x):
        curses = self.curses
        cats = self._funnel_categories()
        filt = ('all' if self.funnel_filter == 0 or self.funnel_filter > len(cats)
                else cats[self.funnel_filter - 1])
        self._put(2, 1, f'Content Engagement Funnel  [f]ilter: {filt}',
                  curses.color_pair(6) | self.curses.A_BOLD)
        # Color legend
        self._put(3, 1, 'legend:', self.curses.A_DIM)
        legend = [('code', 1), ('docs', 3), ('PRs', 6), ('tree', 2),
                  ('issues', 7), ('insights', 4), ('other', 0)]
        x = 10
        for name, pair in legend:
            attr = curses.color_pair(pair) if pair else self.curses.A_DIM
            self._put(3, x, '█', attr)
            self._put(3, x + 1, name, self.curses.A_DIM)
            x += len(name) + 3
        cat_rows, path_rows = self._funnel_lines()
        if not path_rows:
            self._put(5, 1, 'No funnel data. Run  make traffic-funnel  first.', self.curses.A_DIM)
            return
        y = 5
        max_cat = max((a['views'] for _, a in cat_rows), default=1) or 1
        self._put(y, 3, f'{"category":<14} {"views":>7} {"pages":>6}  top page',
                  curses.color_pair(2) | self.curses.A_BOLD)
        y += 1
        for cat, a in cat_rows:
            if y >= 2 + avail - 3:
                break
            pair = self.FUNNEL_COLORS.get(cat, 0)
            c = curses.color_pair(pair) if pair else self.curses.A_DIM
            bar = self._hbar(a['views'], max_cat, 12)
            top_page = a['top'][0][: max_x - 60]
            self._put(y, 3, f'{cat:<14} {a["views"]:>7} {a["pages"]:>6}  {bar:<12} {top_page}', c)
            y += 1
        y += 1
        name_w = min(18, max(10, (max_x - 70) // 2))
        max_v = max((_int(r, 'view_count') for r in path_rows), default=1) or 1
        self._put(y, 3, f'  {"repo":<{name_w}} {"category":<14} {"views":>6}  page',
                  curses.color_pair(2) | self.curses.A_BOLD)
        y += 1
        title_w = max(0, max_x - (5 + name_w + 1 + 14 + 1 + 6 + 2 + 12))
        scroll = self.scroll
        shown = max(0, 2 + avail - y)
        for i, row in enumerate(path_rows[scroll:scroll + shown]):
            name = row.get('repo_name', '')[:name_w]
            cat = row.get('category', '')
            views = _int(row, 'view_count')
            title = (row.get('title') or row.get('path') or '')[:title_w]
            pair = self.FUNNEL_COLORS.get(cat, 0)
            c = curses.color_pair(pair) if pair else self.curses.A_DIM
            bar = self._hbar(views, max_v, 10)
            self._put(y + i, 3, f'  {name:<{name_w}} {cat:<14} {views:>6}  {bar:<10} {title}', c)
        self.scroll_indicator(2, max_x, len(path_rows), shown)

    def _render_history_view(self, avail, max_x):
        curses = self.curses
        clones = self.data.history_series('clones')
        views = self.data.history_series('views')
        coverage = self.data.history.get('coverage', [])
        cov = '  '.join(f'{a}→{b}' for a, b in coverage) if coverage else 'n/a'
        self._put(2, 1, f'History Store  (coverage: {cov})',
                  curses.color_pair(6) | self.curses.A_BOLD)
        if not clones and not views:
            self._put(4, 3, 'No history store yet. Run  make history  first.', self.curses.A_DIM)
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

    def _render_footer(self, max_y, max_x):
        curses = self.curses
        active = curses.color_pair(5)
        dim = self.curses.A_DIM
        x = 1
        items = [
            ('[q]uit', dim),
            ('[r]eload', dim),
            ('[1-9,0,-,=]view', dim),
            (f'[t/T]tf:{self.timeframe}', active if self.timeframe != '2w' else dim),
            ('[v/V]cycle', dim),
            ('[j/k g/G]scroll', dim),
            (f'[/]{self.search or "search"}', active if self.search else dim),
        ]
        if self.view == 'anomaly':
            severities = ('all', 'extreme', 'significant', 'minor')
            items.append((f'[f]ilter:{severities[self.anomaly_filter]}', active))
        elif self.view == 'profile':
            items.append((f'[s]ort:{self.PROFILE_SORT_KEYS[self.profile_sort][0]}', active))
            items.append(('[l]ow:hidden' if self.profile_hide_low else '[l]ow:shown', active))
        elif self.view == 'top':
            items.append((f'[s]criterion:{self._active_criterion()}', active))
        elif self.view == 'table':
            items.append((f'[s]ort:{self.TABLE_SORT_KEYS[self.table_sort][0]}', active))
            items.append((f'[f]ilter:{self.TABLE_STATUS[self.table_status]}', active))
        self.render_footer_items(max_y, items, x=x)


# ---- text mode ---------------------------------------------------------------

def render_text_view(data, view, timeframe='2w'):
    """Print one view as a plain table (plan 08 phase 6)."""
    if view == 'top':
        crits = {}
        for r in data.top_repos_csv:
            crits.setdefault(r.get('sort_criteria', ''), []).append(r)
        for crit, rows in crits.items():
            print(f'== {crit} ==')
            for r in rows[:15]:
                print(f"  {r.get('rank', ''):>3}  {r.get('repo_name', ''):<30} {r.get('value', '')}")
    elif view == 'table':
        for r in sorted(data.repos, key=lambda r: -float(r.get('traffic_score', 0) or 0)):
            print(f"  {r.get('repo_name', ''):<30} clones={_int(r, 'total_clones'):>6} "
                  f"views={_int(r, 'total_views'):>6} score={r.get('traffic_score', '')} "
                  f"[{r.get('status', '')}]")
    elif view == 'lang':
        for r in data.lang_dist:
            print(f"  {r.get('language', ''):<20} {_int(r, 'total_bytes'):>12,} "
                  f"({r.get('total_bytes_pct', '')}%) repos={r.get('repo_count', '')}")
    elif view == 'freq':
        by_week = defaultdict(int)
        for r in data.code_freq:
            by_week[r.get('week_label', '')] += _int(r, 'commits')
        for wl, c in sorted(by_week.items()):
            print(f'  {wl}  {c:>6,}')
    elif view == 'deltas':
        for n, snap in sorted((data.totals.get('repo_snapshots') or {}).items(),
                              key=lambda kv: -abs(kv[1].get('stars_delta', 0))):
            if any(snap.get(k) for k in ('stars_delta', 'forks_delta', 'clones_delta', 'views_delta')):
                print(f"  {n:<30} Δstars={snap.get('stars_delta', 0):>+4} "
                      f"Δclones={snap.get('clones_delta', 0):>+5} Δviews={snap.get('views_delta', 0):>+5}")
    elif view == 'anomaly':
        for r in data.anomaly_data:
            print(f"  {r.get('repo_name', ''):<25} {r.get('metric', ''):>7} "
                  f"{r.get('anomaly_type', ''):<12} z={r.get('modified_z', '')} {r.get('timestamp', '')[:10]}")
    elif view == 'profile':
        for r in data.profile_data:
            print(f"  {r.get('repo_name', ''):<30} score={r.get('clone_intent_score', ''):>7} "
                  f"[{r.get('intent_label', '')}]")
    elif view == 'correlation':
        for r in data.correlation_data[:50]:
            print(f"  {r.get('repo_a', ''):<25} <-> {r.get('repo_b', ''):<25} "
                  f"r_clone={r.get('clone_clone_corr', '')}")
    elif view == 'funnel':
        agg = defaultdict(lambda: {'views': 0, 'pages': 0})
        for r in data.funnel_data:
            cat = r.get('category', '') or 'other'
            agg[cat]['views'] += _int(r, 'view_count')
            agg[cat]['pages'] += 1
        for cat, a in sorted(agg.items(), key=lambda kv: -kv[1]['views']):
            print(f"  {cat:<16} views={a['views']:>7,} pages={a['pages']:>5}")
    elif view == 'history':
        acct = data.history_all
        days = sorted(set(acct['clones']) | set(acct['views']))
        print(f'History store: {len(days)} days, coverage {data.history.get("coverage", [])}')
        for d in days:
            print(f"  {d}  clones={acct['clones'].get(d, 0):>5} views={acct['views'].get(d, 0):>5}")
    else:
        render_text(data, timeframe)


def render_text(data, timeframe):
    """Print everything to stdout — no curses."""
    repos = data.repos
    if not repos:
        print('No repository data found.')
        print('Run make fetch && make analyze first.')
        return

    owner = data.totals.get('owner') or 'github'
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
    parser.add_argument('--view', choices=VIEWS, default='traffic',
                        help='Initial view; with --text, which view to print.')
    args = parser.parse_args(argv)

    from catnip.config import Config, ConfigError
    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f'catnip: config error: {exc}', file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir) if args.run_dir else cfg.latest_run()
    if run_dir is None:
        print('catnip: no runs found. Run `catnip run` first.', file=sys.stderr)
        return 1
    if not (run_dir / 'analysis').is_dir():
        print(f'catnip: {run_dir} has no analysis/ — run `catnip analyze` first.', file=sys.stderr)
        return 1

    data = AnalyticsData(run_dir, cfg.stats_file, cfg.history_file)
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
