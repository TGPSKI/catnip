"""catnip — GitHub traffic analytics you own, on a timer.

Pipeline, in the order the data moves:

    fetch.sh      GitHub REST API   -> <data>/runs/<id>/raw/*.json
    analyze.py    raw JSON          -> <data>/runs/<id>/analysis/*.csv
    traffic_*.py  analysis CSVs     -> the four deep-traffic CSVs
    history.py    analysis CSVs     -> <data>/stats/history/ (never pruned)
    totals.py     runs + history    -> <data>/stats/totals.json
    ui.py         all of the above  -> the terminal

`config.py` is the one place that decides where any of those paths are;
`tui/` is vendored from https://github.com/TGPSKI/pane and knows nothing
about GitHub.
"""

__version__ = "0.4.0"
