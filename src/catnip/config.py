#!/usr/bin/env python3
"""Configuration resolution and repo selection for catnip.

Every knob has one name (``CATNIP_*``) and one resolution order, used
identically by the bash fetcher and the Python pipeline:

    1. process environment
    2. the file named by ``--config`` or ``$CATNIP_CONFIG``
    3. ``./catnip.conf`` in the working directory
    4. ``$XDG_CONFIG_HOME/catnip/catnip.conf`` (default ``~/.config``)
    5. built-in defaults

The file format is ``KEY=value``, one per line, ``#`` for comments — the
intersection of what bash can ``source`` and what Python can parse
without a dependency. A richer format (TOML) would need either a parser
on the bash side or a second source of truth; both are worse than the
mild syntax limits of KEY=value.

Used as a module by the pipeline, and as a CLI by ``fetch.sh``::

    python3 -m catnip.config --shell          # eval-able KEY=value lines
    python3 -m catnip.config --json           # resolved config + paths
    python3 -m catnip.config --select FILE    # repo list JSON -> name<TAB>slug
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# Key -> default. Every key catnip understands is listed here; an unknown
# key in a config file is an error, not a silently ignored typo (a
# misspelled CATNIP_EXCLUDE that quietly fetches 400 repos is exactly the
# kind of failure the triage skill would otherwise have to chase).
DEFAULTS = {
    # Whose repositories to collect. Empty means "the authenticated user",
    # resolved through `gh api user` at run time.
    "CATNIP_OWNER": "",
    # auto | user | org — auto tries /orgs first, then /users.
    "CATNIP_OWNER_TYPE": "auto",
    # Root for everything catnip writes. Runs, stats, and history live here.
    "CATNIP_DATA_DIR": "",
    # Repo selection. Space- or comma-separated glob patterns matched
    # against the bare repo name (not owner/name).
    "CATNIP_INCLUDE": "",
    "CATNIP_EXCLUDE": "",
    "CATNIP_INCLUDE_FORKS": "false",
    "CATNIP_INCLUDE_ARCHIVED": "true",
    "CATNIP_INCLUDE_PRIVATE": "true",
    # Optional per-repo endpoints. Each costs one or more API calls per
    # repo per run; turning them off is the first lever when a large
    # account bumps the hourly rate limit.
    "CATNIP_FETCH_STATS": "true",
    "CATNIP_FETCH_README": "true",
    "CATNIP_FETCH_EVENTS": "true",
    "CATNIP_FETCH_ISSUES": "true",
    # Run retention. `catnip prune` deletes run directories older than
    # this; the history store is never pruned.
    "CATNIP_RETAIN_DAYS": "30",
    # How long after a day closes GitHub is still adding counts to it.
    # `derive.SETTLE_HOURS_FLOOR` is the measured floor and a lower value
    # here is ignored; `catnip settle` measures what this account actually
    # does and says when this needs raising.
    "CATNIP_SETTLE_HOURS": "36",
    # How much of the settle measurement to keep. Unlike the store, this
    # file is a measurement and can be deleted: every day in it is already
    # in the store at its settled value, so trimming loses the revision
    # history and nothing else.
    "CATNIP_SETTLE_LOG_DAYS": "90",
    # systemd timer cadence (OnCalendar= syntax) and jitter. Every six
    # hours, because GitHub keeps revising a day for `CATNIP_SETTLE_HOURS`
    # after it closes and a daily read places a revision no more precisely
    # than "somewhere in the last 24 hours". Four reads a day bound it to
    # six. The jitter is additive in systemd, so 40m spreads the start
    # uniformly over 40 minutes — the +/-20m variability, measured from
    # the middle of that spread.
    "CATNIP_TIMER_ONCALENDAR": "*-*-* 00/6:00:00",
    "CATNIP_TIMER_RANDOM_DELAY": "40m",
}

BOOL_KEYS = {k for k, v in DEFAULTS.items() if v in ("true", "false")}
TRUE = {"1", "true", "yes", "on"}
FALSE = {"0", "false", "no", "off"}

# KEY=value, optionally quoted, optionally `export `-prefixed so a config
# file stays valid input to bash `source`.
LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


class ConfigError(Exception):
    """A config file is unreadable, malformed, or names an unknown key."""


def _unquote(value: str) -> str:
    """Strip surrounding quotes, or a trailing ` # comment` when unquoted."""
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end != -1 else value[1:]
    return value.split(" #", 1)[0].rstrip()


def parse_file(path: Path) -> dict:
    """Parse a KEY=value config file. Raises ConfigError on unknown keys."""
    out = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            raise ConfigError(f"{path}:{lineno}: not KEY=value: {raw!r}")
        key, value = m.group(1), _unquote(m.group(2))
        if key not in DEFAULTS:
            known = ", ".join(sorted(DEFAULTS))
            raise ConfigError(f"{path}:{lineno}: unknown key {key!r}. Known keys: {known}")
        out[key] = value
    return out


def candidate_paths(explicit: str | Path | None = None) -> list[Path]:
    """Config file locations in resolution order (first existing one wins)."""
    paths = []
    named = explicit or os.environ.get("CATNIP_CONFIG")
    if named:
        paths.append(Path(named).expanduser())
    paths.append(Path.cwd() / "catnip.conf")
    xdg = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    paths.append(Path(xdg).expanduser() / "catnip" / "catnip.conf")
    return paths


def default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(xdg).expanduser() / "catnip"


class Config:
    """Resolved configuration plus the paths derived from it."""

    def __init__(self, values: dict, source: Path | None = None):
        self.values = values
        self.source = source

    # ---- resolution -----------------------------------------------------

    @classmethod
    def load(cls, explicit: str | Path | None = None, env: dict | None = None) -> Config:
        env = os.environ if env is None else env
        values = dict(DEFAULTS)
        source = None
        # A config file named explicitly (flag or $CATNIP_CONFIG) must
        # exist — falling through to ./catnip.conf when the named file is
        # missing runs the wrong configuration and reports success.
        named = explicit or env.get("CATNIP_CONFIG")
        if named and not Path(named).expanduser().is_file():
            raise ConfigError(f"config file not found: {named}")
        for path in candidate_paths(explicit):
            if path.is_file():
                values.update(parse_file(path))
                source = path
                break
        # Environment wins over the file — this is what lets a systemd unit
        # or a one-off `CATNIP_OWNER=torvalds catnip fetch` override without
        # editing anything.
        for key in DEFAULTS:
            if env.get(key):
                values[key] = env[key]
        return cls(values, source)

    # ---- typed access ---------------------------------------------------

    def __getitem__(self, key: str) -> str:
        return self.values[key]

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def flag(self, key: str) -> bool:
        raw = str(self.values.get(key, "")).strip().lower()
        if raw in TRUE:
            return True
        if raw in FALSE:
            return False
        raise ConfigError(f"{key}: expected a boolean (true/false), got {raw!r}")

    def integer(self, key: str) -> int:
        try:
            return int(str(self.values.get(key, "")).strip())
        except ValueError as exc:
            raise ConfigError(f"{key}: expected an integer, got {self.values.get(key)!r}") from exc

    def patterns(self, key: str) -> list[str]:
        raw = str(self.values.get(key, "")).replace(",", " ")
        return [p for p in raw.split() if p]

    # ---- derived --------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        raw = self.values.get("CATNIP_DATA_DIR", "").strip()
        return Path(raw).expanduser().resolve() if raw else default_data_dir()

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def stats_dir(self) -> Path:
        return self.data_dir / "stats"

    @property
    def stats_file(self) -> Path:
        return self.stats_dir / "totals.json"

    @property
    def history_dir(self) -> Path:
        return self.stats_dir / "history"

    @property
    def history_file(self) -> Path:
        return self.history_dir / "traffic_daily.json"

    @property
    def daily_snapshots_file(self) -> Path:
        """Per-fetch readings of each recent day, for `catnip settle`.

        A sibling of `snapshots.jsonl` rather than a column in it: that
        file is parsed by everything that reads per-run stats today, and
        this one can be deleted without touching data that cannot be
        refetched."""
        return self.history_dir / "daily_snapshots.jsonl"

    @property
    def reports_dir(self) -> Path:
        """Where `catnip report` writes. Under the data directory, not the
        checkout: reports describe private repositories' traffic, and the
        rule that config.py owns every path exists so the timer and your
        shell cannot disagree about which one is real."""
        return self.data_dir / "reports"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def latest_run(self) -> Path | None:
        """Newest run directory, or None."""
        runs = run_dirs(self.runs_dir)
        return runs[-1] if runs else None

    def owner(self) -> str:
        """Configured owner, or the authenticated `gh` user."""
        configured = self.values.get("CATNIP_OWNER", "").strip()
        if configured:
            return configured
        return gh_login()

    def as_dict(self) -> dict:
        return dict(self.values)

    def paths_dict(self) -> dict:
        return {
            "data_dir": str(self.data_dir),
            "runs_dir": str(self.runs_dir),
            "stats_file": str(self.stats_file),
            "history_dir": str(self.history_dir),
            "history_file": str(self.history_file),
            "daily_snapshots_file": str(self.daily_snapshots_file),
            "reports_dir": str(self.reports_dir),
            "log_dir": str(self.log_dir),
            "config_file": str(self.source) if self.source else "",
        }


RUN_STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")


def run_dirs(runs_dir) -> list:
    """Run directories under runs_dir, oldest first.

    Run IDs are UTC stamps (``20260805T221500Z``), so lexical order is
    chronological — no stat() calls, and no dependence on mtimes that a
    backup or an rsync would rewrite.
    """
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    return [d for d in sorted(runs_dir.iterdir())
            if d.is_dir() and RUN_STAMP_RE.match(d.name)]


def gh_login() -> str:
    """The authenticated GitHub login, via `gh`. Raises ConfigError with a
    fixable message rather than returning something wrong — every
    downstream path is keyed on the owner, so guessing here produces a
    fetch that succeeds and collects nothing."""
    try:
        out = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except FileNotFoundError as exc:
        raise ConfigError(
            "gh CLI not found. Install it (https://cli.github.com) or set "
            "CATNIP_OWNER explicitly."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConfigError("`gh api user` timed out after 30s") from exc
    login = out.stdout.strip()
    if out.returncode != 0 or not login:
        detail = out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "no output"
        raise ConfigError(
            f"could not determine the GitHub login from `gh api user` ({detail}). "
            "Run `gh auth login`, or set CATNIP_OWNER in your config."
        )
    return login


# ---- repo selection -----------------------------------------------------

def slug_for(name: str) -> str:
    """Filesystem-safe slug for a repo name.

    Doubling '-' keeps the mapping injective: without it `a-b` and `a_b`
    would both land on `a_b` and silently overwrite each other's raw JSON.
    Must stay identical to the slug used by analyze.py.
    """
    s = name.replace("/", "_").replace("-", "--")
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", s)


def select_repos(repos: list, cfg: Config) -> tuple[list, list]:
    """Split an API repo list into (selected, rejected-with-reason).

    Rejections are returned rather than dropped so `catnip fetch` can say
    *why* a repo you expected is missing — "0 repos fetched" with no
    explanation is the single most common onboarding dead end.
    """
    include = cfg.patterns("CATNIP_INCLUDE")
    exclude = cfg.patterns("CATNIP_EXCLUDE")
    keep_forks = cfg.flag("CATNIP_INCLUDE_FORKS")
    keep_archived = cfg.flag("CATNIP_INCLUDE_ARCHIVED")
    keep_private = cfg.flag("CATNIP_INCLUDE_PRIVATE")

    selected, rejected = [], []
    for repo in repos:
        name = repo.get("name")
        if not name:
            continue
        if repo.get("fork") and not keep_forks:
            rejected.append((name, "fork (CATNIP_INCLUDE_FORKS=false)"))
        elif repo.get("archived") and not keep_archived:
            rejected.append((name, "archived (CATNIP_INCLUDE_ARCHIVED=false)"))
        elif repo.get("private") and not keep_private:
            rejected.append((name, "private (CATNIP_INCLUDE_PRIVATE=false)"))
        elif include and not any(fnmatch.fnmatch(name, p) for p in include):
            rejected.append((name, "no CATNIP_INCLUDE pattern matched"))
        elif exclude and any(fnmatch.fnmatch(name, p) for p in exclude):
            rejected.append((name, "matched CATNIP_EXCLUDE"))
        else:
            selected.append(repo)
    return selected, rejected


# ---- CLI ----------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Resolve catnip configuration.")
    p.add_argument("--config", help="Explicit config file path.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--shell", action="store_true",
                   help="Emit eval-able KEY=value lines (used by fetch.sh).")
    g.add_argument("--json", action="store_true", help="Emit resolved config + paths as JSON.")
    g.add_argument("--show", action="store_true", help="Human-readable summary.")
    g.add_argument("--select", metavar="FILE",
                   help="Filter a repo-list JSON; writes name<TAB>slug lines to stdout.")
    p.add_argument("--rejects", metavar="FILE",
                   help="With --select: write rejected repos and reasons here.")
    p.add_argument("--selected-json", metavar="FILE",
                   help="With --select: write the surviving repo objects as JSON.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
        if args.select:
            repos = json.loads(Path(args.select).read_text(encoding="utf-8"))
            if not isinstance(repos, list):
                raise ConfigError(f"{args.select}: expected a JSON array of repos")
            selected, rejected = select_repos(repos, cfg)
            for repo in selected:
                sys.stdout.write(f"{repo['name']}\t{slug_for(repo['name'])}\n")
            if args.rejects:
                Path(args.rejects).write_text(
                    "".join(f"{n}\t{why}\n" for n, why in rejected), encoding="utf-8")
            if args.selected_json:
                # The run's canonical repo list. Everything downstream
                # iterates this file, so writing the *filtered* list here is
                # what keeps analysis scoped to what was actually fetched —
                # otherwise excluded repos reappear as rows with no data.
                Path(args.selected_json).write_text(
                    json.dumps(selected, indent=2), encoding="utf-8")
            return 0

        if args.shell:
            values = dict(cfg.as_dict())
            values["CATNIP_OWNER"] = cfg.owner()
            # `export`, not bare assignment: fetch.sh's helpers run Python
            # subprocesses that must resolve to the same configuration, and
            # the run manifest is written by one of them.
            for key, value in sorted(values.items()):
                sys.stdout.write(f"export {key}={shlex.quote(str(value))}\n")
            for key, value in sorted(cfg.paths_dict().items()):
                sys.stdout.write(f"export CATNIP_PATH_{key.upper()}={shlex.quote(value)}\n")
            return 0

        if args.json:
            json.dump({"config": cfg.as_dict(), "paths": cfg.paths_dict()},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
            return 0

        source = cfg.source or "(defaults only — no config file found)"
        print(f"config file : {source}")
        print(f"owner       : {cfg.values['CATNIP_OWNER'] or '(auto: authenticated gh user)'}")
        for key in sorted(DEFAULTS):
            if key != "CATNIP_OWNER":
                print(f"{key:<28}= {cfg.values[key]}")
        print()
        for key, value in cfg.paths_dict().items():
            print(f"{key:<28}= {value}")
        return 0
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
