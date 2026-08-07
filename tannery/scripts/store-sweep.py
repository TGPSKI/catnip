#!/usr/bin/env python3
"""Print every repo with traffic on the given day(s), from the durable store.

The windowed views classify against thresholds, so an account-wide crawl that
touches many repos once each is invisible in every one of them. This is the
tool for that finding class: it reads the store's daily series directly and
answers "which repos moved on this exact day", including repos the report
withheld as too quiet to classify.

    usage: store-sweep.py "YYYY-MM-DD[ YYYY-MM-DD...]"
"""
import json
import re
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: store-sweep.py \"YYYY-MM-DD[ YYYY-MM-DD...]\"")
    dates = sys.argv[1].split()
    for d in dates:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            sys.exit(f"store-sweep.py: dates must be YYYY-MM-DD, got {d!r}")

    cfg = json.loads(subprocess.check_output(
        ["catnip", "config", "--json"], text=True))
    store_path = Path(cfg["paths"]["data_dir"]) / "stats/history/traffic_daily.json"
    repos = json.loads(store_path.read_text())["repos"]

    for date in dates:
        rows = []
        for name, series in repos.items():
            c, cu = series.get("clones", {}).get(date, [0, 0])
            v, vu = series.get("views", {}).get(date, [0, 0])
            if c or v:
                rows.append((c + v, name, c, cu, v, vu))
        rows.sort(reverse=True)
        print(f"{date}: {len(rows)} of {len(repos)} repos moved")
        for _, name, c, cu, v, vu in rows:
            print(f"  {name:<40} clones {c}/{cu}  views {v}/{vu}")
        if not rows:
            print("  none")


if __name__ == "__main__":
    main()
