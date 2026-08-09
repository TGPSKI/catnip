#!/usr/bin/env python3
"""Rebuild rolling totals across every run (a pure rebuild, never an accumulator).

The stats file is a pure *output*, regenerated from scratch on every
invocation and never read back as input. The obvious alternative — an
accumulator that adds each new run to its own previous output — is
wrong here in a way that looks right for weeks: every run snapshots the
same trailing ~14-day traffic window, so adding runs together counts
most days a dozen times over and inflates clones and views severalfold.

Traffic dedupe: the stitched series takes the element-wise max per
(repo, metric, date) across snapshots. GitHub revises recent days upward
as its pipeline settles, so max is the settled value.

When the history store (stats/history/traffic_daily.json, written by
history.py) exists, its merged series is used as the base — it survives
run pruning — and any not-yet-ingested runs are stitched on top.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from catnip import derive
from catnip.config import Config, ConfigError
from catnip.config import run_dirs as list_fetch_dirs


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Rebuild catnip totals from run dirs.")
    p.add_argument("runs_dir", type=Path, nargs="?", help="Runs directory (default: from config).")
    p.add_argument("stats_file", type=Path, nargs="?", help="Output JSON (default: from config).")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--owner", help="Owner recorded in the output (default: from config).")
    p.add_argument("--history-file", type=Path,
                   help="Merged history store used as base (default: from config).")
    return p.parse_args(argv)


def load_json(path):
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARN: unreadable JSON {path}: {exc}", file=sys.stderr)
        return None


def read_csv_rows(path):
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def safe_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def slug_for(name):
    s = name.replace("/", "_").replace("-", "--")
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", s)


def merge_day(series, repo, metric, date, count, uniques):
    """Element-wise max per (repo, metric, date)."""
    slot = series.setdefault(repo, {"clones": {}, "views": {}})[metric]
    prev = slot.get(date)
    if prev is None:
        slot[date] = [count, uniques]
    else:
        slot[date] = [max(prev[0], count), max(prev[1], uniques)]


def stitch_fetch_dir(series, fetch_dir):
    """Merge one fetch dir's raw traffic JSON into the stitched series."""
    raw = fetch_dir / "raw"
    org_repos = load_json(raw / "org_repos.json")
    name_by_slug = {}
    if isinstance(org_repos, list):
        for r in org_repos:
            if isinstance(r, dict) and r.get("name"):
                name_by_slug[slug_for(r["name"])] = r["name"]

    found = False
    for path in sorted(raw.glob("repo_*_clones.json")) + sorted(raw.glob("repo_*_views.json")):
        metric = "clones" if path.name.endswith("_clones.json") else "views"
        slug = path.name[len("repo_"):-len(f"_{metric}.json")]
        repo = name_by_slug.get(slug, slug)
        obj = load_json(path)
        if not isinstance(obj, dict):
            continue
        for it in obj.get(metric, []) or []:
            if not isinstance(it, dict) or not it.get("timestamp"):
                continue
            merge_day(series, repo, metric, it["timestamp"][:10],
                      safe_int(it.get("count")), safe_int(it.get("uniques")))
            found = True
    if found:
        return

    # Fallback: long-format analysis CSV (raw traffic JSON missing).
    for row in read_csv_rows(fetch_dir / "analysis" / "github_traffic_timeseries.csv"):
        metric = row.get("metric", "")
        if metric not in ("clones", "views"):
            continue
        merge_day(series, row.get("repo_name", ""), metric,
                  row.get("timestamp", "")[:10],
                  safe_int(row.get("count")), safe_int(row.get("uniques")))


def stitch_daily_series(dirs, base=None):
    series = base or {}
    for fetch_dir in dirs:
        stitch_fetch_dir(series, fetch_dir)
    return series


def latest_snapshots(dirs):
    """(latest_rows, previous_rows, latest_dir, first_seen) from the
    stats-by-repo CSVs, newest dirs last."""
    analyzed = [d for d in dirs if (d / "analysis" / "github_stats_by_repo.csv").is_file()]
    latest_rows = read_csv_rows(analyzed[-1] / "analysis" / "github_stats_by_repo.csv") if analyzed else []
    prev_rows = read_csv_rows(analyzed[-2] / "analysis" / "github_stats_by_repo.csv") if len(analyzed) > 1 else []
    first_seen = {}
    for d in analyzed:
        for row in read_csv_rows(d / "analysis" / "github_stats_by_repo.csv"):
            name = row.get("repo_name", "")
            if name and name not in first_seen:
                first_seen[name] = d.name
    return latest_rows, prev_rows, (analyzed[-1] if analyzed else None), first_seen


def main(argv=None):
    args = parse_args(argv)
    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2
    runs_dir = args.runs_dir or cfg.runs_dir
    stats_file = args.stats_file or cfg.stats_file
    history_file = args.history_file or cfg.history_file
    owner = args.owner or cfg.values.get("CATNIP_OWNER") or ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dirs = list_fetch_dirs(runs_dir)
    if not dirs:
        print(f"No run directories found under {runs_dir}", file=sys.stderr)
        return 1

    # Stitched daily traffic series: history store as base when present.
    base = {}
    ingested = set()
    store = load_json(history_file)
    if isinstance(store, dict) and isinstance(store.get("repos"), dict):
        base = {repo: {"clones": dict(v.get("clones", {})),
                       "views": dict(v.get("views", {}))}
                for repo, v in store["repos"].items()}
        ingested = set(store.get("fetches_ingested", []))
        print(f"  History store base: {len(base)} repos, "
              f"{len(ingested)} fetches ingested")
    remaining = [d for d in dirs if d.name not in ingested]
    series = stitch_daily_series(remaining, base=base)

    latest_rows, prev_rows, latest_dir, first_seen = latest_snapshots(dirs)
    prev_by_name = {r.get("repo_name", ""): r for r in prev_rows}

    org_daily = defaultdict(lambda: {"clones": 0, "views": 0})
    total_clones = total_views = 0
    per_repo_traffic = {}
    for repo, metrics in series.items():
        c = sum(v[0] for v in metrics["clones"].values())
        v = sum(v[0] for v in metrics["views"].values())
        per_repo_traffic[repo] = {"clones": c, "views": v}
        total_clones += c
        total_views += v
        for date, val in metrics["clones"].items():
            org_daily[date]["clones"] += val[0]
        for date, val in metrics["views"].items():
            org_daily[date]["views"] += val[0]

    # Both windows must end on a day GitHub has finished counting, or the
    # current one carries the fetch day's zero and the day before it at a
    # fraction while the prior one is complete — and every trend arrow reads
    # as a decline that is really the shape of the collection.
    settle_edge = derive.settled_edge(
        max([*ingested, *(d.name for d in remaining)], default=None))

    def window_sum(metric, days, offset=0):
        dates = [d for d in sorted(org_daily)
                 if settle_edge is None or d <= settle_edge]
        sel = dates[-(days + offset): len(dates) - offset if offset else None]
        return sum(org_daily[d][metric] for d in sel)

    repo_snapshots = {}
    for row in latest_rows:
        name = row.get("repo_name", "")
        if not name:
            continue
        prev = prev_by_name.get(name, {})
        # A repo absent from the previous snapshot is first-seen, not a change:
        # computing stars-0 would report its whole star count as a delta.
        seen_prev = name in prev_by_name
        traffic = per_repo_traffic.get(name, {"clones": 0, "views": 0})
        # True daily deltas from the stitched series (last full day vs prior).
        rep = series.get(name, {"clones": {}, "views": {}})
        cdates = sorted(rep["clones"])
        vdates = sorted(rep["views"])
        clones_delta = ((rep["clones"][cdates[-1]][0] - rep["clones"][cdates[-2]][0])
                        if len(cdates) > 1 else 0)
        views_delta = ((rep["views"][vdates[-1]][0] - rep["views"][vdates[-2]][0])
                       if len(vdates) > 1 else 0)
        repo_snapshots[name] = {
            "stars": safe_int(row.get("stars")),
            "forks": safe_int(row.get("forks")),
            "watchers": safe_int(row.get("watchers")),
            "downloads": safe_int(row.get("total_downloads")),
            "clones": traffic["clones"],
            "views": traffic["views"],
            "stars_delta": (safe_int(row.get("stars")) - safe_int(prev.get("stars"))) if seen_prev else 0,
            "forks_delta": (safe_int(row.get("forks")) - safe_int(prev.get("forks"))) if seen_prev else 0,
            "clones_delta": clones_delta,
            "views_delta": views_delta,
            "archived": row.get("archived", "False") == "True",
            "last_fetch": latest_dir.name if latest_dir else "",
            "first_seen": first_seen.get(name, ""),
            "activity_score": float(row.get("activity_score", 0) or 0),
        }

    # Latest-fetch snapshot-shaped aggregates.
    analysis = latest_dir / "analysis" if latest_dir else None
    languages_data = {}
    contrib_agg = defaultdict(lambda: {"total": 0, "repos": set()})
    top_paths = []
    daily_commit_counts = Counter()
    anomaly_counts = {"total": 0, "by_type": {}}
    pr_total = pr_merged = releases = downloads = 0
    if analysis is not None:
        lang_bytes = defaultdict(lambda: {"total_bytes": 0, "repos": set()})
        for row in read_csv_rows(analysis / "github_languages.csv"):
            lang = row.get("language", "")
            if not lang:
                continue
            lang_bytes[lang]["total_bytes"] += safe_int(row.get("bytes"))
            if row.get("repo_name"):
                lang_bytes[lang]["repos"].add(row["repo_name"])
        gtb = sum(v["total_bytes"] for v in lang_bytes.values()) or 1
        languages_data = {
            lang: {"total_bytes": v["total_bytes"],
                   "repo_count": len(v["repos"]),
                   "percentage": round(v["total_bytes"] / gtb * 100, 1)}
            for lang, v in lang_bytes.items()
        }
        for row in read_csv_rows(analysis / "github_contributions.csv"):
            login = row.get("contributor_login", "")
            if not login:
                continue
            contrib_agg[login]["total"] += safe_int(row.get("contributions"))
            if row.get("repo_name"):
                contrib_agg[login]["repos"].add(row["repo_name"])
        path_agg = defaultdict(lambda: {"count": 0, "uniques": 0, "repo": "", "title": ""})
        for row in read_csv_rows(analysis / "github_traffic_paths.csv"):
            path = row.get("path", "")
            if not path:
                continue
            pa = path_agg[path]
            pa["count"] += safe_int(row.get("count"))
            pa["uniques"] += safe_int(row.get("uniques"))
            if not pa["repo"]:
                pa["repo"] = row.get("repo_name", "")
                pa["title"] = row.get("title", "")
        top_paths = [
            {"path": path, **data}
            for path, data in sorted(path_agg.items(), key=lambda kv: -kv[1]["count"])[:20]
        ]
        for row in read_csv_rows(analysis / "github_code_frequency.csv"):
            wl = row.get("week_label", "")
            if wl and "W" in wl:
                daily_commit_counts[wl] += safe_int(row.get("commits"))
        anomaly_rows = read_csv_rows(analysis / "traffic_anomaly.csv")
        anomaly_counts["total"] = len(anomaly_rows)
        by_type = Counter(r.get("anomaly_type", "") for r in anomaly_rows)
        anomaly_counts["by_type"] = dict(by_type)
        for row in read_csv_rows(analysis / "github_pull_requests.csv"):
            pr_total += 1
            if row.get("merged") == "true":
                pr_merged += 1
        releases = len({(r.get("repo_name"), r.get("tag_name"))
                        for r in read_csv_rows(analysis / "github_releases.csv")})
        downloads = sum(safe_int(r.get("download_count"))
                        for r in read_csv_rows(analysis / "github_release_assets.csv"))

    top_contributors = [
        {"login": login, "total_contributions": data["total"],
         "repos": sorted(data["repos"])}
        for login, data in sorted(contrib_agg.items(), key=lambda kv: -kv[1]["total"])[:20]
    ]

    uniques_14d = {
        "note": "uniques are not summable across days; window from latest fetch",
        "clones": sum(safe_int(r.get("total_unique_cloners")) for r in latest_rows),
        "views": sum(safe_int(r.get("total_unique_views")) for r in latest_rows),
    }

    output = {
        "schema_version": 2,
        "updated": now,
        "owner": owner,
        "fetch_dirs_processed": len(dirs),
        "total_repos": len(repo_snapshots),
        "total_stars": sum(v["stars"] for v in repo_snapshots.values()),
        "total_forks": sum(v["forks"] for v in repo_snapshots.values()),
        "total_watchers": sum(v["watchers"] for v in repo_snapshots.values()),
        "total_open_issues": sum(safe_int(r.get("open_issues")) for r in latest_rows),
        "total_pull_requests": pr_total,
        "total_merged_prs": pr_merged,
        "total_releases": releases,
        "total_commits": sum(safe_int(r.get("total_commits")) for r in latest_rows),
        "total_downloads": downloads,
        "total_unique_contributors": len(contrib_agg),
        "archived_repos": sum(1 for v in repo_snapshots.values() if v["archived"]),
        "total_clones": total_clones,
        "total_views": total_views,
        "uniques_14d": uniques_14d,
        "clones_7d": window_sum("clones", 7),
        "clones_prior_7d": window_sum("clones", 7, offset=7),
        "views_7d": window_sum("views", 7),
        "views_prior_7d": window_sum("views", 7, offset=7),
        "org_daily": {d: org_daily[d] for d in sorted(org_daily)},
        "languages_data": languages_data,
        "top_repos": sorted(repo_snapshots.values(),
                            key=lambda x: -x.get("activity_score", 0))[:50],
        "repo_snapshots": repo_snapshots,
        "top_contributors": top_contributors,
        "daily_commit_counts": dict(daily_commit_counts),
        "top_paths": top_paths,
        "anomaly_counts": anomaly_counts,
    }

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    with stats_file.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)
        fh.write("\n")

    print(f"Wrote rolling totals ({output['total_repos']} repos, "
          f"{total_clones} clones, {total_views} views, deduped)")
    print(f"  Stats file: {stats_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
