#!/usr/bin/env python3
"""Ingest run directories into the persistent history store.

This is the module that makes catnip worth running on a timer. GitHub's
traffic API exposes only a trailing ~14-day window, and run directories
are pruned after CATNIP_RETAIN_DAYS — so any day that is not ingested
before both of those horizons pass is gone permanently. The store is
therefore the one artifact catnip never deletes:

  <data>/stats/history/traffic_daily.json  merged per-repo daily series
                                           (atomic rewrite per ingest)
  <data>/stats/history/snapshots.jsonl     append-only, one line per (run, repo)

Merge rule: element-wise max per (repo, metric, date) across snapshots.
GitHub revises recent days upward as its pipeline settles, so max is the
settled value; summing overlapping windows instead is what inflates
clone counts several-fold. By default only runs absent from
`fetches_ingested` are read (idempotent — re-ingesting is a no-op);
--rebuild wipes the store and re-scans whatever runs survive on disk.

`coverage` records contiguous observed date ranges; a gap longer than
the traffic window starts a new range, so consumers render gaps as gaps
rather than as zeros. Star/fork event sections are reconstructed from
the full event CSVs on each ingest.
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from catnip.config import Config, ConfigError
from catnip.config import run_dirs as list_fetch_dirs

SCHEMA_VERSION = 1
GAP_DAYS = 14  # a fetch covers ~14 trailing days; a larger gap = new era


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


def merge_fetch(store, fetch_dir):
    """Max-merge one fetch dir's traffic series into the store. Returns the
    number of (repo, metric, day) points seen."""
    points = 0
    for row in read_csv_rows(fetch_dir / "analysis" / "github_traffic_timeseries.csv"):
        repo = row.get("repo_name", "")
        metric = row.get("metric", "")
        day = row.get("timestamp", "")[:10]
        if not repo or metric not in ("clones", "views") or not day:
            continue
        slot = store["repos"].setdefault(repo, {"clones": {}, "views": {}})[metric]
        count, uniques = safe_int(row.get("count")), safe_int(row.get("uniques"))
        prev = slot.get(day)
        slot[day] = ([count, uniques] if prev is None
                     else [max(prev[0], count), max(prev[1], uniques)])
        points += 1
    return points


def compute_coverage(store):
    dates = sorted({d for repo in store["repos"].values()
                    for metric in ("clones", "views")
                    for d in repo[metric]})
    ranges = []
    for d in dates:
        day = date.fromisoformat(d)
        if ranges and (day - date.fromisoformat(ranges[-1][1])).days <= GAP_DAYS:
            ranges[-1][1] = d
        else:
            ranges.append([d, d])
    return ranges


def events_by_month(fetch_dir, csv_name, ts_field):
    out = defaultdict(lambda: defaultdict(int))
    for row in read_csv_rows(fetch_dir / "analysis" / csv_name):
        repo = row.get("repo_name", "")
        ts = row.get(ts_field, "")
        if repo and len(ts) >= 7:
            out[repo][ts[:7]] += 1
    return {repo: dict(months) for repo, months in out.items()}


def append_snapshots(jsonl_path, fetch_dir):
    rows = read_csv_rows(fetch_dir / "analysis" / "github_stats_by_repo.csv")
    prs = defaultdict(lambda: {"total": 0, "merged": 0})
    for row in read_csv_rows(fetch_dir / "analysis" / "github_pull_requests.csv"):
        prs[row.get("repo_name", "")]["total"] += 1
        if row.get("merged") == "true":
            prs[row.get("repo_name", "")]["merged"] += 1
    releases = defaultdict(set)
    for row in read_csv_rows(fetch_dir / "analysis" / "github_releases.csv"):
        releases[row.get("repo_name", "")].add(row.get("tag_name", ""))
    downloads = defaultdict(int)
    for row in read_csv_rows(fetch_dir / "analysis" / "github_release_assets.csv"):
        downloads[row.get("repo_name", "")] += safe_int(row.get("download_count"))

    day = f"{fetch_dir.name[:4]}-{fetch_dir.name[4:6]}-{fetch_dir.name[6:8]}"
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            name = row.get("repo_name", "")
            if not name:
                continue
            fh.write(json.dumps({
                "fetch": fetch_dir.name,
                "date": day,
                "repo": name,
                "stars": safe_int(row.get("stars")),
                "forks": safe_int(row.get("forks")),
                "watchers": safe_int(row.get("watchers")),
                "open_issues": safe_int(row.get("open_issues")),
                "contributors": safe_int(row.get("total_contributors")),
                "pull_total": prs[name]["total"],
                "pull_merged": prs[name]["merged"],
                "release_count": len(releases[name]),
                "total_downloads": downloads[name],
                "total_clones_14d": safe_int(row.get("total_clones")),
            }, sort_keys=True) + "\n")
    return len(rows)


def ingest(runs_dir, history_dir, owner, rebuild=False):
    """Merge every not-yet-ingested run under runs_dir into the store."""
    runs_dir, history_dir = Path(runs_dir), Path(history_dir)
    store_path = history_dir / "traffic_daily.json"
    jsonl_path = history_dir / "snapshots.jsonl"

    store = None if rebuild else load_json(store_path)
    if not isinstance(store, dict) or "repos" not in store:
        store = {"schema_version": SCHEMA_VERSION, "owner": owner,
                 "fetches_ingested": [], "coverage": [], "repos": {}}
        if rebuild and jsonl_path.exists():
            jsonl_path.unlink()
    store.setdefault("owner", owner)

    ingested = set(store.get("fetches_ingested", []))
    # A run without the traffic timeseries has not been analyzed yet;
    # ingesting it would mark it done and lose its days forever.
    new_dirs = [d for d in list_fetch_dirs(runs_dir)
                if d.name not in ingested
                and (d / "analysis" / "github_traffic_timeseries.csv").is_file()]
    if not new_dirs:
        print(f"Nothing to ingest ({len(ingested)} runs already in store).")
        return store

    for fetch_dir in new_dirs:
        points = merge_fetch(store, fetch_dir)
        snaps = append_snapshots(jsonl_path, fetch_dir)
        ingested.add(fetch_dir.name)
        print(f"  Ingested {fetch_dir.name}: {points} series points, {snaps} snapshots")

    latest = max(new_dirs, key=lambda d: d.name)
    store["stars_by_month"] = events_by_month(latest, "github_stargazer_events.csv", "starred_at")
    store["forks_by_month"] = events_by_month(latest, "github_fork_events.csv", "created_at")
    store["events_era"] = "reconstructed from starred_at/created_at timestamps (all-time)"
    store["fetches_ingested"] = sorted(ingested)
    store["coverage"] = compute_coverage(store)
    store["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    history_dir.mkdir(parents=True, exist_ok=True)
    tmp = store_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, sort_keys=True)
        fh.write("\n")
    # Atomic replace: a store half-written by an interrupted timer run is
    # the one failure mode with no recovery path.
    tmp.replace(store_path)
    print(f"Store: {store_path} ({len(store['repos'])} repos, coverage {store['coverage']})")
    return store


def main(argv=None):
    p = argparse.ArgumentParser(description="Ingest catnip runs into the history store.")
    p.add_argument("runs_dir", type=Path, nargs="?",
                   help="Runs directory (default: from config).")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--history-dir", type=Path, help="History store directory (default: from config).")
    p.add_argument("--owner", help="Owner recorded in the store (default: from config).")
    p.add_argument("--rebuild", action="store_true",
                   help="Wipe the store and re-scan surviving run dirs.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
        runs_dir = args.runs_dir or cfg.runs_dir
        history_dir = args.history_dir or cfg.history_dir
        owner = args.owner or cfg.values.get("CATNIP_OWNER") or ""
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2
    ingest(runs_dir, history_dir, owner, rebuild=args.rebuild)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
