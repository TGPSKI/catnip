#!/usr/bin/env python3
"""MAD-based anomaly detection on daily clone/view time series."""
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from catnip import runfiles

#: Kept equal to derive.Z_MIN_VALUE — the CSV and the TUI must agree about
#: what counts as an event, or `catnip view anomaly` and the anomaly screen
#: disagree about the same day.
MIN_DEVIATION = 3


def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError, OverflowError):
        return default


def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def mad(values):
    """Median Absolute Deviation."""
    med = median(values)
    deviations = [abs(v - med) for v in values]
    return median(deviations)


def mean_ad(values):
    """Mean Absolute Deviation about the median."""
    if not values:
        return 0.0
    med = median(values)
    return sum(abs(v - med) for v in values) / len(values)


def modified_z_score(value, med, mad_val, mean_ad_val=0.0):
    """Modified z-score: 0.6745 * (x - median) / MAD, with a meanAD fallback.

    Returning 0.0 whenever MAD is zero — as this did — makes the detector
    blind to exactly the events worth detecting. A repo that sits at zero
    clones for twelve days and then takes 421 in an afternoon has a median
    of 0 and a MAD of 0, so its spike scored 0.00 and never reached the
    view; on live data that silenced the single largest event in the
    account. (x - median) / (1.253314 * meanAD) is the standard remedy for
    a degenerate MAD and scores that same day 8.7.
    """
    if mad_val:
        return 0.6745 * (value - med) / mad_val
    if mean_ad_val:
        return (value - med) / (1.253314 * mean_ad_val)
    return 0.0


def anomaly_type(severity):
    if abs(severity) > 3.0:
        return "extreme"
    if abs(severity) > 2.0:
        return "significant"
    if abs(severity) > 1.0:
        return "minor"
    return None


def direction(z):
    if z < 0:
        return "dip"
    return "spike"


def analyze_anomalies(raw):
    """Score each repo's daily series, over the days GitHub has finished
    counting. A day still being counted reads low and scores as a dip, and a
    run filed exactly that against a day eighteen hours old — the shape of
    the collection, not of the repo."""
    raw = Path(raw)

    # Load clone and view time series from raw JSON files
    clone_data = {}  # repo_name -> [(timestamp, count, uniques)]
    view_data = {}   # repo_name -> [(timestamp, count, uniques)]

    for ro in runfiles.repo_list(raw):
        rn = ro.get("name", "unknown")

        clones_raw = runfiles.daily(raw, rn, "clones")
        if len(clones_raw) >= 3:
            clone_data[rn] = [{
                "timestamp": c.get("timestamp", ""),
                "count": safe_int(c.get("count")),
                "uniques": safe_int(c.get("uniques")),
                "user": c.get("count_user", 0),
                "user_uniques": c.get("uniques_user", 0),
            } for c in clones_raw]

        views_raw = runfiles.daily(raw, rn, "views")
        if len(views_raw) >= 3:
            view_data[rn] = [{
                "timestamp": v.get("timestamp", ""),
                "count": safe_int(v.get("count")),
                "uniques": safe_int(v.get("uniques")),
            } for v in views_raw]

    results = []
    repos_analyzed = len(set(clone_data.keys()) | set(view_data.keys()))

    for rn, series in clone_data.items():
        values = [e["count"] for e in series]
        _process_series(rn, series, "clones", values, results)

    for rn, series in view_data.items():
        values = [e["count"] for e in series]
        _process_series(rn, series, "views", values, results)

    # Sort: anomaly_type asc (extreme first), then modified_z desc
    type_order = {"extreme": 0, "significant": 1, "minor": 2}
    results.sort(key=lambda r: (type_order.get(r["anomaly_type"], 3), -r["modified_z"]))

    # Write CSV
    out_dir = raw / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "traffic_anomaly.csv"
    fieldnames = [
        "repo_name", "metric", "timestamp", "value",
        "baseline_median", "anomaly_type", "direction",
        "modified_z", "deviation_pct", "days_from_peak",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    # Write summary markdown
    write_summary(out_dir / "traffic_anomaly_summary.md", results, repos_analyzed)

    print(f"Anomaly detection: {len(results)} events from {repos_analyzed} repos")
    print(f"  CSV: {csv_path}")


def _process_series(repo_name, series, metric, values, results):
    if len(values) < 3:
        return

    med = median(values)
    mad_val = mad(values)
    mean_ad_val = mean_ad(values)

    # Must have at least 3 non-zero values to be meaningful
    non_zero = sum(1 for v in values if v > 0)
    if non_zero < 3:
        return

    # Find peak
    peak_val = max(values) if values else 0

    for i, entry in enumerate(series):
        val = entry["count"]
        z = modified_z_score(val, med, mad_val, mean_ad_val)
        atype = anomaly_type(z)
        if atype is None:
            continue
        # Materiality floor. With the meanAD fallback in play a repo whose
        # baseline is zero scores a single clone as extreme, which is true
        # and useless: it fills the table with ones. See derive.Z_MIN_VALUE.
        if abs(val - med) < MIN_DEVIATION:
            continue

        dev_pct = 0.0
        if med > 0:
            dev_pct = round((val - med) / med * 100, 2)

        # Days from peak
        peak_idx = values.index(peak_val)
        days_from_peak = abs(i - peak_idx)

        results.append({
            "repo_name": repo_name,
            "metric": metric,
            "timestamp": entry.get("timestamp", ""),
            "value": val,
            "baseline_median": med,
            "anomaly_type": atype,
            "direction": direction(z),
            "modified_z": round(z, 4),
            "deviation_pct": dev_pct,
            "days_from_peak": days_from_peak,
        })


def write_summary(path, results, repos_analyzed):
    lines = []
    lines.append("# Traffic Anomaly Detection Summary")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Repos analyzed: {repos_analyzed}")
    lines.append(f"Total anomalous events: {len(results)}")
    lines.append("")

    # Distribution by type
    type_counts = defaultdict(int)
    direction_counts = defaultdict(int)
    for r in results:
        type_counts[r["anomaly_type"]] += 1
        direction_counts[r["direction"]] += 1

    lines.append("## Distribution")
    lines.append("")
    lines.append("| Type | Count |")
    lines.append("| --- | --- |")
    for t in ("extreme", "significant", "minor"):
        lines.append(f"| {t} | {type_counts.get(t, 0)} |")
    lines.append("")
    lines.append("| Direction | Count |")
    lines.append("| --- | --- |")
    for d in ("spike", "dip"):
        lines.append(f"| {d} | {direction_counts.get(d, 0)} |")
    lines.append("")

    # Most anomalous repos
    repo_max_z = {}
    for r in results:
        rn = r["repo_name"]
        if rn not in repo_max_z or abs(r["modified_z"]) > abs(repo_max_z[rn]["z"]):
            repo_max_z[rn] = {"z": abs(r["modified_z"]), "type": r["anomaly_type"], "dir": r["direction"]}

    if repo_max_z:
        lines.append("## Most Anomalous Repos")
        lines.append("")
        lines.append("| Repo | Max |Z|| Type | Direction |")
        lines.append("| --- | --- | --- | --- |")
        for rn in sorted(repo_max_z, key=lambda k: -repo_max_z[k]["z"]):
            info = repo_max_z[rn]
            lines.append(f"| {rn} | {info['z']:.4f} | {info['type']} | {info['dir']} |")
        lines.append("")

    # Anomaly count per repo bar chart
    repo_counts = defaultdict(int)
    for r in results:
        repo_counts[r["repo_name"]] += 1
    if repo_counts:
        lines.append("## Anomaly Count per Repo")
        lines.append("")
        max_cnt = max(repo_counts.values()) if repo_counts else 1
        for rn in sorted(repo_counts, key=lambda k: -repo_counts[k]):
            bar = "#" * (int(repo_counts[rn] / max_cnt * 40))
            lines.append(f"- {rn:<30} {bar} ({repo_counts[rn]})")
        lines.append("")

    with path.open("w") as out:
        out.write("\n".join(lines) + "\n")


def main():
    p = argparse.ArgumentParser(description="MAD anomaly detection on clone/view time series.")
    p.add_argument("input_dir", type=Path, help="Directory containing raw/ JSON data + analysis/ output.")
    args = p.parse_args()
    analyze_anomalies(args.input_dir)


if __name__ == "__main__":
    main()