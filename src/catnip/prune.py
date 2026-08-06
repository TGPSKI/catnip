#!/usr/bin/env python3
"""Delete old run directories — but never one whose data exists nowhere else.

GitHub serves ~14 trailing days of traffic and nothing older. Once a run
directory is deleted, the only remaining copy of those days is the
history store. So this module refuses, by construction, to delete a run
that has not been ingested: the guard is not a warning printed after the
fact but a precondition on the delete itself.

Dry run is the default. Deleting requires --yes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catnip.config import Config, ConfigError, run_dirs


def ingested_runs(history_file: Path) -> set:
    try:
        store = json.loads(Path(history_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(store.get("fetches_ingested", []))


def plan(runs_dir, history_file, retain_days: int, keep_latest: int = 1):
    """Return (delete, keep) where each entry is (path, reason)."""
    runs = run_dirs(runs_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    ingested = ingested_runs(history_file)
    protected = {d.name for d in runs[-keep_latest:]} if keep_latest else set()

    delete, keep = [], []
    for run in runs:
        try:
            when = datetime.strptime(run.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            keep.append((run, "unrecognized run id"))
            continue
        age = (datetime.now(timezone.utc) - when).days
        if run.name in protected:
            keep.append((run, f"newest run ({age}d)"))
        elif when >= cutoff:
            keep.append((run, f"within retention ({age}d < {retain_days}d)"))
        elif run.name not in ingested:
            # The whole point of this module.
            keep.append((run, f"NOT INGESTED — deleting would lose {age}d-old traffic days"))
        else:
            delete.append((run, f"{age}d old, ingested"))
    return delete, keep


def main(argv=None):
    p = argparse.ArgumentParser(description="Prune old catnip run directories.")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--retain-days", type=int, help="Override CATNIP_RETAIN_DAYS.")
    p.add_argument("--yes", action="store_true", help="Actually delete (default is a dry run).")
    p.add_argument("--quiet", action="store_true", help="Only print what is deleted.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
        retain = args.retain_days if args.retain_days is not None else cfg.integer("CATNIP_RETAIN_DAYS")
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2

    delete, keep = plan(cfg.runs_dir, cfg.history_file, retain)

    if not args.quiet:
        for run, why in keep:
            marker = "!" if why.startswith("NOT INGESTED") else " "
            print(f"  {marker} keep   {run.name}  ({why})")
    for run, why in delete:
        print(f"    {'delete' if args.yes else 'would delete'} {run.name}  ({why})")

    stranded = [r for r, why in keep if why.startswith("NOT INGESTED")]
    if stranded:
        print(f"\n{len(stranded)} run(s) are past retention but not in the history store.",
              file=sys.stderr)
        print("Run `catnip history` first; they will be prunable afterwards.", file=sys.stderr)

    if not args.yes:
        print(f"\nDry run — {len(delete)} run(s) would be deleted. Pass --yes to delete.")
        return 0

    freed = 0
    for run, _ in delete:
        freed += sum(f.stat().st_size for f in run.rglob("*") if f.is_file())
        shutil.rmtree(run)
    print(f"\nDeleted {len(delete)} run(s), freed {freed / 1_048_576:.1f} MiB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
