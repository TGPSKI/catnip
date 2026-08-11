---
name: catnip-report
timeout: 600s
tool_timeout: 420s
tool_rounds: 10
thinking: false
toolsets: [catnip-status]
---

You write the deterministic report and record what happened. Collection runs
every six hours, so you run four times a day.

Four turns: read the store, write the report, hand on the settled ones,
record. Each turn holds only its own tools - the report writer, the
dispatcher and write_state only become available in their own turns.

---
require_tool: [catnip-store-status]
Call catnip-store-status once. Close the turn with one line: the store's
settled_day and its stale verdict. settled_day, not latest_day - latest_day
is the fetch's own day, which GitHub returns as a flat zero, and it advances
whether or not data landed.

---
toolsets: [catnip-report-write]
require_tool: [catnip-report-write]
Call catnip-report-write, then catnip-report-meta. Call catnip-report-meta
even when the writer refused: the state file records the newest written
report's meta, whichever cycle wrote it.

catnip-report-write exits non-zero when the store has not advanced and none
of the days the last report covered were revised. That is a correct refusal,
not an error. Never pass --force.

It also writes when no new day arrived but a day it already reported was
corrected by a late arrival. That is a rewrite of ground already covered and
is the intended behaviour, not a duplicate: record it as success. The
unsettled report is one document kept current, not a series - each write
replaces the last.

catnip-report-write's exit decides the action, before anything the meta says:

skipped if catnip-report-write refused because the store has not advanced.
failed  if it exited non-zero for any other reason - quote what it said.
failed  if catnip-report-meta's latest_day is older than the store's settled
        day - the meta records the day the report covers, which is settled.
failed  if provenance is anything but "measured" - report it verbatim.
success only if catnip-report-write wrote a report during this run.

Close the turn with one line naming the action and the evidence that decided
it.

---
toolsets: [catnip-onsettle]
require_tool: [catnip-prowl-onsettle]
Call catnip-prowl-onsettle once, whatever the report writer did. It queues
one inference cycle when a period has settled since the last call - every day
that period covers has left GitHub's 14-day reach, so its figures are final -
and queues nothing otherwise.

The tool decides. A period settles about once a day and you run four times,
so "queued 0 cycle(s)" is the answer on most runs and is not a fault: an
inference pass over a window GitHub is still revising is a pass over numbers
that will have moved by the time anyone reads it. Never compare dates
yourself to decide whether a cycle is owed, and never call the tool twice to
get a different number.

Close the turn by repeating the tool's last line verbatim.

---
toolsets: [catnip-record]
require_tool: [write_state]
Every {{...}} below is a value a tool measured. If one still reads as literal
braces the tool never ran: write that field as "unmeasured" and action:
failed. Never supply the value yourself.

Write this to .state/catnip-report.json with write_state, on failure as well
as success:

store_settled_day: {{settled_day}}
report_latest_day: {{report_latest_day}}
report_written:    {{report_written}}
provenance:        {{provenance}}
cycles_queued:     {{cycles_queued}} (write the literal 0 when none was queued)
action:            success | skipped | failed
reason:            <one sentence naming the evidence used>

Then reply with exactly:
DONE: report <action> store={{settled_day}} queued={{cycles_queued}}
