#!/usr/bin/env python3
"""Turn one run's raw GitHub JSON into the CSVs the TUI and the history store read.

Input is a run directory containing `raw/`; output is `analysis/` beside
it — sixteen CSVs plus a human-readable summary.md. Everything downstream
(history ingest, totals, every TUI view) reads these files and never the
raw JSON, so this module is the schema boundary of the project.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_json(path):
    if not path.is_file():
        return None
    # A 0-byte body is an expected outcome for a repo whose GitHub stats/*
    # endpoint never populated (perpetual HTTP 202). Treat it as "no data",
    # silently — the WARN below is reserved for genuinely malformed JSON.
    if path.stat().st_size == 0:
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARN: unreadable JSON {path}: {exc}", file=sys.stderr)
        return None


def gh_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def safe_int(val, default=0):
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError, OverflowError):
        return default


def delta_days(d1, d2):
    if d1 is None or d2 is None:
        return 999
    return int((d2 - d1).total_seconds() / 86400)


def slug_for(name):
    s = name.replace("/", "_").replace("-", "--")
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", s)


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def analyze_github(raw, strict=False):
    raw = Path(raw)
    now = datetime.now(timezone.utc)
    org_repos = load_json(raw / "raw/org_repos.json") or []
    repos_data, lang_dist_rows, code_freq_rows, pr_rows, repo_rel_out, issue_rows, contrib_out = [], [], [], [], [], [], []
    asset_rows, referrer_rows, star_rows, fork_rows = [], [], [], []
    commit_daily_rows = []
    lang_bytes_g = Counter()
    pt_g = pm_g = rt_g = ct_g = 0
    norm = {}

    for ro in org_repos:
        rn = ro.get("name", "unknown")
        rs = slug_for(rn)
        # Fall back to the org-list entry (fetched completely up front) when the
        # per-repo detail fetch is missing/failed. `ro` already carries
        # stargazers_count / forks_count / watchers / pushed_at / license /
        # topics, so a partial or interrupted fetch no longer fabricates 0-star
        # rows — which used to poison cross-fetch deltas (a repo appearing to
        # lose all 72 of its stars). Detail metadata still wins when present.
        m = load_json(raw / f"raw/repo_{rs}.json") or ro
        archived = bool(m.get("archived"))
        # Carried through to the CSV so the viewer can separate what you
        # wrote from what you forked. A fork's commit history and language
        # bytes are upstream's, and mixing them into "your languages" or
        # "most commits" answers a question nobody asked.
        is_fork = bool(m.get("fork"))
        is_private = bool(m.get("private"))
        readme_f = (raw / f"raw/repo_{rs}_readme.md").is_file()
        li = m.get("license")
        lic = li.get("spdx_id", "") if isinstance(li, dict) else ""
        topics = m.get("topics") or []

        lo = load_json(raw / f"raw/repo_{rs}_langs.json")
        lbs = {}
        if isinstance(lo, dict):
            for lang, bv in lo.items():
                lbs[lang] = safe_int(bv)
        if lbs:
            tb = sum(lbs.values())
            for lang, b in lbs.items():
                p = round(b / tb * 100, 2) if tb else 0.0
                lang_dist_rows.append({"repo_name": rn, "language": lang, "bytes": b, "bytes_percentage": p})
                lang_bytes_g[lang] += b
        lc = len(lbs)

        # Per-week series merged from two GitHub stats endpoints:
        #   stats/commit_activity items are {days:[7 ints], total, week(epoch)}
        #     — the source of per-week COMMIT counts.
        #   stats/code_frequency items are [epoch, additions, deletions] (3-elem)
        #     — the source of additions/deletions. (The old code required a 4th
        #     "commits" element here and so skipped every row — code_frequency
        #     never has one — which is why this view was always empty.)
        weekly = {}   # epoch -> {"commits","additions","deletions"}
        co = load_json(raw / f"raw/repo_{rs}_commits.json")
        tc = tw = aw = 0
        if isinstance(co, list):
            for week in co:
                if not isinstance(week, dict): continue
                wc = safe_int(week.get("total"), 0)
                tc += wc; tw += 1
                if wc > 0: aw += 1
                ep = safe_int(week.get("week"), 0)
                if ep:
                    weekly.setdefault(ep, {"commits": 0, "additions": 0, "deletions": 0})["commits"] += wc
                # commit_activity's `days` is seven daily counts starting on
                # the Sunday named by `week`. Collapsing it to the weekly
                # total throws away the only DAILY authorship signal catnip
                # collects — the one thing that can put a cause under a
                # traffic spike ("that Thursday's clones follow that
                # Thursday's push") instead of near it.
                days = week.get("days")
                if ep and isinstance(days, list):
                    for i, dc in enumerate(days[:7]):
                        n = safe_int(dc, 0)
                        if n <= 0: continue
                        try:
                            d = (datetime.fromtimestamp(int(ep), tz=timezone.utc)
                                 + timedelta(days=i)).strftime("%Y-%m-%d")
                        except (ValueError, TypeError, OSError):
                            continue
                        commit_daily_rows.append({"repo_name": rn, "date": d, "commits": n})
        avg_w = round(tc / max(1, tw), 1)
        ct_g += tc

        fo = load_json(raw / f"raw/repo_{rs}_freq.json")
        if isinstance(fo, list):
            for e in fo:
                if not isinstance(e, list) or len(e) < 3: continue
                ep = safe_int(e[0], 0)
                if not ep: continue
                w = weekly.setdefault(ep, {"commits": 0, "additions": 0, "deletions": 0})
                w["additions"] += safe_int(e[1]); w["deletions"] += safe_int(e[2])

        for ep, w in weekly.items():
            ai, di, ci = w["additions"], w["deletions"], w["commits"]
            if ai + di + ci == 0: continue
            try: wl = datetime.fromtimestamp(int(ep), tz=timezone.utc).strftime("%Y-W%U")
            except (ValueError, TypeError, OSError): wl = str(ep)
            code_freq_rows.append({"repo_name": rn, "week_epoch": int(ep), "week_label": wl,
                                   "additions": ai, "deletions": di, "total": ai + di, "commits": ci})

        cso = load_json(raw / f"raw/repo_{rs}_contstats.json")
        rcl = []
        if isinstance(cso, list):
            for cs in cso:
                if not isinstance(cs, dict): continue
                au = cs.get("author")
                if not isinstance(au, dict): continue
                login = au.get("login", "unknown")
                td = cs.get("total", {})
                if not isinstance(td, dict): td = {}
                con = safe_int(td.get("total", cs.get("contributions", 0)))
                wes = safe_int(td.get("weeks", cs.get("weeks", 0)))
                e2 = {"repo_name": rn, "contributor_login": login, "contributions": con, "weeks": wes, "avg_weekly": round(con / max(1, wes), 1)}
                rcl.append(e2); contrib_out.append(dict(e2))
        tcon = len(rcl)

        cto = load_json(raw / f"raw/repo_{rs}_contributors.json")
        tcl_login, tcc = "", 0
        if isinstance(cto, list):
            for c in cto:
                if not isinstance(c, dict): continue
                clogin = c.get("login", "unknown")
                cc = safe_int(c.get("contributions", 0))
                if cc > tcc: tcc = cc; tcl_login = clogin
        po = load_json(raw / f"raw/repo_{rs}_pulls.json")
        pt = po2 = pc = pm = 0
        if isinstance(po, list):
            for pr in po:
                if not isinstance(pr, dict): continue
                pt += 1
                ps = pr.get("state", "unknown")
                if ps == "open": po2 += 1
                elif ps == "closed": pc += 1
                # The pulls list endpoint has merged_at but never a merged
                # bool.
                merged = bool(pr.get("merged_at"))
                if merged: pm += 1; pm_g += 1
                pr_rows.append({"repo_name": rn, "pr_number": safe_int(pr.get("number", 0)), "title": (pr.get("title") or "").replace(",", ";"), "state": ps, "merged": "true" if merged else "false", "merged_at": pr.get("merged_at") or "", "created_at": pr.get("created_at", ""), "closed_at": pr.get("closed_at") or ""})
        pt_g += pt
        ro2 = load_json(raw / f"raw/repo_{rs}_releases.json")
        rcc = 0; lt_d = ""; ltd_date = ""; repo_downloads = 0
        if isinstance(ro2, list):
            for rel in ro2:
                if not isinstance(rel, dict): continue
                tag = rel.get("tag_name", "")
                nm = (rel.get("name") or "")[:200].replace(",", ";")
                pub = rel.get("published_at", "")
                draft = bool(rel.get("draft"))
                pre = bool(rel.get("prerelease"))
                rcc += 1
                if not draft and pub and (ltd_date == "" or pub > ltd_date): lt_d = tag; ltd_date = pub
                repo_rel_out.append({"repo_name": rn, "tag_name": tag, "name": nm, "published_at": pub, "draft": str(draft), "prerelease": str(pre), "total_releases": rcc})
                # Release-asset downloads: the only all-time traffic signal
                # GitHub exposes (plan 08 phase 4).
                for asset in (rel.get("assets") or []):
                    if not isinstance(asset, dict): continue
                    dc = safe_int(asset.get("download_count", 0))
                    repo_downloads += dc
                    asset_rows.append({"repo_name": rn, "tag_name": tag,
                                       "asset": asset.get("name", ""),
                                       "download_count": dc,
                                       "created_at": asset.get("created_at", "")})
        rt_g += rcc

        # Optional expanded-data files (present after a phase-2 fetch).
        rf = load_json(raw / f"raw/repo_{rs}_referrers.json")
        if isinstance(rf, list):
            for ref in rf:
                if not isinstance(ref, dict): continue
                referrer_rows.append({"repo_name": rn,
                                      "referrer": ref.get("referrer", ""),
                                      "count": safe_int(ref.get("count", 0)),
                                      "uniques": safe_int(ref.get("uniques", 0))})
        sg = load_json(raw / f"raw/repo_{rs}_stargazers.json")
        if isinstance(sg, list):
            for ev in sg:
                if isinstance(ev, dict) and ev.get("starred_at"):
                    star_rows.append({"repo_name": rn, "starred_at": ev["starred_at"]})
        fk = load_json(raw / f"raw/repo_{rs}_forks.json")
        if isinstance(fk, list):
            for ev in fk:
                if isinstance(ev, dict) and ev.get("created_at"):
                    fork_rows.append({"repo_name": rn, "created_at": ev["created_at"]})
        io2 = load_json(raw / f"raw/repo_{rs}_issues.json")
        if isinstance(io2, list):
            for iss in io2:
                if not isinstance(iss, dict): continue
                if iss.get("pull_request") is not None: continue
                title = (iss.get("title") or "")[:200].replace(",", ";")
                issue_rows.append({"repo_name": rn, "number": safe_int(iss.get("number", 0)), "title": title, "state": iss.get("state", "unknown"), "created_at": iss.get("created_at", ""), "closed_at": iss.get("closed_at") or "", "is_pr": "false"})
        cd = load_json(raw / f"raw/repo_{rs}_clones.json")
        tclones = tuc = 0
        if isinstance(cd, dict): tclones = safe_int(cd.get("count", 0)); tuc = safe_int(cd.get("uniques", 0))
        vd2 = load_json(raw / f"raw/repo_{rs}_views.json")
        tvus = tutv = 0
        if isinstance(vd2, dict): tvus = safe_int(vd2.get("count", 0)); tutv = safe_int(vd2.get("uniques", 0))
        pd2 = load_json(raw / f"raw/repo_{rs}_paths.json")
        tpaths = 0; tpath_ = ""; tpc = 0
        if isinstance(pd2, list) and len(pd2) > 0:
            tpaths = len(pd2)
            if isinstance(pd2[0], dict): tpath_ = pd2[0].get("path", ""); tpc = safe_int(pd2[0].get("count", 0))
        if archived: status = "archived"
        elif delta_days(gh_date(m.get("pushed_at")), now) > 365: status = "stale"
        else: status = "active"
        rd = delta_days(gh_date(m.get("created_at")), now)
        if rd < 183: ag = "fresh"
        elif rd < 730: ag = "young"
        elif rd < 1825: ag = "mature"
        else: ag = "old"
        norm[rn] = {"stars": safe_int(m.get("stargazers_count", 0)), "forks": safe_int(m.get("forks_count", 0)), "tc": tc, "tcn": tcon, "lc": lc, "ds": delta_days(gh_date(m.get("pushed_at")), now), "tcl": tclones, "tv": tvus, "tp": tpaths, "tuc": tuc, "tuv": tutv, "tw": tw, "aw": aw}
        repos_data.append({
            "name": rn, "html_url": m.get("html_url", ""),
            "description": (m.get("description") or "")[:500],
            "language": m.get("language") or "",
            "language_bytes": lbs, "languages_count": lc,
            "size_kb": safe_int(m.get("size")),
            "stars": safe_int(m.get("stargazers_count")),
            "forks": safe_int(m.get("forks_count")),
            # GitHub's watchers_count == stars; subscribers_count is the
            # real watcher count.
            "watchers": safe_int(m.get("subscribers_count")),
            "open_issues": safe_int(m.get("open_issues_count", 0)),
            "default_branch": m.get("default_branch", ""),
            "created_at": m.get("created_at", ""),
            "updated_at": m.get("updated_at", ""),
            "pushed_at": m.get("pushed_at", ""),
            "archived": archived,
            "is_fork": is_fork,
            "is_private": is_private,
            "has_wiki": bool(m.get("has_wiki")),
            "has_discussions": bool(m.get("has_discussions")),
            "has_pages": bool(m.get("has_pages")),
            "has_downloads": bool(m.get("has_downloads")),
            "has_readme": bool(m.get("readme")) if isinstance(m.get("readme"), dict) else readme_f,
            "license": lic, "topics": topics,
            "allow_forking": bool(m.get("allow_forking")),
            "repo_age_days": rd,
            "days_since_push": delta_days(gh_date(m.get("pushed_at")), now),
            "total_commits": tc, "total_weeks": tw, "active_weeks": aw,
            "avg_weekly_commits": avg_w,
            "total_contributors": tcon,
            "top_contributor_login": tcl_login,
            "top_contributor_contributions": tcc,
            "pull_total": pt, "pull_open": po2,
            "pull_closed": pc, "pull_merged": pm,
            "release_count": rcc, "total_downloads": repo_downloads,
            "latest_release_tag": lt_d, "latest_release_date": ltd_date,
            "traffic_score": 0.0, "activity_score": 0.0,
            "total_clones": tclones, "total_unique_cloners": tuc,
            "total_views": tvus, "total_unique_views": tutv,
            "total_paths": tpaths, "top_path": tpath_,
            "top_path_count": tpc,
            "status": status, "age_group": ag,
        })

    # scoring
    vals = list(norm.values())
    mx = {k: max((v[k] for v in vals), default=1) or 1 for k in ("stars", "forks", "tc", "tcn", "lc", "tcl", "tuc", "tv", "tuv", "tp")}
    for r in repos_data:
        ds = r["days_since_push"] + 1; rt = 1.0 / ds
        r["traffic_score"] = round((r["total_clones"]/mx["tcl"])*0.35 + (r["total_unique_cloners"]/mx["tuc"])*0.25 + (r["total_views"]/mx["tv"])*0.20 + (r["total_unique_views"]/mx["tuv"])*0.10 + (r["total_paths"]/mx["tp"])*0.05 + rt*0.05, 6)
        r["activity_score"] = round((r["stars"]/mx["stars"])*0.25 + (r["forks"]/mx["forks"])*0.15 + (r["total_commits"]/mx["tc"])*0.30 + (r["total_contributors"]/mx["tcn"])*0.15 + (r["languages_count"]/mx["lc"])*0.05 + rt*0.05, 6)
    repos_data.sort(key=lambda r: (-r["traffic_score"], r["name"]))

    # ---- deep traffic analysis ----
    # Each stage is a separate process on purpose: a stage that crashes or
    # hangs on one pathological repo costs its own output, not the whole
    # analysis. Order matters — traffic_cluster consumes the profile and
    # funnel CSVs written by the two stages before it.
    traffic_modules = [
        ("catnip.traffic_anomaly", "Anomaly detection -> traffic_anomaly.csv"),
        ("catnip.traffic_profile", "Clone profiles -> traffic_cloner_profile.csv"),
        ("catnip.traffic_funnel", "Content funnel -> traffic_funnel.csv"),
        ("catnip.traffic_correlation", "Cross-repo correlation -> traffic_correlation.csv"),
        ("catnip.traffic_cluster", "Traffic clustering -> traffic_cluster.json"),
    ]
    # Subprocesses need src/ importable even when analyze.py was invoked by
    # path rather than as `python -m catnip.analyze`.
    env = dict(os.environ)
    src_dir = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = src_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    traffic_failures = 0
    for module, desc in traffic_modules:
        try:
            result = subprocess.run(
                [sys.executable, "-m", module, str(raw)],
                capture_output=True, text=True, timeout=300, env=env, check=False,
            )
            if result.stdout.strip():
                print(f"  {desc}: {result.stdout.strip()}")
            if result.returncode != 0:
                traffic_failures += 1
                if result.stderr.strip():
                    print(f"  {desc}: WARNING - {result.stderr.strip()[:400]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            traffic_failures += 1
            print(f"  {desc}: TIMEOUT after 300s", file=sys.stderr)
        except OSError as exc:
            traffic_failures += 1
            print(f"  {desc}: ERROR - {exc}", file=sys.stderr)
    if traffic_failures:
        # Not fatal by default: the core CSVs are already written and the
        # TUI degrades to "this view has no data". --strict makes it fatal
        # for automated runs, where a silent gap is worse than a failure.
        print(f"WARNING: {traffic_failures} traffic stage(s) failed", file=sys.stderr)

    ad = raw / "analysis"
    ad.mkdir(parents=True, exist_ok=True)
    # Dropped in plan 08 (long-format github_traffic_timeseries.csv is the
    # canonical traffic series); remove stale copies from earlier runs.
    for stale in ("github_traffic_clones.csv", "github_traffic_views.csv"):
        (ad / stale).unlink(missing_ok=True)

    ro2 = []
    for r in repos_data:
        lb = ";".join(f"{k}:{v}" for k, v in sorted(r["language_bytes"].items()))
        tp2 = "|".join(r["topics"])
        ro2.append({
            "repo_name": r["name"], "html_url": r["html_url"],
            "description": r["description"], "language": r["language"],
            "languages_count": r["languages_count"], "languages_bytes": lb,
            "size_kb": r["size_kb"], "stars": r["stars"], "forks": r["forks"],
            "watchers": r["watchers"], "open_issues": r["open_issues"],
            "default_branch": r["default_branch"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "pushed_at": r["pushed_at"],
            "archived": str(r["archived"]), "is_fork": str(r["is_fork"]),
            "is_private": str(r["is_private"]), "has_wiki": str(r["has_wiki"]),
            "has_discussions": str(r["has_discussions"]),
            "has_pages": str(r["has_pages"]),
            "has_downloads": str(r["has_downloads"]),
            "has_readme": str(r["has_readme"]), "license": r["license"],
            "topics": tp2, "allow_forking": str(r["allow_forking"]),
            "total_commits": r["total_commits"], "total_weeks": r["total_weeks"],
            "active_weeks": r["active_weeks"],
            "avg_weekly_commits": r["avg_weekly_commits"],
            "total_contributors": r["total_contributors"],
            "top_contributor_login": r["top_contributor_login"],
            "top_contributor_contributions": r["top_contributor_contributions"],
            "pull_total": r["pull_total"], "pull_open": r["pull_open"],
            "pull_merged": r["pull_merged"], "pull_closed": r["pull_closed"],
            "release_count": r["release_count"],
            "total_downloads": r["total_downloads"],
            "latest_release_tag": r["latest_release_tag"],
            "latest_release_date": r["latest_release_date"],
            "repo_age_days": r["repo_age_days"],
            "days_since_push": r["days_since_push"],
            "traffic_score": r["traffic_score"],
            "activity_score": r["activity_score"],
            "total_clones": r["total_clones"],
            "total_unique_cloners": r["total_unique_cloners"],
            "total_views": r["total_views"],
            "total_unique_views": r["total_unique_views"],
            "total_paths": r["total_paths"],
            "top_path": r["top_path"], "top_path_count": r["top_path_count"],
            "status": r["status"], "age_group": r["age_group"],
        })
    write_csv(ad / "github_repos.csv", list(ro2[0].keys()) if ro2 else [], ro2)
    write_csv(ad / "github_languages.csv", ["repo_name","language","bytes","bytes_percentage"], lang_dist_rows)
    write_csv(ad / "github_contributions.csv", ["repo_name","contributor_login","contributions","weeks","avg_weekly"], contrib_out)
    code_freq_rows.sort(key=lambda x: (x["repo_name"], x["week_epoch"]))
    write_csv(ad / "github_code_frequency.csv", ["repo_name","week_epoch","week_label","additions","deletions","total","commits"], code_freq_rows)
    commit_daily_rows.sort(key=lambda x: (x["repo_name"], x["date"]))
    write_csv(ad / "github_commit_daily.csv", ["repo_name","date","commits"], commit_daily_rows)
    pr_rows.sort(key=lambda x: (x["repo_name"], x["pr_number"]))
    write_csv(ad / "github_pull_requests.csv", ["repo_name","pr_number","title","state","merged","merged_at","created_at","closed_at"], pr_rows)
    write_csv(ad / "github_releases.csv", ["repo_name","tag_name","name","published_at","draft","prerelease","total_releases"], repo_rel_out)
    issue_rows.sort(key=lambda x: (x["repo_name"], x["number"]))
    write_csv(ad / "github_issues.csv", ["repo_name","number","title","state","created_at","closed_at","is_pr"], issue_rows)
    gtb = sum(lang_bytes_g.values()) or 1
    ld_out = []
    for lang, tb in lang_bytes_g.most_common():
        rc = sum(1 for lr in lang_dist_rows if lr["language"] == lang)
        ld_out.append({"language": lang, "total_bytes": tb, "total_bytes_pct": round(tb / gtb * 100, 2), "repo_count": rc})
    write_csv(ad / "github_lang_distribution.csv", ["language","total_bytes","total_bytes_pct","repo_count"], ld_out)
    tro = []
    for crit, f in [("stars","stars"),("forks","forks"),("contributors","total_contributors"),("activity","activity_score"),("recent","days_since_push"),("age","repo_age_days"),("clones","total_clones"),("traffic","traffic_score")]:
        for i, r in enumerate(sorted(repos_data, key=lambda r: -r[f])[:50], 1):
            tro.append({"rank": i, "sort_criteria": crit, "repo_name": r["name"], "value": r[f], "stars": r["stars"], "forks": r["forks"]})
    write_csv(ad / "github_top_repos.csv", ["rank","sort_criteria","repo_name","value","stars","forks"], tro)
    st2 = []
    for r in repos_data:
        st2.append({"repo_name": r["name"], "stars": r["stars"], "forks": r["forks"], "watchers": r["watchers"], "total_downloads": r["total_downloads"], "open_issues": r["open_issues"], "repo_age_days": r["repo_age_days"], "days_since_push": r["days_since_push"], "total_commits": r["total_commits"], "total_contributors": r["total_contributors"], "traffic_score": r["traffic_score"], "activity_score": r["activity_score"], "total_clones": r["total_clones"], "total_unique_cloners": r["total_unique_cloners"], "total_views": r["total_views"], "total_unique_views": r["total_unique_views"], "total_paths": r["total_paths"], "primary_language": r["language"] or "-", "languages_count": r["languages_count"], "archived": str(r["archived"]), "is_fork": str(r["is_fork"]), "status": r["status"]})
    st2.sort(key=lambda x: -float(x.get("traffic_score", 0)))
    write_csv(ad / "github_stats_by_repo.csv", list(st2[0].keys()) if st2 else [], st2)
    vel = []
    for r in repos_data:
        rs2 = slug_for(r["name"])
        cd2 = load_json(raw / f"raw/repo_{rs2}_clones.json") or {}
        vd2 = load_json(raw / f"raw/repo_{rs2}_views.json") or {}
        clw_list = cd2.get("clones", []) if isinstance(cd2, dict) else []
        v_list = vd2.get("views", []) if isinstance(vd2, dict) else []
        def gwl(sl, idx):
            return safe_int(sl[idx].get("count", 0)) if 0 <= idx < len(sl) and isinstance(sl[idx], dict) else 0
        def fpeak(sl, k, f):
            best = ("", 0)
            for it in sl:
                if isinstance(it, dict) and it.get(k):
                    c = safe_int(it.get(f, 0))
                    if c > best[1]: best = (it.get(k, ""), c)
            return best
        clw = gwl(clw_list, -1); clbw = gwl(clw_list, -2)
        vld = gwl(v_list, -1); vlbw = gwl(v_list, -2)
        cg = (clw - clbw) / max(clbw, 1) * 100 if clbw > 0 else (100.0 if clw > 0 else 0.0)
        vg = (vld - vlbw) / max(vlbw, 1) * 100 if vlbw > 0 else (100.0 if vld > 0 else 0.0)
        pk_c = fpeak(clw_list, "timestamp", "count")
        pk_v = fpeak(v_list, "timestamp", "count")
        vel.append({"repo_name": r["name"], "total_clones": r["total_clones"], "clones_last_day": clw, "clones_prior_day": clbw, "clones_growth_pct": round(cg, 2), "total_views": r["total_views"], "views_last_day": vld, "views_prior_day": vlbw, "views_growth_pct": round(vg, 2), "peak_clones_day": pk_c[0], "peak_views_day": pk_v[0], "peak_clones_count": pk_c[1], "peak_views_count": pk_v[1]})
    vel.sort(key=lambda x: (-x["clones_growth_pct"], -x["total_clones"]))
    write_csv(ad / "github_traffic_velocity.csv", list(vel[0].keys()) if vel else [], vel)
    # Tidy daily traffic time-series (all ~14 daily buckets GitHub returns, for
    # both clones and views). The fixed-column CSVs above truncate to 4/7 oldest
    # buckets, which drops the most recent (and often spikiest) days; the TUI
    # graphs read this long-format file instead so nothing is lost.
    ts_out = []
    for r in repos_data:
        rsl = slug_for(r["name"])
        for metric, fname, key in (("clones", "clones", "clones"), ("views", "views", "views")):
            obj = load_json(raw / f"raw/repo_{rsl}_{fname}.json") or {}
            series = obj.get(key, []) if isinstance(obj, dict) else []
            for it in series:
                if not isinstance(it, dict):
                    continue
                ts = it.get("timestamp", "")
                if not ts:
                    continue
                ts_out.append({"repo_name": r["name"], "metric": metric,
                               "timestamp": ts, "count": safe_int(it.get("count", 0)),
                               "uniques": safe_int(it.get("uniques", 0))})
    ts_out.sort(key=lambda x: (x["repo_name"], x["metric"], x["timestamp"]))
    write_csv(ad / "github_traffic_timeseries.csv",
              ["repo_name", "metric", "timestamp", "count", "uniques"], ts_out)

    pr_out2 = []
    for r in repos_data:
        rs5 = slug_for(r["name"])
        pd3 = load_json(raw / f"raw/repo_{rs5}_paths.json")
        if isinstance(pd3, list):
            np2 = len(pd3)
            for p in pd3:
                if isinstance(p, dict):
                    pr_out2.append({"repo_name": r["name"], "path": p.get("path", ""), "title": p.get("title", ""), "count": safe_int(p.get("count", 0)), "uniques": safe_int(p.get("uniques", 0)), "total_paths_in_repo": np2})
    pr_out2.sort(key=lambda x: (-x["count"], x["repo_name"]))
    write_csv(ad / "github_traffic_paths.csv", ["repo_name","path","title","count","uniques","total_paths_in_repo"], pr_out2)

    asset_rows.sort(key=lambda x: (-x["download_count"], x["repo_name"], x["tag_name"]))
    write_csv(ad / "github_release_assets.csv", ["repo_name","tag_name","asset","download_count","created_at"], asset_rows)
    referrer_rows.sort(key=lambda x: (-x["count"], x["repo_name"]))
    write_csv(ad / "github_traffic_referrers.csv", ["repo_name","referrer","count","uniques"], referrer_rows)
    star_rows.sort(key=lambda x: (x["repo_name"], x["starred_at"]))
    write_csv(ad / "github_stargazer_events.csv", ["repo_name","starred_at"], star_rows)
    fork_rows.sort(key=lambda x: (x["repo_name"], x["created_at"]))
    write_csv(ad / "github_fork_events.csv", ["repo_name","created_at"], fork_rows)


    L = []
    L.append("# catnip — run summary")
    L.append("")
    L.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append("")
    L.append("## Organization Stats")
    L.append("")
    L.append("| Metric | Value |")
    L.append("| --- | --- |")
    L.append(f"| Total repos | {len(repos_data)} |")
    L.append(f"| Total stars | {sum(r['stars'] for r in repos_data)} |")
    L.append(f"| Total forks | {sum(r['forks'] for r in repos_data)} |")
    L.append(f"| Total watchers | {sum(r['watchers'] for r in repos_data)} |")
    L.append(f"| Total open_issues | {sum(r['open_issues'] for r in repos_data)} |")
    L.append(f"| Total total_clones | {sum(r['total_clones'] for r in repos_data)} |")
    L.append(f"| Total total_views | {sum(r['total_views'] for r in repos_data)} |")
    L.append(f"| Total pull requests | {pt_g} |")
    L.append(f"| Total merged PRs | {pm_g} |")
    L.append(f"| Total releases | {rt_g} |")
    L.append(f"| Total commits (stats endpoint) | {ct_g} |")
    L.append("")
    L.append("## Top 10 Repos by Traffic Score")
    L.append("")
    L.append("| Rank | Repo | Stars | Clones | Views | Traffic |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(repos_data[:10], 1):
        L.append(f"| {i} | [{r['name']}](<{r['html_url']}>) | {r['stars']} | {r['total_clones']} | {r['total_views']} | {r['traffic_score']:.4f} |")
    L.append("")
    L.append("## Language Distribution")
    L.append("")
    if lang_bytes_g:
        for lang, tb in lang_bytes_g.most_common():
            pct = round(tb / gtb * 100, 1) if gtb else 0
            L.append(f"- **{lang}**: {tb:,} bytes ({pct}%)")
    else:
        L.append("_No language data available._")
    L.append("")
    L.append("## Age Groups")
    L.append("")
    for ag in ("fresh", "young", "mature", "old"):
        cnt = sum(1 for r in repos_data if r["age_group"] == ag)
        L.append(f"- **{ag}**: {cnt}")
    L.append("")
    L.append("## Status Distribution")
    L.append("")
    ac = sum(1 for r in repos_data if r["status"] == "active")
    sl = sum(1 for r in repos_data if r["status"] == "stale")
    ar = sum(1 for r in repos_data if r["archived"])
    L.append(f"- **active**: {ac}  - **stale**: {sl}  - **archived**: {ar}")
    L.append("")
    L.append("## Most Recently Pushed")
    L.append("")
    for i, r in enumerate(sorted(repos_data, key=lambda r: r["pushed_at"] or "", reverse=True)[:10], 1):
        pa = r["pushed_at"] or "never"
        name_v = r["name"]
        dsp_v = r["days_since_push"]
        L.append(f"| {i} | {name_v} | {pa} | {dsp_v} |")
    L.append("")
    L.append("## Pull Request Activity")
    L.append("")
    for i, r in enumerate(sorted(repos_data, key=lambda r: -r["pull_total"])[:10], 1):
        L.append(f"| {i} | {r['name']} | {r['pull_total']} | {r['pull_open']} | {r['pull_merged']} | {r['pull_closed']} |")
    L.append("")
    L.append("## Release Count")
    L.append("")
    L.append(f"Total releases across {len(repos_data)} repos: {rt_g}")
    L.append("")
    L.append("## Highlights")
    L.append("")
    if repos_data:
        top1 = repos_data[0]
        L.append(f"- Highest traffic: repo {top1['name']} at {top1['traffic_score']:.4f}")
        newest = sorted(repos_data, key=lambda r: r["pushed_at"] or "", reverse=True)[0]
        L.append(f"- Most recently pushed: {newest['name']} ({newest['days_since_push']}d ago)")
    L.append("")
    L.append("## Clone / View Counts")
    L.append("")
    sc = sum(r["total_clones"] for r in repos_data)
    sv = sum(r["total_views"] for r in repos_data)
    L.append(f"- Total clones: {sc}")
    L.append(f"- Total views: {sv}")
    L.append("")
    L.append("| Repo | Clones | Views |")
    L.append("| --- | --- | --- |")
    for r in sorted(repos_data, key=lambda r: r["total_clones"], reverse=True)[:5]:
        L.append(f"| {r['name']} | {r['total_clones']} | {r['total_views']} |")
    L.append("")
    L.append("## Popular Paths")
    L.append("")
    if pr_out2:
        L.append(f"Tracked {len(pr_out2)} unique paths across all repos")
        L.append("")
        L.append("| Path | Count | Repo |")
        L.append("| --- | --- | --- |")
        for p in sorted(pr_out2, key=lambda x: -x["count"])[:10]:
            L.append(f"| {p['path']} | {p['count']} | {p['repo_name']} |")
    L.append("")
    with (ad / "summary.md").open("w") as outf:
        outf.write("\n".join(L) + "\n")

    print(f"Analyzed {len(repos_data)} repos, wrote {ad}/")
    for f in sorted(ad.iterdir()):
        print(f"  {f.name}")
    if strict and traffic_failures:
        raise SystemExit(1)


def main(argv=None):
    p = argparse.ArgumentParser(description="Analyze one catnip run directory.")
    p.add_argument("run_dir", type=Path, nargs="?",
                   help="Run directory containing raw/ (default: newest run from config).")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 if any deep-traffic stage fails.")
    args = p.parse_args(argv)

    run_dir = args.run_dir
    if run_dir is None:
        from catnip.config import Config, ConfigError
        try:
            run_dir = Config.load(args.config).latest_run()
        except ConfigError as exc:
            print(f"catnip: config error: {exc}", file=sys.stderr)
            return 2
        if run_dir is None:
            print("catnip: no runs found. Run `catnip fetch` first.", file=sys.stderr)
            return 1
    if not (run_dir / "raw").is_dir():
        print(f"catnip: {run_dir} has no raw/ directory — not a run directory.", file=sys.stderr)
        return 1

    analyze_github(run_dir, strict=args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
