---
name: catnip-report
timeout: 600s
tool_timeout: 420s
tool_rounds: 10
thinking: false
toolsets: [catnip-status]
---

You write the daily deterministic report and record what happened.

Three turns: read the store, write the report, record. Each turn holds only
its own tools - the report writer and write_state only become available in
their own turns.

---
Call catnip-store-status once. Close the turn with one line: the store's
newest day and coverage.

---
toolsets: [catnip-report-write]
Call catnip-report-write, then catnip-report-meta. Call catnip-report-meta
even when the writer refused: the state file records the newest written
report's meta, whichever cycle wrote it.

catnip-report-write exits non-zero when the store has not advanced. That is a
correct refusal, not an error. Never pass --force.

catnip-report-write's exit decides the action, before anything the meta says:

skipped if catnip-report-write refused because the store has not advanced.
failed  if it exited non-zero for any other reason - quote what it said.
failed  if catnip-report-meta's latest_day is older than the store's.
failed  if provenance is anything but "measured" - report it verbatim.
success only if catnip-report-write wrote a report during this run.

Close the turn with one line naming the action and the evidence that decided
it.

---
toolsets: [catnip-record]
Write this to .state/catnip-report.json with write_state, on failure as well
as success:

store_latest_day:  {{latest_day}}
report_latest_day: {{report_latest_day}}
report_written:    {{report_written}}
provenance:        {{provenance}}
action:            success | skipped | failed
reason:            <one sentence naming the evidence used>

Then reply with exactly:
DONE: report <action> store={{latest_day}}
