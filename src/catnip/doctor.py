#!/usr/bin/env python3
"""Preflight and health checks — the first command to run when anything is wrong.

Every check returns one of PASS / WARN / FAIL plus a fix line, and the
whole report is available as JSON (``--json``) so an agent triaging an
incident can read the environment in one call instead of ten.

The checks are ordered the way an investigation should go: identity and
credentials first (which account is this even?), then reachability, then
data. Most catnip failures are coordinate mismatches — the config being
read is not the config being edited, or the token belongs to a different
account than the configured owner — so those are checked before anything
that could be blamed on GitHub.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from catnip.config import Config, ConfigError, candidate_paths, run_dirs

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# Every CSV the TUI reads. Kept here as an explicit contract: a deep
# traffic stage can fail inside `catnip analyze` with only a warning, and
# the sole other symptom is a silently empty panel three days later.
TUI_CSVS = [
    "github_repos.csv", "github_stats_by_repo.csv", "github_languages.csv",
    "github_code_frequency.csv", "github_commit_daily.csv",
    "github_pull_requests.csv", "github_releases.csv",
    "github_lang_distribution.csv", "github_top_repos.csv",
    "github_traffic_timeseries.csv", "github_traffic_paths.csv",
    "github_traffic_referrers.csv", "github_release_assets.csv",
    "traffic_anomaly.csv", "traffic_cloner_profile.csv", "traffic_funnel.csv",
    "traffic_correlation.csv",
]


class Report:
    def __init__(self):
        self.checks = []

    def add(self, name, status, detail, fix=""):
        self.checks.append({"check": name, "status": status, "detail": detail, "fix": fix})
        return status

    @property
    def failed(self):
        return [c for c in self.checks if c["status"] == FAIL]

    @property
    def warned(self):
        return [c for c in self.checks if c["status"] == WARN]

    def render(self, stream=None):
        # Resolved at call time, not at def time, so a caller that has
        # redirected stdout (a test, or `catnip run --quiet`) actually
        # captures the report.
        stream = sys.stdout if stream is None else stream
        icon = {PASS: "✓", WARN: "!", FAIL: "✗"}
        width = max((len(c["check"]) for c in self.checks), default=10)
        for c in self.checks:
            print(f"  {icon[c['status']]} {c['check']:<{width}}  {c['detail']}", file=stream)
            if c["fix"] and c["status"] != PASS:
                print(f"    {' ' * width}  → {c['fix']}", file=stream)
        print(file=stream)
        if self.failed:
            print(f"{len(self.failed)} failed, {len(self.warned)} warning(s).", file=stream)
        elif self.warned:
            print(f"All required checks passed, {len(self.warned)} warning(s).", file=stream)
        else:
            print("All checks passed.", file=stream)


def _run(cmd, timeout=30):
    """Run a command, returning (returncode, stdout, stderr) — never raising."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{cmd[0]}: timed out after {timeout}s"


def check_environment(rep: Report, cfg: Config | None, cfg_error: str = "") -> str | None:
    """Interpreter, tooling, credentials, and identity. Returns the owner
    if it could be resolved."""
    v = sys.version_info
    rep.add("python", PASS if v >= (3, 10) else FAIL,
            f"{v.major}.{v.minor}.{v.micro}",
            "catnip needs Python 3.10 or newer.")

    try:
        import curses  # noqa: F401
        rep.add("curses", PASS, "available")
    except ImportError:
        rep.add("curses", WARN, "not available",
                "The pipeline still runs; only `catnip tui` needs curses. "
                "On Debian/Ubuntu: apt install python3-curses.")

    # ---- config coordinates: which file is actually in effect ----
    searched = ", ".join(str(p) for p in candidate_paths())
    if cfg_error:
        rep.add("config", FAIL, cfg_error, f"Searched: {searched}")
        return None
    if cfg.source:
        rep.add("config", PASS, str(cfg.source))
    else:
        rep.add("config", WARN, "none found — using built-in defaults",
                f"Write one with `catnip init`. Searched: {searched}")

    # ---- gh ----
    if not shutil.which("gh"):
        rep.add("gh cli", FAIL, "not installed",
                "Install the GitHub CLI: https://cli.github.com")
        return None
    rc, out, _ = _run(["gh", "--version"])
    rep.add("gh cli", PASS if rc == 0 else FAIL, out.splitlines()[0] if out else "unknown version")

    rc, _, err = _run(["gh", "auth", "status"])
    if rc != 0:
        rep.add("gh auth", FAIL, "not authenticated",
                "Run: gh auth login --scopes repo")
        return None
    rep.add("gh auth", PASS, "authenticated")

    # Token scopes. Traffic endpoints need push access to each repo, which
    # in practice means the `repo` scope for a classic token; a token with
    # only `public_repo` fetches metadata fine and silently returns no
    # traffic — the single most confusing catnip failure mode.
    rc, scopes, _ = _run(["gh", "api", "-i", "user", "--jq", "empty"])
    scope_line = next((ln for ln in scopes.splitlines()
                       if ln.lower().startswith("x-oauth-scopes:")), "")
    scope_val = scope_line.split(":", 1)[1].strip() if scope_line else ""
    if not scope_line:
        rep.add("token scopes", WARN, "not reported (fine-grained token?)",
                "Fine-grained tokens need repository permissions: "
                "Administration: read (traffic) and Metadata: read.")
    elif "repo" in [s.strip() for s in scope_val.split(",")]:
        rep.add("token scopes", PASS, scope_val or "(none)")
    else:
        rep.add("token scopes", WARN, scope_val or "(none)",
                "Traffic data needs the `repo` scope. Re-auth with: "
                "gh auth refresh --scopes repo")

    # ---- identity ----
    rc, login, err = _run(["gh", "api", "user", "--jq", ".login"])
    if rc != 0 or not login:
        rep.add("authenticated as", FAIL, err or "unknown", "Run: gh auth login")
        return None
    rep.add("authenticated as", PASS, login)

    configured = cfg.values.get("CATNIP_OWNER", "").strip()
    owner = configured or login
    if configured and configured != login:
        # Legal and common (collecting an org), but it is also exactly what
        # a typo looks like, so it is never silent.
        rep.add("configured owner", WARN, f"{configured} (token belongs to {login})",
                "Traffic data requires push access to each repo. If this is "
                "an org you administer, this is expected.")
    else:
        rep.add("configured owner", PASS, owner + ("" if configured else " (auto)"))

    rc, remaining, _ = _run(["gh", "api", "rate_limit", "--jq", ".resources.core.remaining"])
    if rc == 0 and remaining.isdigit():
        n = int(remaining)
        rep.add("rate limit", PASS if n > 500 else WARN, f"{n}/5000 remaining",
                "A full run costs roughly 10 calls per repo. Wait for the "
                "hourly reset, or narrow CATNIP_INCLUDE.")
    else:
        rep.add("rate limit", WARN, "could not read")

    return owner


def check_access(rep: Report, cfg: Config, owner: str):
    """Can we list this owner's repos, and does traffic actually come back?"""
    rc, out, err = _run(["gh", "api", f"users/{owner}", "--jq", ".type"])
    if rc != 0:
        rep.add("owner exists", FAIL, err.splitlines()[-1] if err else "lookup failed",
                f"Check the spelling of CATNIP_OWNER ({owner}).")
        return
    rep.add("owner exists", PASS, f"{owner} is a {out.lower()}")

    endpoint = (f"orgs/{owner}/repos?per_page=1" if out == "Organization"
                else "user/repos?affiliation=owner&per_page=1")
    rc, out, err = _run(["gh", "api", endpoint, "--jq", ".[0].full_name"])
    if rc != 0 or not out:
        rep.add("repo listing", FAIL, err.splitlines()[-1] if err else "no repositories returned",
                "The token cannot see any repositories for this owner.")
        return
    rep.add("repo listing", PASS, f"reachable (first: {out})")

    # The probe that matters: traffic requires push access, and a 403 here
    # is the difference between "catnip works" and "every chart is empty".
    rc, body, err = _run(["gh", "api", f"repos/{out}/traffic/clones", "--jq", ".count"])
    if rc == 0:
        rep.add("traffic access", PASS, f"{out}: {body} clones in the last 14d")
    else:
        rep.add("traffic access", FAIL, f"{out}: denied",
                "Traffic endpoints require push access to the repository "
                "and the `repo` token scope. `gh auth refresh --scopes repo`")


def check_data(rep: Report, cfg: Config):
    """Writability, run inventory, freshness, and completeness."""
    data_dir = cfg.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".catnip-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rep.add("data dir", PASS, f"{data_dir} (writable)")
    except OSError as exc:
        rep.add("data dir", FAIL, f"{data_dir}: {exc}",
                "Set CATNIP_DATA_DIR to a writable location.")
        return

    runs = run_dirs(cfg.runs_dir)
    if not runs:
        rep.add("runs", WARN, f"none under {cfg.runs_dir}", "Collect one with: catnip run")
        return
    latest = runs[-1]
    age_h = _age_hours(latest.name)
    status = PASS if age_h is None or age_h < 48 else WARN
    rep.add("runs", status,
            f"{len(runs)} run(s), newest {latest.name}"
            + (f" ({age_h:.0f}h old)" if age_h is not None else ""),
            "Newest run is over 48h old — check `catnip timer status`.")

    manifest = latest / "manifest.json"
    if manifest.is_file():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            detail = (f"{m.get('repos_fetched')}/{m.get('repos_selected')} repos, "
                      f"{m.get('duration_seconds')}s, {m.get('traffic_denied', 0)} traffic-denied")
            denied = int(m.get("traffic_denied", 0) or 0)
            rep.add("last run", WARN if denied else PASS, detail,
                    f"{denied} repo(s) returned no traffic data — see "
                    f"{latest}/reports/traffic-denied.tsv")
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            rep.add("last run", WARN, f"unreadable manifest: {exc}")
    else:
        rep.add("last run", WARN, "no manifest.json (run predates it, or fetch was interrupted)")

    missing = [f for f in TUI_CSVS if not (latest / "analysis" / f).is_file()
               or (latest / "analysis" / f).stat().st_size == 0]
    if not (latest / "analysis").is_dir():
        rep.add("analysis", FAIL, "no analysis/ directory", f"Run: catnip analyze {latest}")
    elif missing:
        rep.add("analysis", WARN, f"{len(TUI_CSVS) - len(missing)}/{len(TUI_CSVS)} CSVs "
                f"(missing: {', '.join(missing[:4])}{'…' if len(missing) > 4 else ''})",
                f"Re-derive with: catnip analyze {latest}")
    else:
        rep.add("analysis", PASS, f"all {len(TUI_CSVS)} CSVs present")

    store = cfg.history_file
    if not store.is_file():
        rep.add("history store", WARN, "not created yet",
                "Run `catnip history`. Until it exists, pruned runs lose their "
                "traffic days permanently.")
    else:
        try:
            s = json.loads(store.read_text(encoding="utf-8"))
            ingested = len(s.get("fetches_ingested", []))
            un_ingested = [d.name for d in runs if d.name not in set(s.get("fetches_ingested", []))]
            rep.add("history store", WARN if un_ingested else PASS,
                    f"{len(s.get('repos', {}))} repos, {ingested} run(s) ingested, "
                    f"coverage {s.get('coverage')}",
                    f"{len(un_ingested)} run(s) not yet ingested — run `catnip history` "
                    "before pruning.")
        except (json.JSONDecodeError, OSError) as exc:
            rep.add("history store", FAIL, f"unreadable: {exc}",
                    "Rebuild with: catnip history --rebuild")

    if cfg.stats_file.is_file():
        age = _file_age_hours(cfg.stats_file)
        rep.add("totals", PASS, f"{cfg.stats_file} ({age:.0f}h old)")
    else:
        rep.add("totals", WARN, "not computed", "Run: catnip totals")


def check_timer(rep: Report):
    """systemd user timer, when systemd is what this machine uses."""
    if not shutil.which("systemctl"):
        rep.add("timer", WARN, "systemd not present",
                "Use cron instead — see docs/automation.md.")
        return
    rc, out, _ = _run(["systemctl", "--user", "is-enabled", "catnip.timer"])
    if rc != 0:
        rep.add("timer", WARN, "catnip.timer not enabled",
                "Install it with: catnip timer install")
        return
    rc, nxt, _ = _run(["systemctl", "--user", "list-timers", "catnip.timer",
                       "--no-pager", "--no-legend"])
    rc2, active, _ = _run(["systemctl", "--user", "is-active", "catnip.service"])
    detail = nxt.strip() or "enabled"
    rep.add("timer", PASS, detail[:110])
    if active == "failed":
        rep.add("timer last run", FAIL, "catnip.service failed",
                "Inspect with: catnip timer logs")


def _age_hours(run_id):
    try:
        when = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600


def _file_age_hours(path: Path):
    return (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) / 3600


def main(argv=None):
    p = argparse.ArgumentParser(description="Check catnip's environment, access, and data.")
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="Emit the report as JSON (for agents and scripts).")
    p.add_argument("--offline", action="store_true",
                   help="Skip every check that talks to GitHub.")
    p.add_argument("--data-only", action="store_true",
                   help="Only check on-disk data (no network, no tooling checks).")
    args = p.parse_args(argv)

    rep = Report()
    cfg, cfg_error = None, ""
    try:
        cfg = Config.load(args.config)
    except ConfigError as exc:
        cfg_error = str(exc)

    if args.data_only:
        if cfg is None:
            rep.add("config", FAIL, cfg_error, "Fix the config file, then re-run.")
        else:
            check_data(rep, cfg)
    else:
        owner = check_environment(rep, cfg, cfg_error)
        if cfg is not None:
            if owner and not args.offline:
                check_access(rep, cfg, owner)
            check_data(rep, cfg)
            check_timer(rep)

    if args.as_json:
        json.dump({
            "checks": rep.checks,
            "failed": len(rep.failed),
            "warnings": len(rep.warned),
            "config": cfg.as_dict() if cfg else None,
            "paths": cfg.paths_dict() if cfg else None,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "environment_overrides": sorted(k for k in os.environ if k.startswith("CATNIP_")),
        }, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        rep.render()
    return 1 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
