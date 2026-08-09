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
        fail(f"document must start with the '# prowl' header, got {doc[:40]!r}")

    m = COUNTS_RE.search(doc)
    if not m:
        fail("document carries no counts comment - the assembler stamps it "
             "and the editor keeps it verbatim")
    stamped = dict(zip(TIERS, (int(x) for x in m.groups()), strict=False))

    composed = {t: len(re.findall(rf"^### .*`{t}`", doc, re.M))
                for t in TIERS}
    if composed != stamped:
        fail("tier counts differ from the document's own stamp - composed "
             f"{composed} vs stamped {stamped}. The editor arranges; it does not drop or "
             "invent findings")

    cycle_state = Path(".state/prowl-cycle.json")
    if not cycle_state.exists():
        fail("no .state/prowl-cycle.json - nothing recorded this cycle")
    recorded = json.loads(cycle_state.read_text())["findings"]
    if stamped != recorded:
        print(f"superseded - the cycle advanced to {recorded} after this document "
              f"was assembled at {stamped}; a fresher edit is queued")
        return

    # `catnip report --locate`, not a glob: reports are named for the period
    # they cover, and a promoted one sorts before its own superseded stamped
    # siblings. Sorting names would publish beside an older recomputation.
    found = json.loads(subprocess.check_output(
        ["catnip", "report", "--locate"], text=True))
    if not found.get("prowl"):
        fail("no report directory to publish beside")
    out = Path(found["prowl"])
    body = COUNTS_RE.sub("", doc).rstrip() + "\n"
    out.write_text(body)

    print(f"wrote {out}")
    for t in TIERS:
        print(f"  {t:<12} {composed[t]}")


if __name__ == "__main__":
    main()
