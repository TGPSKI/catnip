#!/usr/bin/env python3
"""Reading one run directory's raw GitHub payloads.

Every per-run analysis needs the same three things: which repos the run
covered, where a repo's payload for one endpoint lives, and the daily series
inside it bounded to the days GitHub has finished counting.

All four traffic analyses carried their own copy of all three. The copies of
the filename rule were the dangerous part: each inlined

    rn.replace("/", "_").replace("-", "--")

which is `config.slug_for` with its sanitizing step dropped. A repo whose
name carries any character outside `[A-Za-z0-9_.-]` therefore resolved to a
filename the fetcher never wrote, `is_file()` returned False, and the repo
left anomaly, profile, funnel and correlation output with no error and no
row. No repo on this account triggers it today.

The settling rule lives in `derive`; this module is where run payloads meet
it, so a per-run analysis gets settled days by asking for its series rather
than by remembering to filter.
"""
from __future__ import annotations

import json
from pathlib import Path

from catnip import derive
from catnip.config import slug_for

#: Daily-series endpoints, and the key each payload nests its rows under.
SERIES = {"clones": "clones", "views": "views"}


def repo_list(run):
    """The repo objects this run covered; [] when the listing is unreadable."""
    payload = load(run, None, "org_repos")
    return payload if isinstance(payload, list) else []


def path_for(run, repo, endpoint):
    """Where a run keeps one repo's payload for one endpoint."""
    run = Path(run)
    if repo is None:
        return run / "raw" / f"{endpoint}.json"
    return run / "raw" / f"repo_{slug_for(repo)}_{endpoint}.json"


def load(run, repo, endpoint):
    """One raw payload as parsed JSON, or None when absent or unreadable."""
    path = path_for(run, repo, endpoint)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def totals(run, repo, metric):
    """(count, uniques) for a repo's whole traffic window, or (0, 0)."""
    payload = load(run, repo, metric)
    if not isinstance(payload, dict):
        return 0, 0
    return _int(payload.get("count")), _int(payload.get("uniques"))


def daily(run, repo, metric):
    """`metric`'s daily rows for one repo, over settled days only.

    The run's own stamp is the read time, so the same rule the store gets
    applies here without a store to ask.
    """
    payload = load(run, repo, metric)
    if not isinstance(payload, dict):
        return []
    rows = payload.get(SERIES.get(metric, metric)) or []
    return derive.settled_rows(rows, Path(run).name)


def _int(val, default=0):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
