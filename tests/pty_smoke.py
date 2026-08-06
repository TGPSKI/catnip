#!/usr/bin/env python3
"""Reusable pty smoke harness for curses TUIs.

Drives a TUI command inside a pseudo-terminal of a given size, feeds it a
key script, and asserts a clean exit with no traceback. Usable as a module
(`run_pty_smoke`) or a CLI:

    PYTHONPATH=src python3 tests/pty_smoke.py --size 100x30 --keys vjkq -- \
        python3 -m catnip.ui <run-dir>

Copied deliberately from https://github.com/TGPSKI/pane (src/pane/pty_smoke.py),
which excludes it from the vendor set because it is test infrastructure with
exactly one home per consumer. It lives under tests/ rather than in
catnip/tui/ for the same reason: it is POSIX-only (pty/termios/fcntl) and
must never be imported by the package at run time.

The only honest way to test a curses program is to give it a terminal.
"""
from __future__ import annotations

import argparse
import os
import pty
import select
import struct
import sys
import termios
import time


def run_pty_smoke(cmd: list[str], rows: int, cols: int, keys: str,
                  settle: float = 0.35, timeout: float = 20.0) -> tuple[int, str]:
    """Run cmd in a rows x cols pty, send keys one at a time, return
    (exit_status, captured_output)."""
    import fcntl

    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.execvp(cmd[0], cmd)

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    output = []

    def drain(duration):
        end = time.time() + duration
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    output.append(os.read(fd, 65536))
                except OSError:
                    return False
        return True

    drain(settle * 2)  # initial render
    for key in keys:
        try:
            os.write(fd, key.encode())
        except OSError:
            break
        if not drain(settle):
            break

    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        done, st = os.waitpid(pid, os.WNOHANG)
        if done:
            status = st
            break
        drain(0.1)
    if status is None:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        return 124, b"".join(output).decode("utf-8", "replace")

    try:
        os.close(fd)
    except OSError:
        pass
    code = os.waitstatus_to_exitcode(status)
    return code, b"".join(output).decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", default="100x30", help="COLSxROWS (default 100x30)")
    parser.add_argument("--keys", default="q", help="Key script to feed (default 'q')")
    parser.add_argument("cmd", nargs=argparse.REMAINDER,
                        help="Command to run (prefix with --)")
    args = parser.parse_args()
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("no command given")
    cols, rows = (int(v) for v in args.size.split("x"))

    code, out = run_pty_smoke(cmd, rows, cols, args.keys)
    ok = code == 0 and "Traceback" not in out
    print(f"pty-smoke {'ok' if ok else 'FAIL'} size={args.size} keys={args.keys} exit={code}")
    if not ok:
        sys.stdout.write(out[-2000:])
        sys.exit(1)


if __name__ == "__main__":
    main()
