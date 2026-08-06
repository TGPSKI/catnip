#!/usr/bin/env python3
"""Cosine-similarity traffic clustering with greedy algorithm."""
import argparse
import csv
import json
import math
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


def dot_product(v1, v2):
    return sum(a * b for a, b in zip(v1, v2, strict=False))


def magnitude(v):
    return math.sqrt(sum(x ** 2 for x in v))


def cosine_similarity(v1, v2):
    m1, m2 = magnitude(v1), magnitude(v2)
    if m1 == 0 or m2 == 0:
        return 0.0
    return dot_product(v1, v2) / (m1 * m2)


def _normalize_feature(features, all_values):
    """Normalize a single feature to [0, 1] across all repos."""
    if not all_values:
        return 0.0
    vmin = min(all_values)
    vmax = max(all_values)
    if vmax == vmin:
        return 0.5
    return (features - vmin) / (vmax - vmin) if vmax != vmin else 0.0


def classify_cluster(label_data):
    """Assign a label to a cluster based on its centroid features."""
    ci = label_data.get("clone_intent", 0)
    ar = label_data.get("automation_ratio", 0)
    cei = label_data.get("cei", 0)

    if ci >= 50 and ar <= 1:
        return "developer-tools"
    if ci >= 50 and ar > 1:
        return "developer-other"
    if ci < 20 and cei < 20:
        return "reference-docs"
    if ar > 3:
        return "bot-heavy"
    if ci >= 20 and ci < 50:
        return "tooling"
    return "mixed"


def analyze_clusters(raw):
    raw = Path(raw)

    # Clustering works entirely off the profile and funnel CSVs written by
    # the two stages before it; the repo list is not needed here.
    profile_data = {}
    cloner_path = raw / "analysis" / "traffic_cloner_profile.csv"
    funnel_path = raw / "analysis" / "traffic_funnel.csv"

    if cloner_path.is_file():
        with cloner_path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rname = row.get("repo_name", "")
                profile_data[rname] = {
                    "clone_intent_score": safe_int(row.get("clone_intent_score"), 0),
                    "automation_ratio": float(row.get("automation_ratio", 0)),
                    "edI": float(row.get("edI", 0)),
                    "clone_velocity": float(row.get("clone_velocity", 0)),
                    "view_intensity": float(row.get("view_intensity", 0)),
                    "clone_weekend_pct": float(row.get("clone_weekend_pct", 0)),
                }

    # Load funnel data for CEI and PR engagement
    funnel_rows = {}
    if funnel_path.is_file():
        with funnel_path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rname = row.get("repo_name", "")
                if rname not in funnel_rows:
                    funnel_rows[rname] = {"code_views": 0, "pr_views": 0, "total_views": 0}
                cat = row.get("category", "")
                vc = safe_int(row.get("view_count"), 0)
                funnel_rows[rname]["total_views"] += vc
                if cat in ("code_blob", "src_blob"):
                    funnel_rows[rname]["code_views"] += vc
                if cat in ("pr_detail", "pr_list"):
                    funnel_rows[rname]["pr_views"] += vc

    repos = set(profile_data.keys()) | set(funnel_rows.keys())
    if not repos:
        print("No clustering data available. Run traffic-cloner-profile.py and traffic-funnel.py first.")
        # Write empty output
        out_dir = raw / "analysis"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "traffic_clusters.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump({
                "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "num_repos": 0,
                "num_clusters": 0,
                "clusters": [],
                "orphaned": [],
                "adjacency_matrix": {},
            }, fh, indent=2)
        print(f"  Empty clusters: {out_path}")
        return

    # Build feature vectors per repo
    all_features = defaultdict(list)
    repo_features = {}

    for rname in sorted(repos):
        pd_ = profile_data.get(rname, {})
        fd_ = funnel_rows.get(rname, {})
        tv = fd_.get("total_views", 0) or 1

        clones_per_day = pd_.get("clone_velocity", 0)
        views_per_day = pd_.get("view_intensity", 0)
        cv_ratio = float(pd_.get("clone_intent_score", 0)) / 100.0 if pd_.get("clone_intent_score", 0) > 0 else 0.0
        autom_ratio = float(pd_.get("automation_ratio", 0))
        edI = float(pd_.get("edI", 0))
        weekend_pct = float(pd_.get("clone_weekend_pct", 0))
        cei = round(fd_.get("code_views", 0) / tv * 100, 2)
        pr_eng_pct = round(fd_.get("pr_views", 0) / tv * 100, 2)

        features = {
            "clones_per_day": clones_per_day,
            "views_per_day": views_per_day,
            "clone_to_view_ratio": cv_ratio,
            "automation_ratio": autom_ratio,
            "edI": edI,
            "weekend_pct": weekend_pct,
            "cei": cei,
            "pr_engagement_pct": pr_eng_pct,
        }
        repo_features[rname] = features
        for key, val in features.items():
            all_features[key].append(val)

    # Normalize features
    def normalize_repo(name):
        f = repo_features[name]
        return [_normalize_feature(f[k], all_features[k]) for k in sorted(f.keys())]

    feature_keys = sorted(repo_features[next(iter(repo_features))].keys())

    # Build adjacency matrix (symmetric cosine similarity)
    repo_names = sorted(repo_features.keys())
    norm_vectors = {n: normalize_repo(n) for n in repo_names}
    adjacency = {}

    for n in repo_names:
        adj_row = {}
        for m in repo_names:
            if n == m:
                adj_row[m] = 1.0
            elif m in adjacency and n in adjacency.get(m, {}):
                adj_row[m] = adjacency[m][n]
            else:
                sim = cosine_similarity(norm_vectors[n], norm_vectors[m])
                adj_row[m] = sim
        adjacency[n] = adj_row

    # Greedy clustering
    cluster_threshold = 0.7    # for initial assignment
    merge_threshold = 0.95     # for merging clusters
    assignments = {}  # repo -> cluster_id
    clusters = []  # list of dicts: {id, members[], centroid[], internal_sim[]}
    cluster_id_counter = 0

    unassigned = sorted(repo_names)

    while unassigned:
        best_name = unassigned[0]
        unassigned.remove(best_name)
        assigned_any = False

        for cluster_idx in range(len(clusters)):
            centroids = [[norm_vectors[n][i] for n in clusters[cluster_idx]["members"]]
                         for i in range(len(feature_keys))]
            centroid = [sum(centroids[i]) / len(centroids[i])
                        for i in range(len(feature_keys))]
            sim = cosine_similarity(norm_vectors[best_name], centroid)
            if sim >= cluster_threshold:
                assignments[best_name] = cluster_idx
                clusters[cluster_idx]["members"].append(best_name)
                assigned_any = True
                break

        if not assigned_any:
            new_c = {
                "id": cluster_id_counter,
                "members": [best_name],
                "centroid": norm_vectors[best_name],
            }
            assignments[best_name] = cluster_id_counter
            clusters.append(new_c)
            cluster_id_counter += 1

    # Post-process: merge clusters with high internal similarity
    merged = True
    while merged:
        merged = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                members_i = clusters[i]["members"]
                members_j = clusters[j]["members"]
                internal_sim = 0.0
                count = 0
                for mi in members_i:
                    for mj in members_j:
                        internal_sim += adjacency[mi][mj]
                        count += 1
                avg_sim = internal_sim / count if count > 0 else 0
                if avg_sim >= merge_threshold:
                    clusters[i]["members"].extend(members_j)
                    clusters[j]["members"] = []
                    merged = True
                    break
            if merged:
                break

    # Finalize clusters
    final_clusters = []
    orphaned = []
    for c in clusters:
        members = sorted([m for m in c["members"] if m in assignments])
        if not members:
            continue
        # Compute centroid
        centroid = [0.0] * len(feature_keys)
        for m in members:
            for i in range(len(feature_keys)):
                centroid[i] += norm_vectors[m][i]
        centroid = [v / len(members) for v in centroid]
        c["centroid"] = centroid

        # Compute internal similarity
        internal_sims = []
        for ii in range(len(members)):
            for jj in range(ii + 1, len(members)):
                internal_sims.append(adjacency[members[ii]][members[jj]])
        avg_internal = sum(internal_sims) / len(internal_sims) if internal_sims else 0.0
        c["internal_sim"] = round(avg_internal, 4)

        # Compute avg stats
        avg_stats = {}
        for fk in feature_keys:
            vals = [repo_features[m][fk] for m in members]
            avg_stats[fk] = round(sum(vals) / len(vals), 4) if vals else 0
        label_data = {
            "clone_intent": avg_stats.get("clone_to_view_ratio", 0) * 100,
            "automation_ratio": avg_stats.get("automation_ratio", 0),
            "cei": avg_stats.get("cei", 0),
        }
        label = classify_cluster(label_data)

        final_clusters.append({
            "id": c["id"],
            "label": label,
            "members": members,
            "avg_stats": avg_stats,
            "internal_similarity": avg_internal,
        })

        # Find repos that were merged away
        if members:
            for m in members:
                if m in orphaned:
                    orphaned.remove(m)

    # Repos not in any cluster
    in_clusters = set()
    for c in final_clusters:
        in_clusters.update(c["members"])
    for rname in repo_names:
        if rname not in in_clusters:
            orphaned.append(rname)

    # Build output JSON
    clusters_out = []
    for c in final_clusters:
        members = c["members"]
        cluster_out = dict(c)
        cluster_out["members"] = sorted(members)
        cluster_out["avg_clone_intent"] = round(c["avg_stats"].get("clone_to_view_ratio", 0) * 100, 2)
        cluster_out["avg_clones_per_day"] = round(c["avg_stats"].get("clones_per_day", 0), 2)
        cluster_out["avg_views_per_day"] = round(c["avg_stats"].get("views_per_day", 0), 2)
        cluster_out["avg_automation_ratio"] = round(c["avg_stats"].get("automation_ratio", 0), 4)
        cluster_out["avg_cei"] = round(c["avg_stats"].get("cei", 0), 2)
        clusters_out.append(cluster_out)

    # Adjacency as dict of dicts for output
    adj_out = {}
    for rn in repo_names:
        adj_out[rn] = {m: round(v, 4) for m, v in sorted(adjacency.get(rn, {}).items())}

    output = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "num_repos": len(repo_names),
        "num_clusters": len(final_clusters),
        "clusters": clusters_out,
        "orphaned": sorted(orphaned),
        "adjacency_matrix": adj_out,
    }

    # Write output
    out_dir = raw / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "traffic_clusters.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, default=str)

    print(f"Clustering: {len(repo_names)} repos into {len(final_clusters)} clusters")
    print(f"  JSON: {out_path}")


def main():
    p = argparse.ArgumentParser(description="Traffic clustering analysis.")
    p.add_argument("input_dir", type=Path, help="Directory containing analysis/ and raw/ data.")
    args = p.parse_args()
    analyze_clusters(args.input_dir)


if __name__ == "__main__":
    main()