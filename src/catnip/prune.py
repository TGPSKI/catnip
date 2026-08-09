#!/usr/bin/env python3
"""Delete old run directories — but never one whose data exists nowhere else.

GitHub serves ~14 trailing days of traffic and nothing older. Once a run
directory is deleted, the only remaining copy of those days is the
history store. So this module refuses, by construction, to delete a run
that has not been ingested: the guard is not a warning printed after the
fact but a precondition on the delete itself.

A second precondition: a run whose days GitHub may still be revising is
kept even when it has been ingested. Being ingested means its numbers are
in the store; it does not mean they were the final numbers. Deleting it
discards the only record from which a revision could be re-derived or
audited, and `history --rebuild` reconstructs from surviving runs alone.

Dry run is the default. Deleting requires --yes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from catnip import derive
from catnip.config import Config, ConfigError, run_dirs


def ingested_runs(history_file: Path) -> set:
    try:
        store = json.loads(Path(history_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(store.get("fetches_ingested", []))


def plan(runs_dir, history_file, retain_days: int, keep_latest: int = 1, now=None):
    """Return (delete, keep) where each entry is (path, reason)."""
    runs = run_dirs(runs_dir)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retain_days)
    # A run's newest day is its own, and GitHub is still adding counts to a
    # day for SETTLE_HOURS after it closes. Until then this run holds
    # observations the store may yet be corrected by, and it is the only
    # on-disk copy: `history --rebuild` can reconstruct nothing it deleted.
    # Retention is normally days and this is hours, so it is a floor that
    # almost never binds — and the one time it does is the time it matters.
    settle_cutoff = now - timedelta(hours=derive.settle_hours()) - timedelta(days=1)
    ingested = ingested_runs(history_file)
    protected = {d.name for d in runs[-keep_latest:]} if keep_latest else set()

    delete, keep = [], []
    for run in runs:
        try:
            when = datetime.strptime(run.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            keep.append((run, "unrecognized run id"))
            continue
        age = (now - when).days
        if run.name in protected:
            keep.append((run, f"newest run ({age}d)"))
        elif when >= cutoff:
            keep.append((run, f"within retention ({age}d < {retain_days}d)"))
        elif when >= settle_cutoff:
            keep.append((run, f"observed days GitHub may still revise "
                              f"(settling for {derive.settle_hours()}h)"))
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
