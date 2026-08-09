#!/usr/bin/env python3
"""Render a markdown analysis report from the durable daily store.

This module is deliberately, boringly deterministic. Every number in the
output comes from `catnip.derive`, and the same store with the same
timeframe produces byte-identical markdown. It contains no inference, no
hypotheses and no adjectives it cannot defend.

That constraint is the point, because this report is the floor the
`catnip-prowl` agent skill stands on. The skill's job is to go further —
form its own hypotheses, test them against the raw data, and surface
things strict determinism gates out — and the only way a reader can trust
the interesting half is if the boring half is reproducible and the two
are visibly labelled. Every finding here is tagged `measured`. The skill
adds `inferred` and `speculative`, and must never quietly promote one to
the other.

The guard exists for the same reason. catnip collects daily, so the store
gains at most one day per run: two reports on the same data are not two
findings, they are the same finding printed twice. The primary gate is
therefore whether the store's latest observed day has ADVANCED since the
last report, with a one-day clock floor as a cheap secondary. Both are
overridable, because a prototype that cannot re-run is not a prototype.

A report is named for the period it covers, not the moment it was written,
and provisionality is in the name:

    reports/2026-08-05-2w.20260807T031722Z/   a day in the window can still
                                              be revised
    reports/2026-08-05-2w/                    none of them can

Recomputations of one period sit side by side and nothing is overwritten:
open the unstamped name for the settled answer, a stamped one to audit
what was believed on a date. `promote` runs on every invocation, because a
period settles about two weeks after the cycle that wrote it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catnip import derive
from catnip.config import Config, ConfigError
from catnip.tui.framework import read_csv

#: Minimum wall-clock gap between reports. Secondary to the store-advance
#: check — a clock says time passed, not that anything happened.
MIN_INTERVAL = timedelta(days=1)

#: Provenance tag for everything this module emits. The skill owns the
#: other two; see the module docstring.
MEASURED = "measured"

STAMP_FMT = "%Y%m%dT%H%M%SZ"

#: `<period>.<write stamp>` — a recomputable report.
PROVISIONAL_RE = re.compile(r"^(?P<name>.+)\.(?P<stamp>\d{8}T\d{6}Z)$")
#: `<covered day>-<timeframe>` — the period a report describes.
NAME_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})-(?P<timeframe>\w+)$")
#: Reports written before periods had names: one directory per write stamp.
LEGACY_RE = re.compile(r"^\d{8}T\d{6}Z$")


# ---- naming ------------------------------------------------------------------

def report_name(store, timeframe):
    """The period a report covers: `<newest day>-<timeframe>`.

    Keyed on the day because that is what promotion turns on, and because
    two reports of one period are one document recomputed. The timeframe
    is in the name because `1w` and `2w` end on the same day.
    """
    days = derive.window(store, timeframe)
    end = days[-1] if days else (derive.settled_day(store) or "unknown")
    return f"{end}-{timeframe}"


def split_name(dir_name):
    """(period, write stamp) for a report directory. Stamp is None when
    the report has been promoted, and period is None for a legacy one."""
    m = PROVISIONAL_RE.match(dir_name)
    if m:
        return m.group("name"), m.group("stamp")
    if LEGACY_RE.match(dir_name):
        return None, dir_name
    return dir_name, None


def previous_reports(reports_dir):
    """Every report directory, oldest write first.

    Sorted by write time, not by name: a promoted report sorts before its
    own superseded stamped siblings, so `[-1]` on names hands the guard an
    older recomputation.
    """
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        return []
    out = []
    for child in sorted(reports_dir.iterdir()):
        if not (child / "report.md").is_file():
            continue
        meta = load_meta(child)
        written = meta.get("last_recomputed") or meta.get("written")
        if not written:
            _, stamp = split_name(child.name)
            written = stamp or child.name
        out.append((written, child.name, child))
    return [child for _, _, child in sorted(out)]


def load_meta(report_dir):
    path = Path(report_dir) / "meta.json"
    if not path.is_file():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def window_digest(store, timeframe):
    """A fingerprint of the numbers a report for `timeframe` would state.

    GitHub keeps adding counts to a day for well over a day after it closes,
    and it does not always finish inside `derive.settle_hours()` — that
    constant is a measurement, not a guarantee GitHub has made. When a late
    arrival lands on a day a report already covered, the store is corrected
    and the report is not: it keeps asserting a number the store no longer
    holds, and nothing ever revisits it, because the only question the guard
    used to ask was whether a *new* day had appeared.

    This is the answer to "has anything I already said changed?". It covers
    the window's days and every per-repo total in them, so a single repo's
    backfilled day is enough to mark the report stale.
    """
    days = derive.window(store, timeframe)
    if not days:
        return None
    parts = [",".join(days)]
    for repo in sorted(derive.repo_names(store)):
        for metric in derive.METRICS:
            parts.append(f"{repo}:{metric}:"
                         f"{derive.total(store, repo, metric, days)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def guard(reports_dir, store, now=None, timeframe="2w"):
    """(ok, reason). False means running again would say nothing new.

    Store-advance is the real test: if the newest day catnip has observed
    has not moved since the last report, the inputs are identical and the
    output would be too, however long you waited. The clock floor is a
    cheap second opinion for the case where a store gains a day every few
    hours (a backfill, a rebuild) and the analysis has not settled.

    The day compared is the settled one. Every fetch adds its own date to
    the store whether or not GitHub has counted it, so comparing newest
    days meant the store "advanced" on any day a fetch ran — the guard
    passed on a bucket with nothing in it.

    A new day is not the only thing worth reporting. A day already covered
    can be revised upward days later, and a report that stated the old
    number stays wrong forever unless something notices. So when the day
    has not moved, the window's own numbers are compared against the digest
    the last report recorded, and a difference is grounds to write again.
    """
    now = now or datetime.now(timezone.utc)
    prior = previous_reports(reports_dir)
    if not prior:
        return True, "no previous report"
    meta = load_meta(prior[-1])
    latest = derive.settled_day(store)
    seen = meta.get("latest_day")
    if seen and "settle_hours" not in meta:
        # A report written before settling existed. Its `latest_day` is the
        # store's newest day, which is the fetch's own uncounted day — two
        # days ahead of what that report actually covered on this account.
        # Comparing a settled day against it is comparing two different
        # measurements that share a name, and it resolves the wrong way:
        # the guard suppresses reports until the settled day catches up,
        # which is precisely the cycles during which the store is gaining
        # the settled days worth reporting. Its window was a different
        # range of days too, so there is nothing to compare and a fresh
        # report is owed.
        return True, ("the last report predates the settling window: its "
                      "latest_day is a fetch day, and it covered a "
                      "different range of days")
    if seen and latest:
        # Decisive either way: the store is the input, and whether it has
        # gained a day is the whole question. The clock is not consulted
        # when the data itself can answer — a new day is new information
        # an hour after the last report as much as a week after.
        if latest > seen:
            return True, f"store advanced to {latest}"
        digest = window_digest(store, timeframe)
        recorded = meta.get("window_digest")
        if recorded and digest and digest != recorded:
            return True, (f"the days through {latest} were revised since the "
                          f"last report; its numbers no longer match the store")
        return False, (f"the store's latest day is still {latest}; the last "
                       f"report already covered it. Nothing new to analyse.")
    # Only when the store cannot answer — an older report with no
    # latest_day recorded — does the clock get a say.
    written = meta.get("written")
    if written:
        try:
            when = datetime.strptime(written, STAMP_FMT).replace(tzinfo=timezone.utc)
        except ValueError:
            when = None
        if when and now - when < MIN_INTERVAL:
            wait = MIN_INTERVAL - (now - when)
            return False, (f"last report was {int((now - when).total_seconds() // 3600)}h "
                           f"ago; {int(wait.total_seconds() // 3600) + 1}h until the "
                           f"{MIN_INTERVAL.days}-day floor lifts.")
    return True, "store advanced"


# ---- helpers -----------------------------------------------------------------

def _pct(part, whole):
    return f"{part / whole * 100:.0f}%" if whole else "n/a"


def _table(headers, rows):
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    out.append("")
    return out


# ---- sections ----------------------------------------------------------------

def section_provenance(store, cfg, timeframe, run_dir, now, first=None):
    days = derive.window(store, timeframe)
    coverage = store.get("coverage") or []
    name = report_name(store, timeframe)
    lines = [
        "## Provenance",
        "",
        "Everything below is `measured`: computed from the durable daily "
        "store by `catnip.derive`, reproducible from the same inputs.",
        "",
    ]
    rows = [
        ["report", f"`{name}`"],
        ["generated", now.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    if first:
        # Present only on a recomputation: the store changed under a period
        # already reported, so the figures below are not the ones published
        # at `first`. That copy is still on disk under its own write stamp.
        rows.append(["first written", first])
    lines += _table(["field", "value"], rows + [
        ["owner", store.get("owner") or "(unset)"],
        ["timeframe", f"`{timeframe}` ({len(days)} days"
         + (f", {days[0]} to {days[-1]}" if days else "") + ")"],
        ["store coverage", "  ".join(f"{a} to {b}" for a, b in coverage) or "n/a"],
        ["store days", len(derive.store_days(store))],
        # The fetch day's bucket is still filling, so no window ends there.
        ["settled through", derive.settled_day(store) or "n/a"],
        ["repos in store", len(derive.repo_names(store))],
        ["run analysed", run_dir.name if run_dir else "none (store only)"],
        ["schema", store.get("schema_version", "?")],
    ])
    return lines


def section_headline(store, timeframe):
    days = derive.window(store, timeframe)
    deltas = derive.deltas(store, timeframe)
    clones = sum(r["clones"]["cur"] for r in deltas.values())
    views = sum(r["views"]["cur"] for r in deltas.values())
    comparable = any(r["comparable"] for r in deltas.values())
    lines = ["## Headline", ""]
    if not days:
        return lines + ["The store is empty. Run `catnip run`.", ""]
    lines.append(f"- **{clones:,} clones** and **{views:,} views** across "
                 f"{len(deltas)} repos with traffic, over {len(days)} days.")
    if comparable:
        dc = sum((r["clones"]["delta"] or 0) for r in deltas.values())
        dv = sum((r["views"]["delta"] or 0) for r in deltas.values())
        lines.append(f"- Against the previous {len(days)} days: "
                     f"**{dc:+,} clones**, **{dv:+,} views**.")
    else:
        lines.append("- No previous window of equal length in the store yet, so "
                     "there is no change to report. This is an absence of "
                     "evidence, not a flat result.")
    lines.append("")
    return lines


def section_movers(store, timeframe, limit=10):
    deltas = derive.deltas(store, timeframe)
    rows = sorted(deltas.values(),
                  key=lambda r: -abs(r["clones"]["delta"] or r["clones"]["cur"]))
    out = ["## Biggest movers", ""]
    table = []
    for r in rows[:limit]:
        m = derive.momentum(store, r["repo"], timeframe)["clones"]
        direction = ("rising" if m["slope"] > 0.05 else
                     "falling" if m["slope"] < -0.05 else "flat")
        delta = r["clones"]["delta"]
        table.append([
            f"`{r['repo']}`", r["clones"]["cur"],
            "n/a" if delta is None else f"{delta:+d}",
            f"{r['clones']['rate']:.1f}/day",
            f"{direction} {m['slope']:+.2f}/day",
        ])
    out += _table(["repo", "clones", "Δ", "rate", "trend"], table)
    out.append("A repo can be up on the window and falling within it — the "
               "window compares two totals, the trend fits the days inside "
               "one. Both are true and they answer different questions.")
    out.append("")
    return out


def section_attribution(store, timeframe, limit=15):
    result = derive.attribution(store, timeframe)
    rows = result["rows"]
    out = ["## What moved, and what caused it", ""]
    if not rows:
        return out + ["Nothing moved and nothing shipped in this window.", ""]
    counts = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    out.append("Tier counts: " + ", ".join(
        f"`{t}` {counts[t]}" for t in derive.ATTRIBUTION_TIERS if t in counts))
    out.append("")
    if not result["coupling_available"]:
        out.append(f"> The `coupled` tier is unavailable: it needs "
                   f"{derive.COUPLE_MIN_DAYS}+ days of store.")
        out.append("")
    table = []
    for r in rows[:limit]:
        effect = ("no measurable change" if r["tier"] == "no-effect"
                  else f"{r['metric']} → {r['value']}")
        via = f" via `{r['via'][0]}` r={r['via'][1]:+.2f}" if r["via"] else ""
        table.append([r["day"], f"`{r['repo']}`", r["tier"],
                      f"{r['cause'] or '—'} {r['detail'] or ''}".strip(),
                      effect + via])
    out += _table(["day", "repo", "tier", "cause", "effect"], table)
    unexplained = result["unexplained"]
    if unexplained:
        out.append(f"**{len(unexplained)} movements have no cause in the store.** "
                   "That is either traffic from somewhere catnip cannot see "
                   "(an aggregator, a link in a chat) or an event type it does "
                   "not collect.")
        out.append("")
    return out


def section_events(store, timeframe):
    grid = derive.anomalies(store, timeframe)
    out = ["## Account events", ""]
    if not grid["campaigns"]:
        return out + ["No day where three or more repos moved together.", ""]
    table = []
    for day, repos in sorted(grid["campaigns"].items(), reverse=True):
        table.append([day, len(repos), ", ".join(f"`{r}`" for r in repos[:6])])
    out += _table(["day", "repos", "which"], table)
    out.append("Simultaneous movement is usually one cause with many "
               "consequences, not many independent findings.")
    out.append("")
    return out


def section_audience(store, timeframe):
    rows = derive.audience(store, timeframe)
    ranked = [r for r in rows.values() if r["label"] != "low-signal"]
    ranked.sort(key=lambda r: r["score"])
    out = ["## Audience — people or fetchers", ""]
    if not ranked:
        return out + [
            f"No repo has {derive.AUDIENCE_MIN_SIGNAL}+ unique cloners plus "
            "visitors in this window, so nothing can be classified. Not a "
            "finding of 'no audience' — an absence of evidence either way.", ""]
    low = len(rows) - len(ranked)
    table = [[f"`{r['repo']}`", r["label"], f"{r['score']:.2f}",
              f"{r['ratio']:.1f}", r["clones"], r["views"],
              ", ".join(r["missing"]) or "—"] for r in ranked]
    out += _table(["repo", "class", "score", "clones/visitor", "clones",
                   "views", "withheld"], table)
    out.append(f"{low} repo(s) had too little traffic to classify.")
    crawlers = [r for r in ranked if r["label"] == "crawler"]
    if crawlers:
        out.append("")
        out.append("**Fetcher fleets:** " + ", ".join(f"`{r['repo']}`" for r in crawlers)
                   + ". Their clone counts are real and their audience is not; "
                   "any 'adoption' read from those numbers is a machine.")
    out.append("")
    return out


def section_intent(store, timeframe):
    rows = derive.intent(store, timeframe)
    ranked = sorted((r for r in rows.values() if r["label"] != "low-signal"),
                    key=lambda r: -r["score"])
    out = ["## Clone intent", ""]
    if not ranked:
        return out + ["Nothing has enough unique traffic to rank.", ""]
    out += _table(["repo", "score", "label", "uniq cloners", "uniq visitors", "flag"],
                  [[f"`{r['repo']}`", f"{r['score']:.2f}", r["label"],
                    r["uniq_cloners"], r["uniq_visitors"],
                    "CONFLICT" if r["conflict"] else ""] for r in ranked])
    conflicts = [r for r in ranked if r["conflict"]]
    if conflicts:
        out.append("**CONFLICT** rows score as developer interest while the "
                   "audience view classifies them as fetchers — the same clones "
                   "read two ways, disagreeing. Trust the audience column.")
        out.append("")
    return out


def section_coupling(store, timeframe, limit=10):
    result = derive.coupled(store, timeframe)
    out = ["## Coupled repos", ""]
    if result["reason"]:
        return out + [f"Not computed: {result['reason']}.", ""]
    if not result["pairs"]:
        return out + ["No pair moves together once the account-wide wave is "
                      "removed.", ""]
    table = [[f"`{p['a']}`", f"`{p['b']}`", f"{p['r']:+.3f}", f"{p['raw_r']:+.3f}",
              f"{abs(p['raw_r']) - abs(p['r']):+.3f}"]
             for p in result["pairs"][:limit]]
    out += _table(["repo A", "repo B", "residual r", "raw r", "removed"], table)
    out.append("`removed` is how much of the raw correlation was the account "
               "moving as one. Positive means the pair looked coupled only "
               "because everything spiked together. **Negative means the "
               "opposite and is the more interesting case**: the pair reads as "
               "unrelated raw, and the relationship only appears once the "
               "account-wide wave is subtracted — usually one repo absorbing "
               "attention while another spikes.")
    if not result["lag_available"]:
        out.append("")
        out.append(f"> Lag is suppressed: it needs {derive.LAG_MIN_DAYS} aligned "
                   f"days, and there are {len(result['days'])}.")
    out.append("")
    return out


def section_content(funnel_rows, limit=12):
    depth = derive.funnel_depth(funnel_rows)
    out = ["## Content — what people opened", ""]
    if not depth:
        return out + ["No path data in the newest run. GitHub's popular-paths "
                      "endpoint is a rolling 14-day snapshot with no history, "
                      "so this section needs a recent run.", ""]
    ranked = sorted(depth.values(), key=lambda r: -r["total"])[:limit]
    out += _table(["repo", "views", "uniq", "depth", "past front door / overview"],
                  [[f"`{r['repo']}`", r["total"], r["uniq"],
                    "n/a" if not r["front"] and not r["deep"]
                    else f"{r['depth_ratio']:.2f}" if r["front"] else f"{r['deep']}*",
                    f"{r['deep']} / {r['front']}"] for r in ranked])
    out.append("`depth` above 1.0 means more traffic read content than bounced "
               "off the landing page. `*` means no landing-page views at all, "
               "so the ratio has no denominator. `uniq` sums per-page uniques "
               "and therefore over-counts anyone who read more than one page.")
    out.append("")
    best = [r for r in ranked if r["front"] and r["depth_ratio"] >= 1]
    if best:
        out.append("**Read, not bounced:** " + ", ".join(f"`{r['repo']}`" for r in best))
        out.append("")
    return out


def section_limits(store, timeframe, funnel_rows):
    days = derive.window(store, timeframe)
    total_days = len(derive.store_days(store))
    out = ["## What this report cannot tell you", "",
           "Stated explicitly, because an analysis that only lists findings "
           "implies the rest was checked and found uninteresting.", ""]
    limits = []
    if total_days < derive.LAG_MIN_DAYS:
        limits.append(f"**Lead and lag between repos.** Needs "
                      f"{derive.LAG_MIN_DAYS} aligned days; the store has "
                      f"{total_days}.")
    if len(days) < derive.COUPLE_MIN_DAYS:
        limits.append(f"**Coupling.** Needs {derive.COUPLE_MIN_DAYS} days in the "
                      f"window; this one has {len(days)}.")
    if not derive.previous_window(store, timeframe):
        limits.append("**Change.** No previous window of equal length, so no "
                      "delta is computable.")
    if not (store.get("events") or {}):
        limits.append("**Causes.** The store has no release or push log, so "
                      "every movement is unattributed — which is not the same "
                      "as uncaused.")
    if store.get("referrers") is None:
        limits.append("**Referrer diversity.** Never collected, so the audience "
                      "score is computed from three components, not four.")
    if not funnel_rows:
        limits.append("**Content.** No path data in the newest run.")
    limits.append("**Traffic before the store's first day.** GitHub serves ~14 "
                  "days and nothing older; days before catnip started are gone "
                  "permanently.")
    limits.append("**Who.** GitHub's traffic API reports counts and uniques, "
                  "never identities. 'Unique visitors' cannot be deduplicated "
                  "across pages or days.")
    out += [f"- {line}" for line in limits]
    out.append("")
    return out


# ---- assembly ----------------------------------------------------------------

def build_report(store, cfg, timeframe="2w", run_dir=None, now=None, first=None):
    """The full markdown document as a string.

    `first` is the write stamp of the period's first report, passed only
    when this is a recomputation of one already on disk.
    """
    now = now or datetime.now(timezone.utc)
    funnel_rows = read_csv(run_dir / "analysis" / "traffic_funnel.csv") if run_dir else []
    owner = store.get("owner") or "this account"
    lines = [f"# catnip report — {owner}", "",
             f"_Deterministic analysis of the durable daily store. Every figure "
             f"is `{MEASURED}`: derived arithmetic, reproducible from the same "
             f"store and timeframe._", ""]
    for section in (
        section_provenance(store, cfg, timeframe, run_dir, now, first),
        section_headline(store, timeframe),
        section_movers(store, timeframe),
        section_attribution(store, timeframe),
        section_events(store, timeframe),
        section_audience(store, timeframe),
        section_intent(store, timeframe),
        section_coupling(store, timeframe),
        section_content(funnel_rows),
        section_limits(store, timeframe, funnel_rows),
    ):
        lines += section
    return "\n".join(lines).rstrip() + "\n"


# ---- promotion ---------------------------------------------------------------

def first_written(reports_dir, name):
    """When this period was first reported, across every recomputation."""
    stamps = []
    for child in Path(reports_dir).iterdir() if Path(reports_dir).is_dir() else []:
        period, stamp = split_name(child.name)
        if period != name or not (child / "report.md").is_file():
            continue
        meta = load_meta(child)
        stamps.append(meta.get("first_written") or meta.get("written") or stamp or "")
    return min((s for s in stamps if s), default=None)


def promote(reports_dir, now=None):
    """Move every settled period to its unstamped name. Returns the moves.

    Runs on every invocation: a period becomes final about two weeks after
    the cycle that produced it, so tying promotion to a report being
    written would leave the settled copy stamped while the store is quiet.

    The newest recomputation is promoted — it read the most corrected
    store. Its superseded siblings stay put; they are the record of what
    was believed on each date.
    """
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        return []
    cutoff = derive.final_day(now)
    groups = {}
    for child in sorted(reports_dir.iterdir()):
        period, stamp = split_name(child.name)
        if not stamp or not period or not (child / "report.md").is_file():
            continue
        m = NAME_RE.match(period)
        if m and m.group("day") <= cutoff:
            groups.setdefault(period, []).append((stamp, child))

    moved = []
    for period, entries in sorted(groups.items()):
        target = reports_dir / period
        if target.exists():
            continue
        stamp, src = max(entries)
        src.rename(target)
        meta = load_meta(target)
        meta.update({"promoted": True,
                     "promoted_at": (now or datetime.now(timezone.utc)).strftime(STAMP_FMT),
                     "status": "settled"})
        (target / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        moved.append((src, target))
    return moved


def locate(reports_dir):
    """The newest report on disk, as paths an agent can open. None if empty.

    Exists so nothing outside this module knows the naming rule. Globbing
    `reports/*/` and taking the last was correct while every directory was
    a write stamp; with periods it returns a superseded recomputation
    whenever a promoted report has stamped siblings.
    """
    prior = previous_reports(reports_dir)
    if not prior:
        return None
    latest = prior[-1]
    period, stamp = split_name(latest.name)
    return {
        "name": period,
        "dir": str(latest),
        "report": str(latest / "report.md"),
        "meta_path": str(latest / "meta.json"),
        "prowl": str(latest / "prowl.md"),
        "promoted": stamp is None and period is not None,
        "meta": load_meta(latest),
    }


# ---- writing -----------------------------------------------------------------

def write_report(text, reports_dir, store, timeframe, run_dir, now=None):
    """Write report.md + meta.json under `<period>.<write stamp>/`."""
    now = now or datetime.now(timezone.utc)
    stamp = now.strftime(STAMP_FMT)
    reports_dir = Path(reports_dir)
    name = report_name(store, timeframe)
    out_dir = reports_dir / f"{name}.{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    meta = {
        "report_name": name,
        # Apart: when this period was first described, and when the
        # description was last redone because the store changed.
        "first_written": first_written(reports_dir, name) or stamp,
        "last_recomputed": stamp,
        "status": "provisional",
        "promoted": False,
        # Under its original name: readers older than periods use this.
        "written": stamp,
        # The day the report actually covers, which `guard` compares against
        # on the next cycle. It must be the same day the windows end on.
        "latest_day": derive.settled_day(store),
        # Both days, named apart. The whole defect this file now guards
        # against was two different measurements sharing the name
        # `latest_day` across a version boundary.
        "store_latest_day": derive.latest_day(store),
        "store_days": len(derive.store_days(store)),
        "timeframe": timeframe,
        "run": run_dir.name if run_dir else None,
        "provenance": MEASURED,
        # What this report asserted, so the next cycle can tell whether a
        # late arrival has since changed a day it already described.
        "window_digest": window_digest(store, timeframe),
        "settle_hours": derive.settle_hours(),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_dir


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Write a deterministic markdown analysis of the store.")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--timeframe", default="2w",
                   choices=sorted(derive.WINDOW_DAYS),
                   help="Analysis window (default: 2w).")
    p.add_argument("--out-dir", type=Path, help="Override the reports directory.")
    p.add_argument("--force", action="store_true",
                   help="Write even if the store has not advanced since the "
                        "last report. For prototyping.")
    p.add_argument("--stdout", action="store_true",
                   help="Print the report instead of writing it.")
    p.add_argument("--promote", action="store_true",
                   help="Only run the promotion sweep; write nothing.")
    p.add_argument("--locate", action="store_true",
                   help="Print the newest report's paths and meta as JSON.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2

    reports_dir = args.out_dir or cfg.reports_dir

    if args.locate:
        json.dump(locate(reports_dir) or {}, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    # Whatever the guard decides: a period settles about two weeks after
    # the cycle that wrote it.
    for src, target in promote(reports_dir):
        print(f"Promoted: {src.name} -> {target.name}")
    if args.promote:
        return 0

    if not cfg.history_file.is_file():
        print(f"catnip: no durable store at {cfg.history_file}. "
              f"Run `catnip run` first.", file=sys.stderr)
        return 1
    with cfg.history_file.open(encoding="utf-8") as fh:
        store = json.load(fh)

    if not args.stdout:
        ok, reason = guard(reports_dir, store, timeframe=args.timeframe)
        if not ok and not args.force:
            print(f"catnip: skipping report — {reason}", file=sys.stderr)
            print("catnip: pass --force to write anyway.", file=sys.stderr)
            return 3

    first = first_written(reports_dir, report_name(store, args.timeframe))
    text = build_report(store, cfg, args.timeframe, cfg.latest_run(), first=first)
    if args.stdout:
        print(text, end="")
        return 0
    out_dir = write_report(text, reports_dir, store, args.timeframe, cfg.latest_run())
    print(f"Report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
