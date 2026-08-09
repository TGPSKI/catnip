#!/usr/bin/env python3
"""Print the durable store's status as JSON.

Was a 900-character shell one-liner inside shell-tools.json with the
settling wait written into it as the literal 36 — in two places. The
moment an operator raised `CATNIP_SETTLE_HOURS`, the tool went on
computing `settled_expected` from 36 and reporting `stale` against a
horizon catnip itself no longer used, which is the coordinate mismatch
AGENTS.md exists to prevent: two answers to "which day is settled",
neither labelled.

The arithmetic still has to be duplicated here — the tannery runs
`catnip` from PATH and cannot import `catnip.derive` — so the one thing
this must never do is duplicate the *inputs* as well.

    usage: store-status.py
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

STAMP = "%Y%m%dT%H%M%SZ"


def settled_edge(when, hours):
    """The newest day a read at `when` saw finished. Mirrors derive.settled_edge."""
    return ((when - timedelta(hours=hours)).date() - timedelta(days=1)).isoformat()


def main():
    cfg = json.loads(subprocess.check_output(
        ["catnip", "config", "--json"], text=True))
    hours = int(cfg["config"].get("CATNIP_SETTLE_HOURS") or 36)
    with open(cfg["paths"]["history_file"], encoding="utf-8") as fh:
        store = json.load(fh)

    days = sorted({d for repo in (store.get("repos") or {}).values()
                   for metric in ("clones", "views")
                   for d in (repo.get(metric) or {})})
    fetches = sorted(store.get("fetches_ingested") or [])
    latest_fetch = (datetime.strptime(fetches[-1], STAMP).replace(tzinfo=timezone.utc)
                    if fetches else None)
    edge = settled_edge(latest_fetch, hours) if latest_fetch else None
    settled = [d for d in days if edge is None or d <= edge]
    settled_day = settled[-1] if settled else None
    expected = settled_edge(datetime.now(timezone.utc), hours)

    json.dump({
        "schema_version": store.get("schema_version"),
        "owner": store.get("owner"),
        "updated": store.get("updated"),
        "latest_day": days[-1] if days else None,
        "settled_day": settled_day,
        "settled_expected": expected,
        "stale": "yes" if (settled_day is None or settled_day < expected) else "no",
        "settle_hours": hours,
        "coverage": store.get("coverage"),
        "repos": len(store.get("repos") or {}),
        "paths_repos": len(store.get("paths") or {}),
    }, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
