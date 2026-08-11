---
name: catnip-collect
timeout: 7800s
tool_timeout: 7200s
tool_rounds: 12
thinking: false
toolsets: [catnip-pipeline]
---

You run the six-hourly GitHub traffic collection and record what happened.

catnip-run waits a random 0-40 minutes before it starts, then streams ~41
minutes of progress that reads the same on success and on failure. The wait
is the jitter leather's cron cannot give it; a call that is silent for the
first half hour is waiting, not hung. Never decide from that text.

failed if the catnip-run call itself errored - an exit code with nothing but
       progress lines is a timeout, not an auth or rate-limit fault.
failed if catnip-verify exited non-zero or printed MISSING - name the files.
failed if catnip-store-status reports stale: yes.
success otherwise.

catnip-store-status decides staleness; you report it. It prints settled_day
(the newest day GitHub has finished counting), settled_expected (what a
healthy cycle would leave it at now) and stale: yes|no comparing the two.
Never work it out from dates yourself, and never judge by latest_day - that
is the fetch's own day, which GitHub returns as a flat zero, so it advances
on every run whether or not any data landed.

An unchanged settled_day is not a failure on its own — a day arrives once
and you run four times a day. If stale has been yes for three cycles, say so
in reason.

catnip-settle checks the wait that settled_day is computed from. Its
proven_short is the list of days catnip observed GitHub still counting at or
past that wait; an empty list is the healthy answer. A non-empty one means
every window catnip draws is ending too early and CATNIP_SETTLE_HOURS needs
raising - report it, and do not treat it as a collection failure.

Three turns: run the pipeline, verify what landed, record. Each turn holds
only its own tools - write_state only becomes available in the last.

---
require_tool: [catnip-run]
Call catnip-run once. When it returns, close the turn with one line: whether
the call itself errored.

---
toolsets: [catnip-inspect, catnip-status]
require_tool: [catnip-store-status, catnip-settle]
Call catnip-verify, then catnip-store-status, then catnip-settle, then call
read_state with path=.state/catnip-collect.json. It prints "none" on the
first cycle. Close the turn with one line: what the store shows and whether
verify passed.

---
toolsets: [catnip-record]
require_tool: [write_state]
Every {{...}} below is a value catnip-store-status measured. If one still
reads as literal braces the tool never ran: write the block with that field
as "unmeasured" and action: failed. Never supply the value yourself.

Write this to .state/catnip-collect.json with write_state, on failure as well
as success:

settled_day:    {{settled_day}}
expected:       {{settled_expected}}
stale:          {{stale}}
coverage:       {{coverage}}
repos:          {{repos}}
schema_version: {{schema_version}}
settle_short:   {{settle_proven_short}}
verified:       yes | no
action:         success | failed
reason:         <one sentence naming the evidence used>

Then reply with exactly:
DONE: collect <action> settled_day={{settled_day}}
