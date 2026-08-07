---
name: catnip-prowl-write
tool_rounds: 20
timeout: 600s
tool_timeout: 120s
thinking: false
toolsets: [catnip-file]
---

You receive a prowl analysis: a CYCLE line, then FINDING and REFUTED blocks.

Record it as given. Do not judge a block, merge two blocks, add one the
analysis did not make, or drop one because it looks weak. Copy CYCLE verbatim
into every call.

---
toolsets: [catnip-file]
Call catnip-prowl-finding once per FINDING block, passing its tier, claim,
evidence and body unchanged, and catnip-prowl-refuted once per REFUTED block,
passing its hypothesis, checked_with and why_not unchanged.

The number of calls equals the number of blocks. Do not repeat a block you
have already recorded.

---
toolsets: [catnip-publish, catnip-record]
Call catnip-prowl-publish with the same cycle.

Then write this to .state/catnip-prowl.json with write_state, on failure as
well as success:

cycle:                <the CYCLE line from the analysis>
findings_measured:    <count from catnip-prowl-publish>
findings_inferred:    <count from catnip-prowl-publish>
findings_speculative: <count from catnip-prowl-publish>
refuted:              <count from catnip-prowl-publish>
published:            <path from catnip-prowl-publish, or none>
action:               success | failed
reason:               <one sentence>

Then reply with exactly:
DONE: prowl-write <action> cycle=<cycle>
