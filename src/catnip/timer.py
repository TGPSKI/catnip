#!/usr/bin/env python3
"""Install and inspect the systemd **user** timer that keeps catnip current.

A user timer, not a system one, because `gh` reads credentials from the
user's keyring/config: a root-owned system unit finds no authentication
and fails every night. The cost of that choice is that user units stop
when the last session ends unless lingering is enabled, so `install`
checks for it and says so rather than leaving a timer that only runs
while someone is logged in.

Machines without systemd get an equivalent cron line from
`catnip timer cron`; see docs/automation.md.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from catnip.config import Config, ConfigError

UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "systemd"
SERVICE, TIMER = "catnip.service", "catnip.timer"


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def catnip_bin() -> Path:
    """Absolute path to the `catnip` entry point backing this checkout."""
    candidate = Path(__file__).resolve().parents[2] / "bin" / "catnip"
    if candidate.is_file():
        return candidate
    found = shutil.which("catnip")
    if found:
        return Path(found)
    raise ConfigError("cannot locate the catnip executable to point the unit at")


def render(cfg: Config, config_file: str | None = None) -> dict:
    """Render both unit files from the templates in systemd/."""
    src = config_file or (str(cfg.source) if cfg.source else "")
    # Pin the config path into the unit. A timer that resolves config
    # relative to a working directory is a coordinate mismatch waiting to
    # happen: it would silently pick up a different catnip.conf than the
    # one you edit interactively.
    subs = {
        "@CATNIP_BIN@": str(catnip_bin()),
        "@CATNIP_CONFIG_ENV@": f"Environment=CATNIP_CONFIG={src}" if src else "",
        "@CATNIP_ONCALENDAR@": cfg.get("CATNIP_TIMER_ONCALENDAR", "daily"),
        "@CATNIP_RANDOM_DELAY@": cfg.get("CATNIP_TIMER_RANDOM_DELAY", "1h"),
    }
    out = {}
    for name in (SERVICE, TIMER):
        template = TEMPLATE_DIR / f"{name}.in"
        if not template.is_file():
            raise ConfigError(f"unit template missing: {template}")
        lines = []
        for raw in template.read_text(encoding="utf-8").splitlines():
            line = raw
            for key, value in subs.items():
                line = line.replace(key, value)
            # A placeholder that resolved to nothing leaves a blank line
            # where a directive was; systemd tolerates it, readers don't.
            if not line.strip() and any(key in raw for key in subs):
                continue
            lines.append(line)
        out[name] = "\n".join(lines).rstrip() + "\n"
    return out


def install(cfg: Config, config_file=None, enable=True):
    if not shutil.which("systemctl"):
        print("systemd is not available on this machine.", file=sys.stderr)
        print("Use the cron equivalent instead:  catnip timer cron", file=sys.stderr)
        return 1
    units = render(cfg, config_file)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in units.items():
        (UNIT_DIR / name).write_text(text, encoding="utf-8")
        print(f"  wrote {UNIT_DIR / name}")

    _run(["systemctl", "--user", "daemon-reload"])
    if enable:
        r = _run(["systemctl", "--user", "enable", "--now", TIMER])
        if r.returncode != 0:
            print(r.stderr.strip(), file=sys.stderr)
            return 1
        print(f"  enabled {TIMER}")

    # Without lingering, user units are torn down at logout — the timer
    # then only runs while you happen to be logged in, which looks like
    # "catnip randomly stops collecting".
    linger = _run(["loginctl", "show-user", os.environ.get("USER", ""), "--property=Linger"])
    if "Linger=yes" not in linger.stdout:
        print("\n  NOTE: user lingering is off, so this timer only runs while you are")
        print("        logged in. To let it run on a headless/rebooted machine:")
        print(f"          sudo loginctl enable-linger {os.environ.get('USER', '$USER')}")

    print("\nNext run: see `catnip timer status`. Logs: `catnip timer logs`.")
    return 0


def uninstall():
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "disable", "--now", TIMER])
    removed = 0
    for name in (SERVICE, TIMER):
        path = UNIT_DIR / name
        if path.exists():
            path.unlink()
            removed += 1
            print(f"  removed {path}")
    if shutil.which("systemctl"):
        _run(["systemctl", "--user", "daemon-reload"])
    print(f"Removed {removed} unit file(s). Collected data was not touched.")
    return 0


def status():
    if not shutil.which("systemctl"):
        print("systemd is not available on this machine.")
        return 1
    if not (UNIT_DIR / TIMER).is_file():
        print(f"{TIMER} is not installed. Install it with: catnip timer install")
        return 1
    for cmd in (["systemctl", "--user", "list-timers", TIMER, "--no-pager"],
                ["systemctl", "--user", "status", SERVICE, "--no-pager", "-n", "0"]):
        r = _run(cmd)
        print(r.stdout.rstrip() or r.stderr.rstrip())
        print()
    return 0


def logs(lines=50, follow=False):
    if not shutil.which("journalctl"):
        print("journalctl is not available.", file=sys.stderr)
        return 1
    cmd = ["journalctl", "--user", "-u", SERVICE, "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("-f")
    return subprocess.run(cmd, check=False).returncode


#: Seconds of jitter in front of a cron run. systemd has RandomizedDelaySec;
#: cron has nothing, so the line sleeps a random part of it first. 2400 is
#: the same 40-minute spread `CATNIP_TIMER_RANDOM_DELAY` defaults to.
CRON_JITTER_SECONDS = 2400

#: awk, not $RANDOM: crontab runs the command under /bin/sh, where $RANDOM
#: is a bashism that expands to the empty string and turns the sleep into a
#: syntax error every six hours.
CRON_JITTER = (f"sleep $(awk 'BEGIN{{srand();print int(rand()*"
               f"{CRON_JITTER_SECONDS})}}')")


def cron_line(cfg: Config, config_file=None) -> str:
    src = config_file or (str(cfg.source) if cfg.source else "")
    env = f"CATNIP_CONFIG={src} " if src else ""
    return (f"# catnip — collection every 6 hours (cron has no Persistent=\n"
            f"# equivalent; a machine asleep at one of these times misses that\n"
            f"# read, and the next one four to six hours later covers for it).\n"
            f"# The sleep is the jitter systemd gets from RandomizedDelaySec.\n"
            f"0 */6 * * *  {CRON_JITTER} && {env}{catnip_bin()} run --quiet >> "
            f"{cfg.log_dir / 'cron.log'} 2>&1")


def main(argv=None):
    p = argparse.ArgumentParser(description="Manage catnip's scheduled collection.")
    p.add_argument("action", choices=["install", "uninstall", "status", "logs", "print", "cron"])
    p.add_argument("--config", help="Explicit config file path.")
    p.add_argument("--no-enable", action="store_true", help="Write units without enabling them.")
    p.add_argument("-n", "--lines", type=int, default=50, help="Log lines to show.")
    p.add_argument("-f", "--follow", action="store_true", help="Follow the log.")
    args = p.parse_args(argv)

    try:
        cfg = Config.load(args.config)
        if args.action == "install":
            return install(cfg, args.config, enable=not args.no_enable)
        if args.action == "uninstall":
            return uninstall()
        if args.action == "status":
            return status()
        if args.action == "logs":
            return logs(args.lines, args.follow)
        if args.action == "cron":
            print(cron_line(cfg, args.config))
            return 0
        for name, text in render(cfg, args.config).items():
            print(f"# ---- {UNIT_DIR / name} ----")
            print(text)
        return 0
    except ConfigError as exc:
        print(f"catnip: config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
