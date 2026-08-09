#!/usr/bin/env python3
"""Clone intent & profile analysis from clone + view time series + popular paths."""
import argparse
import csv
from datetime import datetime
from pathlib import Path

from catnip import runfiles


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


def clone_intent_label(ratio):
    if ratio >= 50:
        return "developer"
    if ratio >= 20:
        return "tooling"
    return "reference"


def analyze_profiles(raw):
    raw = Path(raw)

    clone_data = {}   # repo_name -> [{timestamp, count, uniques}]
    view_data = {}

    for ro in runfiles.repo_list(raw):
        rn = ro.get("name", "unknown")

        cs = runfiles.daily(raw, rn, "clones")
        if len(cs) >= 3:
            clone_data[rn] = [
                {"timestamp": c.get("timestamp", ""),
                 "count": safe_int(c.get("count")),
                 "uniques": safe_int(c.get("uniques"))}
                for c in cs
            ]

        vs = runfiles.daily(raw, rn, "views")
        if len(vs) >= 3:
            view_data[rn] = [
                {"timestamp": v.get("timestamp", ""),
                 "count": safe_int(v.get("count")),
                 "uniques": safe_int(v.get("uniques"))}
                for v in vs
            ]

    rows = []

    for rn in sorted(set(clone_data.keys()) | set(view_data.keys())):
        clones = clone_data.get(rn, [])
        views = view_data.get(rn, [])

        clone_values = [c["count"] for c in clones]
        view_values = [v["count"] for v in views]
        clone_uniques = [c["uniques"] for c in clones]
        view_uniques = [v["uniques"] for v in views]

        total_clones = sum(clone_values) if clone_values else 0
        total_views = sum(view_values) if view_values else 0
        total_unique_cloners = max(clone_uniques) if clone_uniques else 0
        total_unique_viewers = max(view_uniques) if view_uniques else 0

        # 1. Clone-to-view ratio, clamped to 0-200
        if total_views > 0:
            cv_ratio = total_clones / total_views
        else:
            cv_ratio = 1.0 if total_clones > 0 else 0.0
        cv_ratio_raw = round(cv_ratio, 4)

        # Intent score: ratio * 100, clamped 0-200
        intent_score = min(200, max(0, cv_ratio * 100))
        intent_label = clone_intent_label(intent_score)
        # Volume gate: ratio labels are meaningless on a handful of events
        # (plan 08 phase 5).
        if total_views + total_clones < 10:
            intent_label = "low-signal"

        # 2. Automation ratio: 1 - (unique_cloners / max(unique_viewers, 1))
        if total_unique_viewers > 0:
            autom_ratio = 1.0 - (total_unique_cloners / total_unique_viewers)
        else:
            autom_ratio = -1.0 if total_unique_cloners == 0 else 5.0
        autom_ratio = max(-1.0, min(5.0, autom_ratio))

        # 3. Engagement Depth Index: min(total_views / max|unique_viewers, 1), 50)
        edI = min(total_views / max(total_unique_viewers, 1), 50) if total_unique_viewers or total_views > 0 else 0.0

        # 4. Clone velocity: total_clones / days with clones
        days_with_clones = sum(1 for v in clone_values if v > 0) or 1
        clone_velocity = round(total_clones / days_with_clones, 2)

        # 5. View intensity: total_views / 14
        view_intensity = round(total_views / 14, 2)

        # 6. Clones decay: days from peak clone day to first day where clone <= peak/2
        peak_clone_val = max(clone_values) if clone_values else 0
        clones_half_days = compute_decay_days(clones, peak_clone_val)

        # 7. Views decay
        peak_view_val = max(view_values) if view_values else 0
        views_half_days = compute_decay_days(views, peak_view_val)

        # 8. Clone weekend %
        clone_weekend_pct = 0.0
        if clone_values:
            weekend_clones = 0
            for c in clones:
                ts = parse_timestamp(c.get("timestamp", ""))
                if ts and ts.weekday() >= 5:  # Sat=5, Sun=6
                    weekend_clones += c["count"]
            total_cv = sum(clone_values)
            if total_cv > 0:
                clone_weekend_pct = round(weekend_clones / total_cv * 100, 2)

        # 9. View weekend %
        view_weekend_pct = 0.0
        if view_values:
            weekend_views = 0
            for v in views:
                ts = parse_timestamp(v.get("timestamp", ""))
                if ts and ts.weekday() >= 5:
                    weekend_views += v["count"]
            total_vv = sum(view_values)
            if total_vv > 0:
                view_weekend_pct = round(weekend_views / total_vv * 100, 2)

        # 10. Peak clone day
        peak_clone_day = ""
        if clone_values:
            peak_idx = clone_values.index(peak_clone_val)
            if peak_idx < len(clones):
                peak_clone_day = clones[peak_idx].get("timestamp", "")

        # Only the peak *clone* day reaches the CSV (`peak_day`); the view
        # equivalent was computed and dropped, so it is not computed here.

        # 15-16. Peak values (redundant but for sortability)
        peak_clones_val = peak_clone_val
        peak_views_val = peak_view_val

        # 15-16. Baselines
        baseline_clones = median(clone_values)
        baseline_views = median(view_values)

        rows.append({
            "repo_name": rn,
            "clone_intent_score": round(intent_score, 2),
            "intent_label": intent_label,
            "clone_to_view_ratio": cv_ratio_raw,
            "automation_ratio": round(autom_ratio, 4),
            "edI": round(edI, 2),
            "clone_velocity": clone_velocity,
            "view_intensity": view_intensity,
            "clones_to_half_peak_days": clones_half_days,
            "views_to_half_peak_days": views_half_days,
            "clone_weekend_pct": clone_weekend_pct,
            "view_weekend_pct": view_weekend_pct,
            "peak_day": peak_clone_day,
            "peak_clones": peak_clones_val,
            "peak_views": peak_views_val,
            "baseline_clones": baseline_clones,
            "baseline_views": baseline_views,
        })

    # Write CSV
    out_dir = raw / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "traffic_cloner_profile.csv"
    fieldnames = [
        "repo_name", "clone_intent_score", "intent_label", "clone_to_view_ratio",
        "automation_ratio", "edI", "clone_velocity", "view_intensity",
        "clones_to_half_peak_days", "views_to_half_peak_days",
        "clone_weekend_pct", "view_weekend_pct", "peak_day",
        "peak_clones", "peak_views", "baseline_clones", "baseline_views",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"Clone profiles: {len(rows)} repos profiled")
    print(f"  CSV: {csv_path}")


def compute_decay_days(series, peak_val):
    """Days from peak clone day to first day where clone <= peak/2."""
    if not series or peak_val == 0:
        return 0
    half = peak_val / 2.0
    peak_idx = -1
    for i, s in enumerate(series):
        if s["count"] == peak_val:
            peak_idx = i
            break
    if peak_idx < 0:
        return 0
    for i in range(peak_idx + 1, len(series)):
        if series[i]["count"] <= half:
            return i - peak_idx
    return len(series) - peak_idx


def parse_timestamp(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def main():
    p = argparse.ArgumentParser(description="Clone intent & profile analysis.")
    p.add_argument("input_dir", type=Path, help="Directory containing raw/ JSON data.")
    args = p.parse_args()
    analyze_profiles(args.input_dir)


if __name__ == "__main__":
    main()