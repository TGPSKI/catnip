#!/usr/bin/env python3
"""Re-open a prowl cycle whose window the store has since revised.

report.md notices when a day it described is corrected — it records a
window digest and recomputes. prowl.md cannot: a finding is prose plus a
tier, not a recomputable query, and `measured` is both the strongest claim
the pipeline makes and the one most likely to go stale, because the
freshest days are the least settled.

So the cycle is re-opened instead of the finding re-checked. The recorded
digest is recomputed over the SAME days the cycle covered, and a
difference enqueues one brief on the analyst queue. From there the
existing chain runs: analyst -> writer -> editor -> prowl.md, whose
supersede path already handles a second document for one cycle.

Two bounds stop this running forever. A cycle whose window has left
GitHub's 14-day reach is closed permanently — nothing can revise it again.
And a revision is enqueued once: the digest that triggered a re-run is
recorded, so a check that runs daily does not re-enqueue the same one.

The cycle's recorded findings are archived before the re-run, not appended
to. `prowl-publish.sh` dedupes on the claim heading and keeps the first of
each, so leaving them in place would republish the stale figures under the
same heading the re-run was meant to correct.

    usage: prowl-reopen.py [--limit N] [--dry-run]
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROWL = Path(".state/prowl")
INTAKE = ("http://127.0.0.1:7751/intake"
          "?kind=prowl.brief&source=catnip-prowl-reopen&queue=prowl-analyze-in")
# Everything one cycle accumulates. Archived together, so the re-run starts
# from an empty record rather than deduping against the figures it corrects.
CYCLE_FILES = ("md", "refuted.md", "corrections.md", "watch.md",
               "assembled.md", "previous-headings")

BRIEF = """\
This cycle has already been published. Its window was re-read and the store
no longer holds the numbers it was written from: at least one day in
{start} to {end} was revised after publication.

Re-derive this window from current data. Every measured claim in the
published prowl for {cycle} rests on figures that may have moved, so quote
nothing from it - read the report and the views again.

File a FINDING for what holds on the current numbers, and a REFUTED block
for anything the published cycle claimed that no longer does. If everything
still holds at the new figures, file a WATCH saying so and naming the days
that changed.
"""


def fail(msg):
    sys.exit("prowl-reopen.py: " + msg)


def digest_now(end, timeframe):
    """The digest the store holds for the days a cycle covered."""
    try:
        out = subprocess.check_output(
            ["catnip", "report", "--digest", "--end", end,
             "--timeframe", timeframe], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"catnip report --digest failed: {exc}")
    return json.loads(out)


def archive(cycle, stamp):
    moved = []
    for suffix in CYCLE_FILES:
        src = PROWL / f"{cycle}.{suffix}"
        if src.exists():
            src.rename(PROWL / f"{cycle}.superseded-{stamp}.{suffix}")
            moved.append(suffix)
    return moved


def enqueue(cycle, window_start, window_end):
    body = "CYCLE: {}\nWINDOW: {} to {}\nANGLE: {}\n\n{}\n".format(
        cycle, window_start, window_end,
        f"re-derive the revised window {window_start} to {window_end}",
        BRIEF.format(cycle=cycle, start=window_start, end=window_end))
    req = urllib.request.Request(INTAKE, data=body.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        fail(f"intake refused the re-run of {cycle}: {exc}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=10,
                   help="How many of the newest cycles to check (default 10).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be re-opened; enqueue nothing.")
    args = p.parse_args()

    records = sorted(PROWL.glob("*.digest")) if PROWL.is_dir() else []
    if not records:
        print("no published cycle carries a window digest yet")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reopened = 0

    for path in records[-args.limit:]:
        try:
            rec = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            print(f"  skipped      {path.name} (unreadable)")
            continue
        cycle = rec.get("cycle") or path.stem
        if rec.get("closed"):
            continue
        end, recorded = rec.get("window_end"), rec.get("window_digest")
        if not end or not recorded:
            print(f"  skipped      {cycle} (no window recorded)")
            continue

        now = digest_now(end, rec.get("timeframe") or "2w")
        if now.get("settled"):
            # Past GitHub's reach: this cycle can never be revised again.
            rec["closed"] = True
            rec["closed_at"] = stamp
            path.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
            print(f"  closed       {cycle} (window settled at {end})")
            continue
        if now.get("window_digest") == recorded:
            print(f"  unchanged    {cycle}")
            continue
        if rec.get("reopened_digest") == now.get("window_digest"):
            print(f"  already      {cycle} (re-run queued for this revision)")
            continue

        print(f"  REVISED      {cycle} ({recorded} -> {now.get('window_digest')})")
        if args.dry_run:
            continue
        # Enqueue first: a rename is instant and an unreachable intake is
        # not, so failing after the archive would leave the cycle's record
        # moved aside with nothing queued to replace it.
        enqueue(cycle, now.get("start") or "?", end)
        moved = archive(cycle, stamp)
        rec["reopened_at"] = stamp
        rec["reopened_digest"] = now.get("window_digest")
        rec["reopened_count"] = int(rec.get("reopened_count") or 0) + 1
        path.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
        print(f"    archived {len(moved)} file(s), queued one brief")
        reopened += 1

    print(f"re-opened {reopened} cycle(s)")


if __name__ == "__main__":
    main()
