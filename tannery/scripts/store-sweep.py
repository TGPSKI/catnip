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


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: store-sweep.py \"YYYY-MM-DD[ YYYY-MM-DD...]\"")
    dates = sys.argv[1].split()
    for d in dates:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            sys.exit("store-sweep.py: dates must be YYYY-MM-DD, got %r" % d)

    cfg = json.loads(subprocess.check_output(
        ["catnip", "config", "--json"], text=True))
    store_path = "%s/stats/history/traffic_daily.json" % cfg["paths"]["data_dir"]
    repos = json.load(open(store_path))["repos"]

    for date in dates:
        rows = []
        for name, series in repos.items():
            c, cu = series.get("clones", {}).get(date, [0, 0])
            v, vu = series.get("views", {}).get(date, [0, 0])
            if c or v:
                rows.append((c + v, name, c, cu, v, vu))
        rows.sort(reverse=True)
        print("%s: %d of %d repos moved" % (date, len(rows), len(repos)))
        for _, name, c, cu, v, vu in rows:
            print("  %-40s clones %d/%d  views %d/%d" % (name, c, cu, v, vu))
        if not rows:
            print("  none")


if __name__ == "__main__":
    main()
