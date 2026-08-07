#!/usr/bin/env python3
"""Dispatch every SEED block in a meta-analysis as one investigation brief.

N varies by cycle - that is the meta-analyst's whole judgment. A stage that
dispatches by making one call per seed makes a data-dependent number of
calls, and a model that makes three calls for four seeds drops an angle
silently. Parsing the blocks is deterministic, so it belongs here: the agent
makes one call, this decides how many briefs there were.

Each brief lands on prowl-analyze-in via intake, which stores it as a hide
first. The analyst curing runs one analysis per brief; its packages fan back
in through the writer, whose per-cycle files accumulate and whose publish
dedupes - collation is free.

    usage: prowl-dispatch.py <cycle> <window> <seeds>
"""
import re
import sys
import urllib.request

INTAKE = ("http://127.0.0.1:7751/intake"
          "?kind=prowl.brief&source=catnip-prowl-meta&queue=prowl-analyze-in")
FIELD_RE = re.compile(r"^(angle|brief):[ \t]*", re.M)


def fail(msg):
    sys.exit("prowl-dispatch.py: " + msg)


def seeds(text):
    starts = list(re.finditer(r"^SEED[ \t]*$", text, re.M))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        body = text[m.end():end]
        fields = {}
        parts = list(FIELD_RE.finditer(body))
        for j, f in enumerate(parts):
            stop = parts[j + 1].start() if j + 1 < len(parts) else len(body)
            fields[f.group(1)] = body[f.end():stop].strip()
        yield fields


def main():
    if len(sys.argv) != 4:
        fail("usage: prowl-dispatch.py <cycle> <window> <seeds>")
    cycle, window, text = sys.argv[1], sys.argv[2], sys.argv[3]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cycle):
        fail(f"cycle must be YYYY-MM-DD, got {cycle!r}")

    parsed = []
    for s in seeds(text):
        missing = [k for k in ("angle", "brief") if not s.get(k)]
        if missing:
            fail("SEED {!r} is missing {}".format(
                s.get("angle", "?")[:60], ", ".join(missing)))
        parsed.append(s)
    if not parsed:
        fail("no SEED blocks - the meta-analysis dispatched nothing")

    for s in parsed:
        body = "CYCLE: {}\nWINDOW: {}\nANGLE: {}\n\n{}\n".format(
            cycle, window, s["angle"], s["brief"])
        req = urllib.request.Request(
            INTAKE, data=body.encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            fail("intake refused seed {!r}: {}".format(s["angle"][:60], e))
        print("  seeded       {}".format(s["angle"][:64]))

    print(f"dispatched {len(parsed)} brief(s)")


if __name__ == "__main__":
    main()
