#!/usr/bin/env python3
"""The derived-metrics engine, offline.

These are the numbers the operator makes decisions on, so the tests are
written against the failures that actually shipped: windows that mean
different things on different days, a delta that is really a window
artifact, a spike the detector cannot see, a composite that scores
"unknown" as "zero", and a correlation that is one release wave agreeing
with itself.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catnip import derive as D  # noqa: E402


def store(repos, referrers=None, events=None):
    """Build a store dict. repos: {name: {metric: {day: [count, uniques]}}}."""
    out = {"schema_version": 2, "owner": "test", "repos": repos}
    if referrers is not None:
        out["referrers"] = referrers
    if events is not None:
        out["events"] = events
    return out


def days(start_day, n):
    return [f"2026-07-{start_day + i:02d}" for i in range(n)]


def flat(metric, values, start_day=1, uniques=None):
    """{metric: {day: [count, uniq]}} from a list of daily counts."""
    ds = days(start_day, len(values))
    return {metric: {d: [v, (uniques[i] if uniques else v)]
                     for i, (d, v) in enumerate(zip(ds, values, strict=True))}}


class WindowTests(unittest.TestCase):

    def setUp(self):
        self.s = store({"a": flat("clones", list(range(1, 21)))})

    def test_window_is_calendar_not_last_n_observations(self):
        # A night the timer missed must shorten the window, not stretch it
        # across a fortnight while still being labelled "last 7 days".
        gappy = store({"a": {"clones": {"2026-07-01": [1, 1], "2026-07-02": [1, 1],
                                        "2026-07-20": [1, 1]}}})
        win = D.window(gappy, "1w")
        self.assertEqual(win, ["2026-07-20"])

    def test_window_lengths_match_the_selector(self):
        for tf, n in (("1d", 1), ("1w", 7), ("2w", 14)):
            self.assertEqual(len(D.window(self.s, tf)), n, tf)

    def test_all_and_epoch_take_the_whole_store(self):
        self.assertEqual(len(D.window(self.s, "all")), 20)
        self.assertEqual(D.window(self.s, "epoch"), D.window(self.s, "all"))

    def test_previous_window_abuts_and_matches_length(self):
        cur = D.window(self.s, "1w")
        prev = D.previous_window(self.s, "1w")
        self.assertEqual(len(prev), len(cur))
        self.assertLess(prev[-1], cur[0])

    def test_previous_window_is_empty_when_the_store_is_too_short(self):
        short = store({"a": flat("clones", [1, 2, 3])})
        self.assertEqual(D.previous_window(short, "1w"), [])

    def test_empty_store_is_survivable(self):
        self.assertEqual(D.window(store({}), "1w"), [])
        self.assertIsNone(D.latest_day(store({})))
        self.assertEqual(D.deltas(store({}), "1w"), {})


class SeriesTests(unittest.TestCase):

    def test_unobserved_days_are_zero_filled_in_order(self):
        s = store({"a": {"clones": {"2026-07-03": [5, 2]}}})
        self.assertEqual(D.series(s, "a", "clones", days(1, 4)), [0, 0, 5, 0])

    def test_uniques_field_is_separate_from_raw(self):
        s = store({"a": {"clones": {"2026-07-01": [10, 2]}}})
        self.assertEqual(D.total(s, "a", "clones", days(1, 1), D.RAW), 10)
        self.assertEqual(D.total(s, "a", "clones", days(1, 1), D.UNIQ), 2)

    def test_active_repos_excludes_the_silent_ones(self):
        s = store({"loud": flat("clones", [4, 5]), "silent": flat("clones", [0, 0])})
        self.assertEqual(D.active_repos(s, days(1, 2)), ["loud"])


class DeltaTests(unittest.TestCase):

    def test_delta_is_window_minus_previous_window(self):
        s = store({"a": flat("clones", [1] * 7 + [3] * 7)})
        row = D.deltas(s, "1w")["a"]["clones"]
        self.assertEqual((row["cur"], row["prev"], row["delta"]), (21, 7, 14))

    def test_rate_is_per_day_of_the_window(self):
        s = store({"a": flat("clones", [2] * 14)})
        self.assertAlmostEqual(D.deltas(s, "1w")["a"]["clones"]["rate"], 2.0)

    def test_no_second_window_reports_incomparable_not_a_fake_delta(self):
        # The defect this replaces: differencing rolling totals produced a
        # confident number where the honest answer is "not yet".
        s = store({"a": flat("clones", [5] * 5)})
        row = D.deltas(s, "1w")["a"]
        self.assertFalse(row["comparable"])
        self.assertIsNone(row["clones"]["delta"])
        self.assertIsNone(row["clones"]["prev"])

    def test_a_steady_repo_has_no_delta(self):
        # The signature failure of the snapshot diff: a repo where nothing
        # happened showed a large negative change as the window's left edge
        # aged out from under it.
        s = store({"a": flat("clones", [4] * 14)})
        self.assertEqual(D.deltas(s, "1w")["a"]["clones"]["delta"], 0)

    def test_decline_is_reported_as_decline(self):
        s = store({"a": flat("clones", [10] * 7 + [1] * 7)})
        self.assertEqual(D.deltas(s, "1w")["a"]["clones"]["delta"], -63)

    def test_repos_with_no_traffic_in_either_window_are_omitted(self):
        s = store({"a": flat("clones", [1] * 14), "z": flat("clones", [0] * 14)})
        self.assertEqual(sorted(D.deltas(s, "1w")), ["a"])


class ModifiedZTests(unittest.TestCase):

    def test_mad_zero_series_still_scores_its_spike(self):
        # The live failure: one repo sat at zero for eleven days and
        # then took 421 clones. MAD is 0, the textbook formula divides by it
        # and returns 0.00, and the largest event in the account never
        # reached the screen.
        values = [0] * 11 + [421, 120, 0]
        self.assertGreater(max(D.modified_z(values)), 3.0)

    def test_flat_series_has_no_anomalies(self):
        self.assertEqual(D.modified_z([7] * 10), [0.0] * 10)

    def test_ordinary_spike_uses_the_mad_form(self):
        values = [5, 7, 6, 8, 40, 6, 5, 7, 6, 5]
        z = D.modified_z(values)
        self.assertEqual(max(range(len(z)), key=lambda i: z[i]), 4)

    def test_severity_bands(self):
        self.assertEqual(D.severity(4.0), "extreme")
        self.assertEqual(D.severity(-2.5), "significant")
        self.assertEqual(D.severity(1.5), "minor")
        self.assertIsNone(D.severity(0.5))


class AnomalyGridTests(unittest.TestCase):

    def test_grid_is_aligned_to_the_window_days(self):
        s = store({"a": flat("clones", [0] * 10 + [50] * 2 + [0, 0])})
        grid = D.anomalies(s, "2w")
        self.assertEqual(len(grid["days"]), 14)
        self.assertTrue(all(len(cells) == 14 for _r, cells in grid["rows"]))

    def test_immaterial_spikes_never_form_a_campaign(self):
        # Twenty repos each taking a single clone on one day is not an
        # account event, however extreme one clone is against a baseline
        # of zero.
        repos = {f"r{i}": flat("clones", [0] * 13 + [1]) for i in range(20)}
        self.assertEqual(D.anomalies(store(repos), "2w")["campaigns"], {})

    def test_material_simultaneous_spikes_collapse_into_one_event(self):
        repos = {f"r{i}": flat("clones", [0] * 13 + [40]) for i in range(6)}
        campaigns = D.anomalies(store(repos), "2w")["campaigns"]
        self.assertEqual(list(campaigns), ["2026-07-14"])
        self.assertEqual(len(campaigns["2026-07-14"]), 6)

    def test_a_lone_spike_is_not_a_campaign(self):
        repos = {"a": flat("clones", [0] * 13 + [90]),
                 "b": flat("clones", [3] * 14)}
        self.assertEqual(D.anomalies(store(repos), "2w")["campaigns"], {})

    def test_cell_names_the_metric_that_drove_it(self):
        s = store({"a": dict(flat("clones", [1] * 13 + [2]),
                             **flat("views", [0] * 13 + [80]))})
        cells = D.anomalies(s, "2w")["rows"][0][1]
        self.assertEqual(cells[-1]["metric"], "views")


class AudienceTests(unittest.TestCase):

    def _repo(self, clones, cloner_uniq, views, visitor_uniq, n=14):
        return {"clones": {d: [clones // n, cloner_uniq // n]
                           for d in days(1, n)},
                "views": {d: [views // n, visitor_uniq // n] for d in days(1, n)}}

    def test_fetcher_fleet_and_real_audience_land_on_opposite_labels(self):
        # Calibrated against the live account: a repo cloned 38x per unique
        # visitor is a fetcher fleet; one cloned ~1.5x per visitor is people.
        s = store({"crawler": self._repo(560, 112, 140, 3),
                   "audience": self._repo(448, 196, 980, 126)})
        rows = D.audience(s, "2w")
        self.assertEqual(rows["crawler"]["label"], "crawler")
        self.assertEqual(rows["audience"]["label"], "audience")
        self.assertLess(rows["crawler"]["score"], rows["audience"]["score"])

    def test_missing_components_are_dropped_not_scored_zero(self):
        # No referrer history in the store must not read as "reached from
        # nowhere", which is what a zero would claim.
        s = store({"a": self._repo(140, 70, 280, 70)})
        row = D.audience(s, "2w")["a"]
        self.assertIn("referrers", row["missing"])
        self.assertIsNone(row["scores"]["referrers"])
        self.assertGreater(row["score"], 0.0)

    def test_referrer_history_is_used_when_present(self):
        s = store({"a": self._repo(140, 70, 280, 70)},
                  referrers={"a": {"2026-07-05": {"github.com": [9, 4],
                                                  "news.example": [3, 3],
                                                  "t.co": [2, 2]}}})
        row = D.audience(s, "2w")["a"]
        self.assertEqual(row["referrer_diversity"], 3)
        self.assertNotIn("referrers", row["missing"])

    def test_depth_is_withheld_when_there_are_too_few_visitors(self):
        s = store({"a": self._repo(140, 70, 280, 14)})
        self.assertIn("depth", D.audience(s, "1d")["a"]["missing"])

    def test_depth_penalises_both_tails(self):
        # 150 views from 2 unique visitors is one client hammering, not
        # deep reading, and must not score as engaged browsing.
        self.assertIsNone(D._human_depth(150, 2))
        self.assertLess(D._human_depth(1500, 5), 0.5)
        self.assertEqual(D._human_depth(20, 5), 1.0)

    def test_burst_is_withheld_on_a_window_too_short_to_have_shape(self):
        s = store({"a": self._repo(14, 14, 14, 14)})
        self.assertIn("burst", D.audience(s, "1d")["a"]["missing"])

    def test_too_little_traffic_is_unclassifiable_not_mixed(self):
        # Five unique cloners and two unique visitors is four data points.
        # Labelling that "mixed" reports a finding where there is only an
        # absence of one — and "mixed" then stops meaning anything, because
        # most of the account lands in it.
        tiny = {"clones": {d: [0, 0] for d in days(1, 14)},
                "views": {d: [0, 0] for d in days(1, 14)}}
        tiny["clones"]["2026-07-12"] = [4, 4]
        tiny["clones"]["2026-07-13"] = [1, 1]
        tiny["views"]["2026-07-12"] = [17, 1]
        tiny["views"]["2026-07-13"] = [3, 1]
        row = D.audience(store({"gamble": tiny}), "2w")["gamble"]
        self.assertEqual(row["label"], "low-signal")
        self.assertLess(row["signal"], D.AUDIENCE_MIN_SIGNAL)

    def test_low_signal_agrees_with_the_intent_view(self):
        # Two views describing the same repo must not disagree about
        # whether there is enough traffic to describe it.
        self.assertEqual(D.AUDIENCE_MIN_SIGNAL, D.INTENT_LOW_SIGNAL)

    def test_mixed_survives_for_repos_with_real_traffic(self):
        # 'mixed' must still be reachable, and mean what it says: the
        # components disagree. Shaped like the live account's middle case —
        # cloned about four times per visitor (fetcher-ish) but browsed
        # properly and concentrated on one day (human-ish).
        repo = {"clones": {d: [1, 1] for d in days(1, 14)},
                "views": {d: [4, 0] for d in days(1, 14)}}
        repo["clones"]["2026-07-14"] = [42, 15]
        repo["views"]["2026-07-14"] = [4, 7]
        row = D.audience(store({"m": repo}), "2w")["m"]
        self.assertEqual(row["label"], "mixed", row["scores"])
        self.assertGreaterEqual(row["signal"], D.AUDIENCE_MIN_SIGNAL)
        # It is 'mixed' because the components disagree, not because they
        # all landed mid-scale.
        present = [v for v in row["scores"].values() if v is not None]
        self.assertGreater(max(present) - min(present), 0.4)

    def test_classification_never_filters_the_store(self):
        s = store({"crawler": self._repo(560, 112, 140, 3),
                   "audience": self._repo(448, 196, 980, 126)})
        self.assertEqual(sorted(D.audience(s, "2w")), ["audience", "crawler"])


class IntentTests(unittest.TestCase):

    def _repo(self, cloners, visitors):
        return {"clones": {"2026-07-01": [cloners * 3, cloners]},
                "views": {"2026-07-01": [visitors * 3, visitors]}}

    def test_smoothing_separates_small_n_from_large_n(self):
        # Unsmoothed, both of these are exactly 1.00 and the top of the
        # ranking is noise.
        s = store({"small": self._repo(7, 7), "large": self._repo(541, 541)})
        rows = D.intent(s, "1d")
        self.assertNotEqual(round(rows["small"]["score"], 3),
                            round(rows["large"]["score"], 3))
        self.assertLess(rows["small"]["score"], rows["large"]["score"])

    def test_score_is_uncapped(self):
        s = store({"a": self._repo(400, 1)})
        self.assertGreater(D.intent(s, "1d")["a"]["score"], 100)

    def test_low_signal_repos_are_labelled_not_ranked(self):
        s = store({"a": self._repo(2, 1)})
        self.assertEqual(D.intent(s, "1d")["a"]["label"], "low-signal")

    def test_uses_uniques_not_raw_counts(self):
        # Same uniques, wildly different raw counts: the score must not move.
        a = {"clones": {"2026-07-01": [1000, 20]}, "views": {"2026-07-01": [30, 10]}}
        b = {"clones": {"2026-07-01": [20, 20]}, "views": {"2026-07-01": [30, 10]}}
        rows = D.intent(store({"a": a, "b": b}), "1d")
        self.assertEqual(rows["a"]["score"], rows["b"]["score"])

    def test_crawler_scoring_developer_is_flagged_as_a_conflict(self):
        # Shaped like the live fetcher fleet: silent, then one enormous day,
        # cloned dozens of times per unique visitor.
        crawler = {"clones": {d: [0, 0] for d in days(1, 13)},
                   "views": {d: [0, 0] for d in days(1, 13)}}
        crawler["clones"]["2026-07-14"] = [541, 114]
        crawler["views"]["2026-07-14"] = [150, 3]
        rows = D.intent(store({"c": crawler}), "2w")
        self.assertEqual(rows["c"]["label"], "developer")
        self.assertEqual(rows["c"]["audience_label"], "crawler")
        self.assertTrue(rows["c"]["conflict"])

    def test_a_steady_high_ratio_fetcher_is_not_forced_to_crawler(self):
        # Burst as specified ("crawlers land on push day, people spread")
        # reads a steady automated poller as human-shaped, so the composite
        # lands mid-scale rather than at either extreme. Recorded because
        # it is a real limit of the metric, not an accident.
        steady = {"clones": {d: [40, 8] for d in days(1, 14)},
                  "views": {d: [10, 1] for d in days(1, 14)}}
        row = D.audience(store({"c": steady}), "2w")["c"]
        self.assertEqual(row["label"], "mixed")
        self.assertLess(row["scores"]["ratio"], 0.4)


class CoupledTests(unittest.TestCase):

    def test_a_shared_release_spike_does_not_survive_residualization(self):
        # Two repos whose only common movement is the account-wide wave.
        wave = [1] * 10 + [100] + [1] * 3
        s = store({"a": flat("clones", wave), "b": flat("clones", wave),
                   "c": flat("clones", wave)})
        pairs = D.coupled(s, "2w")["pairs"]
        self.assertTrue(all(abs(p["r"]) < abs(p["raw_r"]) or p["raw_r"] == 0
                            for p in pairs), pairs)

    def test_too_few_days_refuses_with_a_reason(self):
        s = store({"a": flat("clones", [5, 5, 5]), "b": flat("clones", [5, 5, 5])})
        result = D.coupled(s, "1d")
        self.assertEqual(result["pairs"], [])
        self.assertIn("aligned days", result["reason"])

    def test_lag_is_suppressed_until_the_store_is_deep_enough(self):
        short = store({"a": flat("clones", list(range(14))),
                       "b": flat("clones", list(range(14)))})
        self.assertFalse(D.coupled(short, "2w")["lag_available"])
        long = store({"a": {"clones": {f"2026-{6 + i // 30:02d}-{i % 30 + 1:02d}":
                                       [i, i] for i in range(40)}}})
        self.assertTrue(D.coupled(long, "all")["lag_available"])

    def test_near_silent_repos_are_not_paired(self):
        s = store({"loud1": flat("clones", [9] * 14), "loud2": flat("clones", [8] * 14),
                   "quiet": flat("clones", [0] * 13 + [1])})
        named = {r for p in D.coupled(s, "2w")["pairs"] for r in (p["a"], p["b"])}
        self.assertNotIn("quiet", named)

    def test_coupled_for_returns_adjacency_for_one_repo(self):
        s = store({"a": flat("clones", [1, 9, 2, 8, 3, 7, 4, 6, 5, 5, 6, 4, 7, 3]),
                   "b": flat("clones", [9, 1, 8, 2, 7, 3, 6, 4, 5, 5, 4, 6, 3, 7]),
                   "c": flat("clones", [5] * 14)})
        for row in D.coupled_for(s, "2w", "a"):
            self.assertNotEqual(row["repo"], "a")


class FunnelDepthTests(unittest.TestCase):

    ROWS = [
        {"repo_name": "leather", "category": "overview", "view_count": "100"},
        {"repo_name": "leather", "category": "doc_blob", "view_count": "140"},
        {"repo_name": "leather", "category": "code_blob", "view_count": "60"},
        {"repo_name": "bounce", "category": "overview", "view_count": "300"},
        {"repo_name": "bounce", "category": "doc_blob", "view_count": "10"},
    ]

    def test_depth_ratio_separates_reading_from_bouncing(self):
        rows = D.funnel_depth(self.ROWS)
        self.assertAlmostEqual(rows["leather"]["depth_ratio"], 2.0)
        self.assertGreater(rows["leather"]["depth_ratio"], 1.0)
        self.assertLess(rows["bounce"]["depth_ratio"], 1.0)

    def test_uniques_are_summed_per_page_and_documented_as_an_upper_bound(self):
        # GitHub gives uniques per path with no way to dedupe a person
        # across paths, so a reader who opened three pages counts three
        # times. The number is still useful next to views; the derivation
        # has to say what it is.
        rows = [
            {"repo_name": "r", "category": "overview", "view_count": "100",
             "unique_visitors": "37"},
            {"repo_name": "r", "category": "doc_blob", "view_count": "88",
             "unique_visitors": "59"},
        ]
        self.assertEqual(D.funnel_depth(rows)["r"]["uniq"], 96)
        text = "\n".join(D.DERIVATIONS["funnel"])
        self.assertIn("upper bound", text)

    def test_missing_uniques_are_zero_not_an_exception(self):
        rows = [{"repo_name": "r", "category": "overview", "view_count": "5"}]
        self.assertEqual(D.funnel_depth(rows)["r"]["uniq"], 0)

    def test_no_overview_does_not_divide_by_zero(self):
        rows = D.funnel_depth([{"repo_name": "x", "category": "doc_blob",
                                "view_count": "5"}])
        self.assertEqual(rows["x"]["depth_ratio"], 5.0)

    def test_unparsable_counts_are_zero_not_an_exception(self):
        rows = D.funnel_depth([{"repo_name": "x", "category": "overview",
                                "view_count": "n/a"}])
        self.assertEqual(rows["x"]["total"], 0)


class EventTests(unittest.TestCase):

    STORE = store({"a": flat("clones", [1] * 14)},
                  events={"a": {"releases": {"v1": "2026-07-05", "v0": "2026-06-01"},
                                "pushes": {"2026-07-05": 3, "2026-06-02": 9}}})

    def test_only_events_inside_the_window_are_returned(self):
        events = D.events_in(self.STORE, "a", days(1, 14))
        self.assertEqual(events["releases"], [("2026-07-05", "v1")])
        self.assertEqual(events["pushes"], {"2026-07-05": 3})

    def test_a_repo_with_no_events_is_empty_not_an_error(self):
        events = D.events_in(self.STORE, "unknown", days(1, 14))
        self.assertEqual(events, {"releases": [], "pushes": {}})

    def test_referrers_absent_from_the_store_is_none_not_empty(self):
        # "catnip never collected this" and "this repo has no referrers"
        # are different claims and must stay distinguishable.
        self.assertIsNone(D.referrers_in(self.STORE, "a", days(1, 14)))


class DerivationTests(unittest.TestCase):

    def test_every_derived_view_can_explain_itself(self):
        for view in ("audience", "deltas", "anomaly", "profile", "correlation",
                     "funnel", "table", "traffic", "drilldown"):
            self.assertIn(view, D.DERIVATIONS, view)
            self.assertGreater(len(D.DERIVATIONS[view]), 4, view)

    def test_thresholds_in_the_text_track_the_constants(self):
        # An overlay that documents a threshold the code no longer uses is
        # worse than no overlay.
        text = "\n".join(D.DERIVATIONS["profile"])
        self.assertIn(str(D.INTENT_BANDS[0][0]), text)
        self.assertIn(str(D.INTENT_LOW_SIGNAL), text)
        anomaly = "\n".join(D.DERIVATIONS["anomaly"])
        self.assertIn(str(D.Z_MIN_VALUE), anomaly)
        self.assertIn(str(D.CAMPAIGN_MIN_REPOS), anomaly)

    def test_unknown_view_gets_an_honest_placeholder(self):
        self.assertTrue(any("no derivation" in line.lower()
                            for line in D.derivation("nonesuch")))


if __name__ == "__main__":
    unittest.main()


class FunnelClassificationTests(unittest.TestCase):
    """The repo landing page must classify as `overview`.

    GitHub's popular-paths endpoint reports it as "/owner/repo"; the
    classifier matched only "/" and the bare "/tree/main" forms, so no
    path on any repo was ever an overview. depth_ratio is deep views over
    overview views, which made it a bare count of deep views across the
    whole account — and every repo displayed as though nobody had ever
    landed on its front page.
    """

    def setUp(self):
        from catnip.traffic_funnel import classify_path
        self.classify = classify_path

    def test_repo_root_is_the_front_door(self):
        self.assertEqual(self.classify("/octocat/hello-world"), "overview")
        self.assertEqual(self.classify("/owner/repo/"), "overview")

    def test_default_branch_listing_is_still_the_front_door(self):
        self.assertEqual(self.classify("/owner/repo/tree/main"), "overview")
        self.assertEqual(self.classify("/owner/repo/tree/master"), "overview")

    def test_a_subdirectory_is_not_the_front_door(self):
        self.assertEqual(self.classify("/owner/repo/tree/main/examples"), "dir_tree")

    def test_deeper_paths_keep_their_categories(self):
        for path, want in (
            ("/owner/repo/blob/main/README.md", "doc_blob"),
            ("/owner/repo/blob/main/main.go", "code_blob"),
            ("/owner/repo/pulls", "pr_list"),
            ("/owner/repo/pull/12", "pr_detail"),
            ("/owner/repo/pulse", "pulse"),
            ("/owner/repo/graphs/traffic", "traffic_graph"),
        ):
            self.assertEqual(self.classify(path), want, path)

    def test_depth_ratio_is_meaningful_once_overviews_exist(self):
        rows = [
            {"repo_name": "r", "category": self.classify(p), "view_count": str(n)}
            for p, n in (("/o/r", 100), ("/o/r/blob/main/DOC.md", 140),
                         ("/o/r/blob/main/x.go", 60))
        ]
        self.assertAlmostEqual(D.funnel_depth(rows)["r"]["depth_ratio"], 2.0)


class MomentumTests(unittest.TestCase):
    """Level says how much; slope says whether it is still happening.

    A repo that spiked once and stopped and a repo climbing steadily can
    show the same delta, and the deltas column cannot tell them apart.
    """

    def test_first_difference_is_aligned_with_its_days(self):
        self.assertEqual(D.first_difference([5, 7, 4]), [0, 2, -3])

    def test_first_difference_of_a_flat_series_is_zero(self):
        self.assertEqual(D.first_difference([3, 3, 3]), [0, 0, 0])

    def test_slope_signs_the_direction(self):
        self.assertGreater(D.trend_slope([1, 2, 3, 4]), 0)
        self.assertLess(D.trend_slope([4, 3, 2, 1]), 0)
        self.assertEqual(D.trend_slope([2, 2, 2, 2]), 0.0)

    def test_slope_is_per_step(self):
        self.assertAlmostEqual(D.trend_slope([0, 2, 4, 6]), 2.0)

    def test_degenerate_series_have_no_direction(self):
        self.assertEqual(D.trend_slope([]), 0.0)
        self.assertEqual(D.trend_slope([7]), 0.0)

    def test_a_spike_that_stopped_reads_as_falling(self):
        # The distinction the view exists for: same total, opposite story.
        spiked = store({"a": flat("clones", [0, 0, 90, 1, 1, 1, 1])})
        climbing = store({"a": flat("clones", [1, 4, 8, 14, 21, 22, 24])})
        self.assertLess(D.momentum(spiked, "a", "1w")["clones"]["slope"], 0)
        self.assertGreater(D.momentum(climbing, "a", "1w")["clones"]["slope"], 0)

    def test_falling_but_decelerating_is_expressible(self):
        # A drop that is levelling off: negative slope, positive accel.
        s = store({"a": flat("clones", [50, 30, 18, 11, 7, 5, 4])})
        m = D.momentum(s, "a", "1w")["clones"]
        self.assertLess(m["slope"], 0)
        self.assertGreater(m["accel"], 0)

    def test_rate_comparison_is_absent_without_a_previous_window(self):
        s = store({"a": flat("clones", [1] * 5)})
        self.assertIsNone(D.momentum(s, "a", "1w")["clones"]["prev_rate"])

    def test_momentum_covers_both_metrics(self):
        s = store({"a": dict(flat("clones", [1] * 14), **flat("views", [2] * 14))})
        m = D.momentum(s, "a", "2w")
        self.assertIn("clones", m)
        self.assertIn("views", m)
        self.assertEqual(len(m["clones"]["diffs"]), len(m["days"]))
