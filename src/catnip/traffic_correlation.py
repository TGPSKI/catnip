#!/usr/bin/env python3
"""Cross-repo Pearson correlation analysis of clone/view time series."""
import argparse
import csv
import math
from pathlib import Path

from catnip import runfiles


def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError, OverflowError):
        return default


def pearson(x, y):
    """Pearson correlation coefficient between two sequences."""
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sx2 = sum((v - mx) ** 2 for v in x)
    sy2 = sum((v - my) ** 2 for v in y)
    if sx2 == 0 or sy2 == 0:
        return 0.0
    sxy = sum((xv - mx) * (yv - my) for xv, yv in zip(x, y, strict=False))
    return sxy / math.sqrt(sx2 * sy2)


def align_shift(a_len, b_len, lag):
    """Return (a_start, a_end, b_start, b_end, overlap) for a given lag."""
    if lag > 0:
        a_start, a_end = lag, a_len
        b_start, b_end = 0, b_len - lag
    else:
        a_start, a_end = 0, a_len + lag
        b_start, b_end = -lag, b_len
    n_a = a_end - a_start
    n_b = b_end - b_start
    overlap = max(0, min(n_a, n_b))
    return max(a_start, 0), max(a_end, 0), max(b_start, 0), max(b_end, 0), overlap


def analyze_correlations(raw):
    raw = Path(raw)

    repo_data = {}

    for ro in runfiles.repo_list(raw):
        rn = ro.get("name", "unknown")
        # Ending on the fetch day would hand every pair of repos a shared
        # zero to agree on, and a correlation built out of days nobody has
        # traffic on is the artifact this module exists to avoid.
        c_series = [safe_int(c.get("count", 0)) for c in runfiles.daily(raw, rn, "clones")]
        v_series = [safe_int(v.get("count", 0)) for v in runfiles.daily(raw, rn, "views")]

        if c_series or v_series:
            repo_data[rn] = {"clones": c_series, "views": v_series}

    # Need at least 2 repos with >= 3 non-zero days in both series
    valid_repos = []
    for rn, d in repo_data.items():
        c_nz = sum(1 for v in d["clones"] if v > 0)
        v_nz = sum(1 for v in d["views"] if v > 0)
        if c_nz >= 3 and v_nz >= 3:
            valid_repos.append(rn)

    results = []
    threshold = 0.3

    for i in range(len(valid_repos)):
        for j in range(i + 1, len(valid_repos)):
            ra, rb = valid_repos[i], valid_repos[j]
            ca, cb = repo_data[ra]["clones"], repo_data[rb]["clones"]
            va, vb = repo_data[ra]["views"], repo_data[rb]["views"]

            clone_cr = pearson(ca, cb)
            view_cr = pearson(va, vb)

            # Cross-lag: A's clones shifted vs B's views
            best_lr = 0.0
            best_lag = 0
            max_lag = min(3, len(ca), len(vb))

            for lag in range(-max_lag, max_lag + 1):
                a_s, a_e, b_s, b_e, ov = align_shift(len(ca), len(vb), lag)
                if ov < 2:
                    a_p, b_p = [], []
                else:
                    a_p = [ca[k] if k < len(ca) else 0 for k in range(a_s, a_e)]
                    b_p = [vb[k] if k < len(vb) else 0 for k in range(b_s, b_e)]
                    # Pad to equal length
                    ml = max(len(a_p), len(b_p))
                    while len(a_p) < ml:
                        a_p.append(0)
                    while len(b_p) < ml:
                        b_p.append(0)
                if a_p:
                    lr_val = pearson(a_p, b_p)
                    if abs(lr_val) > abs(best_lr):
                        best_lr = lr_val
                        best_lag = lag

            share_days = min(len(ca), len(cb))
            both_active = sum(1 for k in range(share_days)
                              if k < len(ca) and ca[k] > 0 and k < len(cb) and cb[k] > 0)

            if abs(clone_cr) >= threshold or abs(view_cr) >= threshold:
                direction = "none"
                if best_lag > 0:
                    direction = "A leads"
                elif best_lag < 0:
                    direction = "B leads"

                results.append({
                    "repo_a": ra,
                    "repo_b": rb,
                    "clone_clone_corr": round(clone_cr, 4),
                    "view_view_corr": round(view_cr, 4),
                    "cross_lag_best": best_lag,
                    "cross_lag_direction": direction,
                    "cross_lag_r": round(best_lr, 4),
                    "share_days": share_days,
                    "both_active_days": both_active,
                })

    out_dir = raw / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "traffic_correlation.csv"
    fieldnames = [
        "repo_a", "repo_b", "clone_clone_corr", "view_view_corr",
        "cross_lag_best", "cross_lag_direction", "cross_lag_r",
        "share_days", "both_active_days",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    print(f"Correlations: {len(results)} pairs from {len(valid_repos)} valid repos")
    print(f"  CSV: {csv_path}")


def main():
    p = argparse.ArgumentParser(description="Cross-repo correlation analysis.")
    p.add_argument("input_dir", type=Path, help="Directory containing raw/ JSON data.")
    args = p.parse_args()
    analyze_correlations(args.input_dir)


if __name__ == "__main__":
    main()