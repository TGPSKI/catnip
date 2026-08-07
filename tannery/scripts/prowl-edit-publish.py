#!/usr/bin/env python3
"""Write the editor's document to the published prowl.md - guarded.

The editor arranges, merges and cuts; it must not drop or invent findings.
Two distinct checks, because two distinct things go wrong:

- fidelity: the document's tier counts must equal its own embedded counts
  comment (stamped by the assembler). A mismatch is the editor's doing -
  refuse loudly.
- staleness: the embedded counts must equal the cycle's current record in
  .state/prowl-cycle.json. A mismatch means more packages landed after this
  document was assembled - skip cleanly; a fresher edit is already queued.

    usage: prowl-edit-publish.py <document>
"""
import json
import re
import subprocess
import sys
from pathlib import Path

TIERS = ("measured", "inferred", "speculative")
COUNTS_RE = re.compile(
    r"<!-- counts measured=(\d+) inferred=(\d+) speculative=(\d+) -->")


def fail(msg):
    sys.exit("prowl-edit-publish.py: " + msg)


def main():
    if len(sys.argv) != 2:
        fail("usage: prowl-edit-publish.py <document>")
    doc = sys.argv[1]
    if not doc.startswith("# prowl"):
        fail("document must start with the '# prowl' header, got %r"
             % doc[:40])

    m = COUNTS_RE.search(doc)
    if not m:
        fail("document carries no counts comment - the assembler stamps it "
             "and the editor keeps it verbatim")
    stamped = dict(zip(TIERS, (int(x) for x in m.groups())))

    composed = {t: len(re.findall(r"^### .*`%s`" % t, doc, re.M))
                for t in TIERS}
    if composed != stamped:
        fail("tier counts differ from the document's own stamp - composed "
             "%s vs stamped %s. The editor arranges; it does not drop or "
             "invent findings" % (composed, stamped))

    cycle_state = Path(".state/prowl-cycle.json")
    if not cycle_state.exists():
        fail("no .state/prowl-cycle.json - nothing recorded this cycle")
    recorded = json.loads(cycle_state.read_text())["findings"]
    if stamped != recorded:
        print("superseded - the cycle advanced to %s after this document "
              "was assembled at %s; a fresher edit is queued" % (
                  recorded, stamped))
        return

    cfg = json.loads(subprocess.check_output(
        ["catnip", "config", "--json"], text=True))
    reports = sorted(Path(cfg["paths"]["data_dir"]).glob("reports/*/"))
    if not reports:
        fail("no report directory to publish beside")
    out = reports[-1] / "prowl.md"
    body = COUNTS_RE.sub("", doc).rstrip() + "\n"
    out.write_text(body)

    print("wrote %s" % out)
    for t in TIERS:
        print("  %-12s %d" % (t, composed[t]))


if __name__ == "__main__":
    main()
