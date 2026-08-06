#!/usr/bin/env python3
"""Write a catnip config file — interactively, or entirely from flags.

Interactive by default when stdin is a terminal; fully scriptable
otherwise, which is what lets the onboarding skill (and CI) drive it.
The file it writes is annotated: a config you can read six months later
is worth more than one that is merely short.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from catnip.config import DEFAULTS, Config, ConfigError, default_data_dir, gh_login

TEMPLATE = """\
# catnip configuration — KEY=value, '#' comments.
#
# Resolution order (first wins): environment > this file > ./catnip.conf >
# ~/.config/catnip/catnip.conf > built-in defaults.
# See `catnip config` for what is actually in effect right now.

# Whose repositories to collect. Leave empty to use the authenticated
# `gh` account. Traffic data requires push access, so this is normally
# you or an organization you administer.
CATNIP_OWNER={owner}

# auto | user | org — auto tries the org endpoint, then the user one.
CATNIP_OWNER_TYPE={owner_type}

# Everything catnip writes lives here: runs/, stats/, logs/.
CATNIP_DATA_DIR={data_dir}

# Repo selection. Space-separated glob patterns against the bare repo
# name. Empty CATNIP_INCLUDE means "everything not excluded".
CATNIP_INCLUDE={include}
CATNIP_EXCLUDE={exclude}
CATNIP_INCLUDE_FORKS={forks}
CATNIP_INCLUDE_ARCHIVED={archived}
CATNIP_INCLUDE_PRIVATE={private}

# Optional endpoints, each costing API calls per repo per run. Turn
# these off first if a large account runs into the hourly rate limit.
CATNIP_FETCH_STATS={stats}
CATNIP_FETCH_README={readme}
CATNIP_FETCH_EVENTS={events}
CATNIP_FETCH_ISSUES={issues}

# How long run directories survive `catnip prune`. The history store is
# never pruned — it is the only copy of traffic days older than the ~14
# GitHub still serves.
CATNIP_RETAIN_DAYS={retain}

# Timer cadence (systemd OnCalendar syntax) and jitter. Daily is the
# floor that keeps the 14-day traffic window from developing holes.
CATNIP_TIMER_ONCALENDAR={oncalendar}
CATNIP_TIMER_RANDOM_DELAY={random_delay}
"""


def ask(prompt, default, interactive):
    if not interactive:
        return default
    shown = f" [{default}]" if default else " []"
    try:
        answer = input(f"{prompt}{shown}: ").strip()
    except EOFError:
        return default
    return answer or default


def main(argv=None):
    p = argparse.ArgumentParser(description="Create a catnip config file.")
    p.add_argument("--path", type=Path,
                   help="Where to write (default: ~/.config/catnip/catnip.conf).")
    p.add_argument("--local", action="store_true",
                   help="Write ./catnip.conf in the current directory instead.")
    p.add_argument("--owner", help="GitHub user or org to collect.")
    p.add_argument("--owner-type", choices=["auto", "user", "org"])
    p.add_argument("--data-dir", help="Where catnip stores runs and stats.")
    p.add_argument("--include", help="Space-separated include globs.")
    p.add_argument("--exclude", help="Space-separated exclude globs.")
    p.add_argument("--include-forks", choices=["true", "false"])
    p.add_argument("--retain-days", type=int)
    p.add_argument("--oncalendar", help="systemd OnCalendar cadence (default: daily).")
    p.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    p.add_argument("--no-input", action="store_true", help="Never prompt; use flags and defaults.")
    args = p.parse_args(argv)

    if args.path and args.local:
        print("catnip: pass either --path or --local, not both.", file=sys.stderr)
        return 2
    if args.local:
        path = Path.cwd() / "catnip.conf"
    elif args.path:
        path = args.path.expanduser()
    else:
        path = Path.home() / ".config" / "catnip" / "catnip.conf"

    if path.exists() and not args.force:
        print(f"catnip: {path} already exists. Pass --force to overwrite, or edit it.",
              file=sys.stderr)
        print("        `catnip config` shows what is currently in effect.", file=sys.stderr)
        return 1

    interactive = sys.stdin.isatty() and not args.no_input

    owner = args.owner
    if not owner:
        try:
            detected = gh_login()
        except ConfigError as exc:
            detected = ""
            if not interactive:
                print(f"catnip: {exc}", file=sys.stderr)
                return 2
            print(f"catnip: {exc}\n", file=sys.stderr)
        owner = ask("GitHub user or org to collect", detected, interactive)
    if not owner:
        print("catnip: an owner is required (--owner, or authenticate gh first).",
              file=sys.stderr)
        return 2

    data_dir = args.data_dir or ask("Data directory", str(default_data_dir()), interactive)
    exclude = args.exclude if args.exclude is not None else ask(
        "Exclude repo globs (space-separated, blank for none)", "", interactive)
    include = args.include if args.include is not None else ask(
        "Only include these globs (blank = everything)", "", interactive)
    forks = args.include_forks or ask("Include forks? (true/false)", "false", interactive)
    retain = str(args.retain_days) if args.retain_days else ask(
        "Keep run directories for how many days?", DEFAULTS["CATNIP_RETAIN_DAYS"], interactive)
    oncalendar = args.oncalendar or ask(
        "Timer cadence (systemd OnCalendar)", DEFAULTS["CATNIP_TIMER_ONCALENDAR"], interactive)

    text = TEMPLATE.format(
        owner=owner,
        owner_type=args.owner_type or DEFAULTS["CATNIP_OWNER_TYPE"],
        data_dir=data_dir,
        include=include,
        exclude=exclude,
        forks=forks,
        archived=DEFAULTS["CATNIP_INCLUDE_ARCHIVED"],
        private=DEFAULTS["CATNIP_INCLUDE_PRIVATE"],
        stats=DEFAULTS["CATNIP_FETCH_STATS"],
        readme=DEFAULTS["CATNIP_FETCH_README"],
        events=DEFAULTS["CATNIP_FETCH_EVENTS"],
        issues=DEFAULTS["CATNIP_FETCH_ISSUES"],
        retain=retain,
        oncalendar=oncalendar,
        random_delay=DEFAULTS["CATNIP_TIMER_RANDOM_DELAY"],
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")

    # Parse it straight back. A config file that catnip cannot read is
    # worse than none at all, and this is the cheapest possible moment to
    # find that out.
    try:
        cfg = Config.load(path)
    except ConfigError as exc:
        print(f"catnip: the file just written does not parse: {exc}", file=sys.stderr)
        return 1
    print(f"  owner    : {cfg.values['CATNIP_OWNER']}")
    print(f"  data dir : {cfg.data_dir}")
    print("\nNext: `catnip doctor` to check access, then `catnip run` to collect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
