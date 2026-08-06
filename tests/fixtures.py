#!/usr/bin/env python3
"""Synthetic run directories — everything the pipeline reads, with no network.

`make_run` writes the same file layout `fetch.sh` produces, so the
analyze/history/totals stages under test are the real ones. The shapes
here are the contract with GitHub's API; when a field name changes
upstream, this file is where the change gets encoded.
"""
import json
from datetime import date, timedelta
from pathlib import Path

# Kept identical to catnip.config.slug_for; imported rather than copied so
# a divergence fails the test suite instead of the next live run.
from catnip.config import slug_for


def _traffic(days, base, start):
    return {
        "count": base * days,
        "uniques": max(1, base // 2) * days,
        "clones": [
            {"timestamp": f"{start + timedelta(days=i)}T00:00:00Z",
             "count": base + (i % 3), "uniques": max(1, base // 2)}
            for i in range(days)
        ],
    }


def make_run(root, repos=("alpha", "beta-repo"), days=14, run_id="20260805T120000Z",
             with_traffic=True, stars=3):
    """Write a run directory under <root>/runs/<run_id> and return its path."""
    root = Path(root)
    run = root / "runs" / run_id
    raw = run / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (run / "reports").mkdir(exist_ok=True)
    start = date(2026, 7, 23)

    listing = []
    for idx, name in enumerate(repos):
        meta = {
            "name": name,
            "full_name": f"testuser/{name}",
            "private": False,
            "fork": False,
            "archived": False,
            "html_url": f"https://github.com/testuser/{name}",
            "description": f"synthetic repo {name}",
            "language": "Python",
            "stargazers_count": stars + idx,
            "forks_count": idx,
            "watchers_count": stars + idx,
            "open_issues_count": idx,
            "size": 1024 * (idx + 1),
            "default_branch": "main",
            "license": {"spdx_id": "GPL-3.0"},
            "topics": ["analytics"],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-08-01T00:00:00Z",
            "pushed_at": "2026-08-04T00:00:00Z",
        }
        listing.append(meta)
        slug = slug_for(name)
        (raw / f"repo_{slug}.json").write_text(json.dumps(meta), encoding="utf-8")
        (raw / f"repo_{slug}_langs.json").write_text(
            json.dumps({"Python": 10000 * (idx + 1), "Shell": 2000}), encoding="utf-8")
        (raw / f"repo_{slug}_contributors.json").write_text(
            json.dumps([{"login": "testuser", "contributions": 42}]), encoding="utf-8")
        (raw / f"repo_{slug}_pulls.json").write_text(json.dumps([
            {"number": 1, "title": "a pull request", "state": "closed",
             "merged_at": "2026-07-30T00:00:00Z", "created_at": "2026-07-29T00:00:00Z",
             "closed_at": "2026-07-30T00:00:00Z", "user": {"login": "testuser"}},
        ]), encoding="utf-8")
        (raw / f"repo_{slug}_releases.json").write_text(json.dumps([
            {"tag_name": "v0.1.0", "name": "v0.1.0", "published_at": "2026-07-15T00:00:00Z",
             "created_at": "2026-07-15T00:00:00Z", "prerelease": False, "draft": False,
             "assets": [{"name": "dist.tar.gz", "size": 2048, "download_count": 7}]},
        ]), encoding="utf-8")
        (raw / f"repo_{slug}_issues.json").write_text(json.dumps([
            {"number": 2, "title": "an issue", "state": "open",
             "created_at": "2026-07-20T00:00:00Z", "closed_at": None,
             "user": {"login": "testuser"}},
        ]), encoding="utf-8")
        (raw / f"repo_{slug}_commits.json").write_text(json.dumps(
            [{"week": 1753228800, "total": 5, "days": [1, 1, 1, 1, 1, 0, 0]}]),
            encoding="utf-8")
        (raw / f"repo_{slug}_freq.json").write_text(
            json.dumps([[1753228800, 500, -100]]), encoding="utf-8")
        (raw / f"repo_{slug}_contstats.json").write_text(json.dumps([
            {"author": {"login": "testuser"}, "total": 42,
             "weeks": [{"w": 1753228800, "a": 500, "d": 100, "c": 5}]},
        ]), encoding="utf-8")
        (raw / f"repo_{slug}_readme.md").write_text(f"# {name}\n", encoding="utf-8")
        (raw / f"repo_{slug}_stargazers.json").write_text(json.dumps([
            {"starred_at": "2026-07-25T00:00:00Z", "user": {"login": "fan"}},
        ]), encoding="utf-8")
        (raw / f"repo_{slug}_forks.json").write_text(json.dumps([]), encoding="utf-8")

        if with_traffic:
            clones = _traffic(days, 2 + idx, start)
            views = {"count": clones["count"] * 4, "uniques": clones["uniques"] * 2,
                     "views": [{"timestamp": c["timestamp"], "count": c["count"] * 4,
                                "uniques": c["uniques"] * 2} for c in clones["clones"]]}
            (raw / f"repo_{slug}_clones.json").write_text(json.dumps(clones), encoding="utf-8")
            (raw / f"repo_{slug}_views.json").write_text(json.dumps(views), encoding="utf-8")
            (raw / f"repo_{slug}_paths.json").write_text(json.dumps([
                {"path": f"/testuser/{name}", "title": name, "count": 30, "uniques": 12},
                {"path": f"/testuser/{name}/blob/main/README.md", "title": "README",
                 "count": 12, "uniques": 5},
                {"path": f"/testuser/{name}/releases", "title": "Releases",
                 "count": 4, "uniques": 3},
            ]), encoding="utf-8")
            (raw / f"repo_{slug}_referrers.json").write_text(json.dumps([
                {"referrer": "github.com", "count": 20, "uniques": 8},
                {"referrer": "google.com", "count": 5, "uniques": 4},
            ]), encoding="utf-8")

    (raw / "repos_listed.json").write_text(json.dumps(listing), encoding="utf-8")
    (raw / "org_repos.json").write_text(json.dumps(listing), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "run_id": run_id, "owner": "testuser", "owner_type": "user",
        "config_file": "", "repos_listed": len(listing), "repos_selected": len(listing),
        "repos_skipped": 0, "repos_fetched": len(listing), "traffic_denied": 0,
        "rate_remaining_before": "5000", "rate_remaining_after": "4900",
        "duration_seconds": 12,
        "fetched": {"stats": "true", "readme": "true", "events": "true", "issues": "true"},
    }, indent=2), encoding="utf-8")
    return run


def write_config(root, **overrides):
    """Write a catnip.conf under root pointing at root as the data dir."""
    values = {"CATNIP_OWNER": "testuser", "CATNIP_DATA_DIR": str(root)}
    values.update(overrides)
    path = Path(root) / "catnip.conf"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path
