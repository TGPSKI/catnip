---
name: catnip-prowl-write
tool_rounds: 6
timeout: 600s
tool_timeout: 120s
thinking: false
toolsets: [catnip-file]
---

You receive a prowl analysis: CYCLE and WINDOW lines, then CORRECTION,
FINDING, REFUTED and WATCH blocks.

Record it as given. Do not judge a block, merge two blocks, add one the
analysis did not make, or drop one because it looks weak.

Two turns: the first records the package, the second writes the state file.
write_state only becomes available in the second.

---
Call catnip-prowl-record once. cycle is the date on the CYCLE line, copied
verbatim; analysis is everything below that line, unchanged. Parsing and
counting the blocks is the tool's job, not yours - it records every block or
fails naming the one it refused.

If it refuses, do not edit the package to satisfy it - a package you altered
is no longer the analysis. Move on and record the rejection verbatim as
action: failed.

Either way, close the turn with one line: what was recorded, or the
rejection it named.

---
toolsets: [catnip-record]
Write this to .state/catnip-prowl.json with write_state, on failure as well
as success:

cycle:                {{prowl_cycle}}
findings_measured:    {{prowl_measured}}
findings_inferred:    {{prowl_inferred}}
findings_speculative: {{prowl_speculative}}
refuted:              {{prowl_refuted}}
published:            {{prowl_published}}
action:               success | failed
reason:               <one sentence>

Then reply with exactly:
DONE: prowl-write <action> cycle={{prowl_cycle}}
