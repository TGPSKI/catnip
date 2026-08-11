#!/usr/bin/env python3
"""Queue an inference cycle when a deterministic report settles.

The meta-analyst used to run on its own cron, every third day, which asks
the calendar a question only the data can answer. Most of what it read was
provisional: GitHub keeps revising a day for `CATNIP_SETTLE_HOURS` after it
closes and can correct one until it leaves the 14-day window, so a cycle
seeded on a Tuesday investigated numbers that were different by Thursday.
Analysing a window nothing can change again is the same work over figures
that will still be there.

So the trigger is the transition itself: `catnip report` recomputes a
period the moment nothing can revise it, and that transition — not a
weekday — enqueues one cycle. Runs where nothing settled queue nothing,
which on a six-hourly timer is most of them.

The dispatched period is recorded, so this is safe to call every cycle and
safe to call twice: the settling sweep reports a transition once, but the
record is what decides, and a consumer installed after the fact or down
for a day resumes from the newest settled period rather than replaying
every one of them.

    usage: prowl-onsettle.py [--dry-run]
"""
import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(".state/catnip-onsettle.json")
INTAKE = ("http://127.0.0.1:7751/intake"
          "?kind=report.settled&source=catnip-report&queue=prowl-meta-in")

NOTICE = """\
SETTLED: {period}
WINDOW END: {day}
TIMEFRAME: {timeframe}
REPORT: {dir}

The deterministic report for {period} has settled: every day it covers has
left GitHub's 14-day traffic reach, so no later fetch can change a figure in
it. It was recomputed from the store as it now stands, which is not the copy
that was on disk while the window was open.

Size this cycle. The store's current window is the one to investigate - the
settled period is what says this cycle is owed, not what limits it.
"""


def fail(msg):
    sys.exit("prowl-onsettle.py: " + msg)


def transitions():
    """What `catnip report` knows about settled periods, now."""
    try:
        out = subprocess.check_output(
            ["catnip", "report", "--transitions"], text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        fail(f"catnip report --transitions failed: {exc}")
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        fail(f"catnip report --transitions printed no JSON: {out[:200]}")


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def enqueue(period):
    body = NOTICE.format(**period)
    req = urllib.request.Request(INTAKE, data=body.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:
        fail(f"intake refused the cycle for {period['period']}: {exc}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be queued; enqueue nothing.")
    args = p.parse_args()

    result = transitions()
    known = result.get("known") or []
    if not known:
        print("no settled period on disk yet")
        print("queued 0 cycle(s)")
        return

    state = load_state()
    seen = state.get("dispatched") or []
    fresh = [k for k in known if k["period"] not in seen]
    if not fresh:
        print(f"no transition since {seen[-1] if seen else 'never'}")
        print("queued 0 cycle(s)")
        return

    # One cycle, however many periods are new. Each one seeds N analyst runs
    # against the same current window: a backlog would spend the account's
    # whole inference budget re-asking one question.
    newest = fresh[-1]
    for period in fresh[:-1]:
        print(f"  skipped      {period['period']} (superseded by a newer "
              f"settled period in the same sweep)")
    print(f"  SETTLED      {newest['period']} (settled_at "
          f"{newest.get('settled_at') or 'unrecorded'})")
    if args.dry_run:
        print("queued 0 cycle(s)")
        return

    enqueue(newest)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "dispatched": seen + [k["period"] for k in fresh],
        "last_period": newest["period"],
        "last_dispatched_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    }, indent=2, sort_keys=True) + "\n")
    print("queued 1 cycle(s)")


if __name__ == "__main__":
    main()
