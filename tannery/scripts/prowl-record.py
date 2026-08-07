#!/usr/bin/env python3
"""File every block in one prowl analysis, then publish the cycle.

The number of blocks varies by cycle - that is the whole point of the
analysis. A stage that records them with one tool call per block makes a
data-dependent number of calls, and a model that makes three calls for four
findings drops one silently: no error, just a shorter report. Parsing blocks
is deterministic, so it belongs here. The agent makes one call; this decides
how many blocks there were.

Validation fails the whole call - loudly, so the curing retries and then
dead-letters where someone will see it, instead of filing a repaired claim.
The tier defines its own obligations: an inferred claim states the test that
held, a speculative one states what would confirm or kill it.

    usage: prowl-record.py <cycle> <analysis>
"""
import re
import subprocess
import sys
from pathlib import Path

TIERS = ("measured", "inferred", "speculative")
EVIDENCE_RE = re.compile(
    r"^catnip-(report-read|view|derivation|store-series|store-sweep|"
    r"prowl-previous|store-status)\b")
FIELD_RE = re.compile(
    r"^(tier|claim|evidence|body|test|read|would_confirm|refutes"
    r"|hypothesis|checked_with|why_not|item):[ \t]*", re.M)
# A FINDING carries its substance in body OR test - the inferred half of a
# measured/inferred pair legitimately has no body, its test and read are the
# content. Validation below enforces body-or-test.
REQUIRED = {
    "FINDING": ("tier", "claim", "evidence"),
    "REFUTED": ("hypothesis", "checked_with", "why_not"),
    "CORRECTION": ("claim", "evidence", "body"),
    "WATCH": ("item", "body"),
}


def blocks(analysis):
    """Yield (kind, {field: value}) per block, in document order."""
    starts = list(re.finditer(
        r"^(FINDING|REFUTED|CORRECTION|WATCH)[ \t]*$", analysis, re.M))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(analysis)
        body = analysis[m.end():end]
        fields = {}
        parts = list(FIELD_RE.finditer(body))
        for j, f in enumerate(parts):
            stop = parts[j + 1].start() if j + 1 < len(parts) else len(body)
            raw = body[f.end():stop].strip()
            # Models indent continuation lines; markdown then reads tables
            # and prose as code blocks. Flatten the indentation.
            fields[f.group(1)] = "\n".join(
                line.lstrip() for line in raw.splitlines())
        yield m.group(1), fields


def fail(msg):
    sys.exit("prowl-record.py: " + msg)


def oneline(text):
    return " ".join(text.split())


def main():
    if len(sys.argv) != 3:
        fail("usage: prowl-record.py <cycle> <analysis>")
    cycle, analysis = sys.argv[1], sys.argv[2]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cycle):
        fail("cycle must be YYYY-MM-DD, got %r" % cycle)

    window = ""
    m = re.search(r"^WINDOW:[ \t]*(.+)$", analysis, re.M)
    if m:
        window = m.group(1).strip()

    parsed = {"FINDING": [], "REFUTED": [], "CORRECTION": [], "WATCH": []}
    for kind, f in blocks(analysis):
        label = f.get("claim") or f.get("hypothesis") or f.get("item") or "?"
        missing = [k for k in REQUIRED[kind] if not f.get(k)]
        if missing:
            fail("%s %r is missing %s" % (kind, label[:60], ", ".join(missing)))
        if kind == "FINDING":
            if not (f.get("body") or f.get("test")):
                fail("FINDING %r has neither body nor test - nothing carries "
                     "its substance" % label[:60])
            if f["tier"] not in TIERS:
                fail("FINDING %r has tier %r, not one of %s" % (
                    label[:60], f["tier"], "|".join(TIERS)))
            if f["tier"] == "inferred" and not f.get("test"):
                fail("inferred FINDING %r states no test - an inferred claim "
                     "is a tested hypothesis" % label[:60])
            if f["tier"] == "speculative" and not f.get("would_confirm"):
                fail("speculative FINDING %r states nothing that would "
                     "confirm or kill it" % label[:60])
        if kind in ("FINDING", "CORRECTION") and \
                not EVIDENCE_RE.match(f["evidence"]):
            fail("%s %r evidence does not open with the catnip tool it "
                 "rests on: %r" % (kind, label[:60], f["evidence"][:80]))
        parsed[kind].append(f)

    if not any(parsed.values()):
        # A seeded angle may legitimately be refutation- or watch-only; a
        # package with no blocks at all recorded nothing.
        fail("no blocks in the analysis - nothing to record")

    # An agent's context has no prowl-vs-report lineage - it cannot know
    # whether a killed claim was a prior prowl's. CORRECTION is therefore not
    # a model block type: legacy blocks reclassify as refutations, and
    # publish promotes a refutation to the Correction section only when it
    # matches a previous cycle's actual heading.
    for c in parsed.pop("CORRECTION"):
        parsed["REFUTED"].append({
            "hypothesis": c["claim"].strip('"'),
            "checked_with": c["evidence"].split()[0],
            "why_not": c["body"],
        })
        print("  reclassified correction -> refuted: %s" % c["claim"][:50])

    prowl = Path(".state/prowl")
    prowl.mkdir(parents=True, exist_ok=True)
    if window:
        (prowl / ("%s.window" % cycle)).write_text(window + "\n")

    with open(prowl / ("%s.md" % cycle), "a") as fh:
        for f in parsed["FINDING"]:
            fh.write("### %s  `%s`\n\n" % (f["claim"], f["tier"]))
            if f.get("body"):
                fh.write("%s\n\n" % f["body"])
            for key, label in (("test", "Test"), ("read", "Read"),
                               ("would_confirm", "Would confirm further"),
                               ("refutes", "Refutes")):
                if f.get(key):
                    fh.write("**%s:** %s\n\n" % (label, f[key]))
            fh.write("**Evidence:** %s\n\n" % f["evidence"])
            print("  filed %-12s %s" % (f["tier"], f["claim"][:64]))

    if parsed["REFUTED"]:
        with open(prowl / ("%s.refuted.md" % cycle), "a") as fh:
            for r in parsed["REFUTED"]:
                fh.write("- %s - checked with %s; did not hold: %s\n" % (
                    oneline(r["hypothesis"]), oneline(r["checked_with"]),
                    oneline(r["why_not"])))
                print("  refuted      %s" % r["hypothesis"][:64])

    if parsed["WATCH"]:
        with open(prowl / ("%s.watch.md" % cycle), "a") as fh:
            for w in parsed["WATCH"]:
                fh.write("- **%s** - %s\n" % (
                    oneline(w["item"]), oneline(w["body"])))
                print("  watch        %s" % w["item"][:64])

    print("filed %d finding(s), %d refutation(s), %d watch item(s)" % tuple(
        len(parsed[k]) for k in ("FINDING", "REFUTED", "WATCH")))
    # Line-anchored for the extract rules, like the counts below.
    print("cycle %s" % cycle)
    sys.stdout.flush()
    # prowl-publish.sh owns assembly and prints the authoritative counts.
    rc = subprocess.call(["./scripts/prowl-publish.sh", cycle])
    if rc != 0:
        raise SystemExit(rc)

    # The assembled document goes to the editor before anything reaches the
    # published prowl.md - the guarded edit-publish is the only writer there.
    import urllib.request
    assembled = (prowl / ("%s.assembled.md" % cycle)).read_text()
    req = urllib.request.Request(
        "http://127.0.0.1:7751/intake"
        "?kind=prowl.assembled&source=prowl-record&queue=editor-in",
        data=assembled.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        fail("recorded and assembled, but the editor handoff failed: %s" % e)
    print("handed to editor")


if __name__ == "__main__":
    main()
