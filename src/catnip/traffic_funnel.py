#!/usr/bin/env python3
"""Content engagement funnel analysis via path taxonomy from popular paths."""
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError, OverflowError):
        return default


# Code file extensions (case-insensitive matching)
CODE_EXTS = {
    ".py", ".js", ".ts", ".rs", ".c", ".h", ".cpp", ".java", ".go",
    ".rb", ".sh", ".swift", ".kt", ".scala", ".lua", ".pl", ".php",
    ".css", ".json", ".yaml", ".toml", ".html", ".md", ".tex", ".svg",
    ".png", ".jpg", ".csv", ".sql", ".proto", ".graphql", ".wasm",
    ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".qmd", ".R", ".jl",
    ".dart", ".zig", ".nim", ".v", ".el", ".org", ".adoc", ".rst", ".mdx",
}


def classify_path(path):
    """Classify a path URL into the taxonomy. Priority order: first match wins."""
    if not path:
        return "other"

    p = path.lower()

    # overview - repo root or main landing pages
    if p in ("/", "/tree/main", "/tree/master", "/blob/main", "/blob/master"):
        return "overview"

    # doc_blob - markdown/doc files in blob paths, excluding root overview
    if "/blob/" in p:
        ext = ""
        dot_idx = p.rfind(".")
        if dot_idx > 0:
            ext = p[dot_idx:]
        if ext in CODE_EXTS:
            if ext in {".md", ".tex", ".rst", ".mdx", ".adoc", ".org"}:
                return "doc_blob"
            return "code_blob"
        if p.endswith(("/doc/", "/docs/", "/documentation/")):
            return "doc_blob"
        return "doc_blob"

    # src_blob - source code files
    if "/src/" in p or p.endswith(("/main", "/main.rs", "/index.js", "/index.ts", "/app.go")):
        return "src_blob"

    # dir_tree - directory listing URLs
    if "/tree/" in p or "/tree/" + p.split("/tree/")[-1] == p or p.endswith("/tree"):
        return "dir_tree"

    # pr_list - pull request list
    if "/pulls" in p and "/pull/" not in p and "/pulls/" not in p:
        return "pr_list"

    # pr_detail - specific pull request
    if "/pull/" in p:
        return "pr_detail"

    # issues - issue list
    if p.endswith("/issues") or "/issues/" in p and "/pull/" not in p:
        return "issues"

    # actions - GitHub Actions
    if "/actions" in p:
        return "actions"

    # releases
    if "/releases" in p:
        return "releases"

    # pulse
    if "/pulse" in p:
        return "pulse"

    # discussions
    if "/discussions" in p:
        return "discussions"

    # forks
    if "/forks" in p:
        return "forks"

    # traffic_graph
    if "/graphs/" in p or "traffic_graph" in p:
        return "traffic_graph"

    return "other"


def is_code_relative(ext):
    return ext.lower() in CODE_EXTS


def analyze_funnel(raw):
    raw = Path(raw)

    org_repos = None
    org_path = raw / "raw/org_repos.json"
    if org_path.is_file():
        try:
            org_repos = json.load(org_path.open())
        except (json.JSONDecodeError, OSError):
            org_repos = []

    repos_list = org_repos if isinstance(org_repos, list) else []
    all_rows = []

    for ro in repos_list:
        rn = ro.get("name", "unknown")
        rn_f = rn.replace("/", "_").replace("-", "--")

        # Load view total
        vp = raw / f"raw/repo_{rn_f}_views.json"
        total_views = 0
        total_unique_visitors = 0
        if vp.is_file():
            try:
                vd = json.load(vp.open())
                if isinstance(vd, dict):
                    total_views = safe_int(vd.get("count", 0))
                    total_unique_visitors = safe_int(vd.get("uniques", 0))
            except (json.JSONDecodeError, OSError):
                pass

        # Load popular paths
        pp = raw / f"raw/repo_{rn_f}_paths.json"
        if pp.is_file():
            try:
                paths_data = json.load(pp.open())
                if isinstance(paths_data, list):
                    for p in paths_data:
                        if not isinstance(p, dict):
                            continue
                        path_str = p.get("path", "") or ""
                        title = p.get("title", "") or ""
                        count = safe_int(p.get("count", 0))
                        uniques = safe_int(p.get("uniques", 0))

                        category = classify_path(path_str)
                        ext = ""
                        di = path_str.rfind(".")
                        if di > 0:
                            ext = path_str[di:].lower()
                        code_rel = category in ("code_blob", "src_blob") or is_code_relative(ext)
                        pr_rel = category in ("pr_detail", "pr_list")
                        doc_rel = category == "doc_blob"
                        infra_rel = category in ("actions", "releases", "pulse", "discussions", "forks", "traffic_graph", "issues", "dir_tree")

                        all_rows.append({
                            "repo_name": rn,
                            "category": category,
                            "view_count": count,
                            "view_pct": 0,  # computed later
                            "unique_visitors": uniques,
                            "unique_pct": 0,
                            "is_code_relative": str(code_rel),
                            "is_pr_related": str(pr_rel),
                            "is_doc_related": str(doc_rel),
                            "is_infra_related": str(infra_rel),
                            "path": path_str,
                            "title": title,
                            "_total_views": total_views,
                            "_total_unique_visitors": total_unique_visitors,
                        })
            except (json.JSONDecodeError, OSError):
                pass

    # Compute view_pct and unique_pct per repo
    repo_views = defaultdict(int)
    repo_uniques = defaultdict(int)
    for r in all_rows:
        repo_views[r["repo_name"]] += r["view_count"]
        repo_uniques[r["repo_name"]] += r["unique_visitors"]

    for r in all_rows:
        rv = repo_views.get(r["repo_name"], 0) or 1
        ru = repo_uniques.get(r["repo_name"], 0) or 1
        r["view_pct"] = round(r["view_count"] / rv * 100, 2)
        r["unique_pct"] = round(r["unique_visitors"] / ru * 100, 2)

    # Write CSV
    out_dir = raw / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "traffic_funnel.csv"
    fieldnames = [
        "repo_name", "category", "view_count", "view_pct", "unique_visitors",
        "unique_pct", "is_code_relative", "is_pr_related", "is_doc_related",
        "is_infra_related", "path", "title",
    ]
    sorted_rows = sorted(all_rows, key=lambda r: r["repo_name"])
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted_rows)

    # Write summary
    write_summary(out_dir / "traffic_summary.md", all_rows)

    print(f"Funnel analysis: {len(all_rows)} path entries from {len(set(r['repo_name'] for r in all_rows))} repos")
    print(f"  CSV: {csv_path}")


def write_summary(path, rows):
    """Write per-repo funnel classification summary."""
    lines = []
    lines.append("# Content Funnel Analysis Summary")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # Group by repo
    by_repo = defaultdict(list)
    by_repo_total_views = defaultdict(int)
    by_repo_total_unique = defaultdict(int)
    for r in rows:
        by_repo[r["repo_name"]].append(r)
        by_repo_total_views[r["repo_name"]] += r["view_count"]
        by_repo_total_unique[r["repo_name"]] += r["unique_visitors"]

    for rn in sorted(by_repo.keys()):
        entries = by_repo[rn]
        total_v = by_repo_total_views[rn]

        # Compute CEI (Code Eng Index)
        code_views = sum(e["view_count"] for e in entries if e["category"] in ("code_blob", "src_blob"))
        total_for_ci = total_v or 1
        cei = round(code_views / total_for_ci * 100, 2)

        # PR engagement
        pr_views = sum(e["view_count"] for e in entries if e["category"] in ("pr_detail", "pr_list"))
        pr_engagement = round(pr_views / total_for_ci * 100, 2)

        # Doc blob %
        doc_views = sum(e["view_count"] for e in entries if e["category"] == "doc_blob")
        doc_pct = round(doc_views / total_for_ci * 100, 2)

        # Overview %
        ov_views = sum(e["view_count"] for e in entries if e["category"] == "overview")
        ov_pct = round(ov_views / total_for_ci * 100, 2)

        # CES: CEI*0.40 + PR*0.30 + doc*0.20 + overview*0.10
        ces = round(cei * 0.40 + pr_engagement * 0.30 + doc_pct * 0.20 + ov_pct * 0.10, 2)

        # Classification
        if ces >= 50 and pr_engagement >= 20:
            classification = "deep-engagement"
        elif ces >= 30 and pr_engagement >= 10:
            classification = "explore"
        elif pr_engagement >= 30:
            classification = "tooling"
        elif cei >= 30:
            classification = "browse"
        elif doc_pct >= 30:
            classification = "documentation"
        else:
            classification = "stable-tool"

        lines.append(f"## {rn}")
        lines.append("")
        lines.append(f"- **CEI**: {cei}%")
        lines.append(f"- **PR Engagement**: {pr_engagement}%")
        lines.append(f"- **CES**: {ces}")
        lines.append(f"- **Classification**: {classification}")
        lines.append("")

        # Category distribution
        cat_counts = defaultdict(int)
        for e in entries:
            cat_counts[e["category"]] += e["view_count"]
        if cat_counts:
            lines.append("### Category Distribution")
            lines.append("")
            lines.append("| Category | Views | Bar |")
            lines.append("| --- | --- | --- |")
            max_c = max(cat_counts.values()) if cat_counts else 1
            for cat in ("code_blob", "doc_blob", "pr_detail", "pr_list",
                         "src_blob", "dir_tree", "overview", "issues",
                         "actions", "releases", "pulse", "discussions",
                         "forks", "traffic_graph", "other"):
                if cat in cat_counts:
                    bar = "#" * (int(cat_counts[cat] / max_c * 30)) if max_c else ""
                    lines.append(f"| {cat} | {cat_counts[cat]} | {bar} |")
            lines.append("")

    with path.open("w") as out:
        out.write("\n".join(lines) + "\n")


def main():
    p = argparse.ArgumentParser(description="Content engagement funnel analysis.")
    p.add_argument("input_dir", type=Path, help="Directory containing raw/ JSON data.")
    args = p.parse_args()
    analyze_funnel(args.input_dir)


if __name__ == "__main__":
    main()