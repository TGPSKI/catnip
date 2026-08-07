---
name: catnip-report
timeout: 600s
tool_timeout: 420s
tool_rounds: 10
thinking: false
toolsets: [catnip-status]
---

You write the daily deterministic report and record what happened.

catnip-report-write exits non-zero when the store has not advanced. That is a
correct refusal, not an error. Never pass --force.

skipped if catnip-report-write refused because the store has not advanced.
failed  if it exited non-zero for any other reason - quote what it said.
failed  if catnip-report-meta's latest_day is older than the store's.
failed  if provenance is anything but "measured" - report it verbatim.
success otherwise.

---
toolsets: [catnip-status]
Call catnip-store-status.

---
toolsets: [catnip-report-write]
Call catnip-report-write, then catnip-report-meta.

---
toolsets: [catnip-record]
Write this to .state/catnip-report.json with write_state, on failure as well
as success:

store_latest_day:  {{latest_day}}
report_latest_day: <the written report's latest_day, or none>
report_written:    <the written report's stamp, or none>
provenance:        <the written report's provenance, or none>
action:            success | skipped | failed
reason:            <one sentence naming the evidence used>

Then reply with exactly:
DONE: report <action> store={{latest_day}}
