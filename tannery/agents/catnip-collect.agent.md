---
name: catnip-collect
timeout: 6600s
tool_timeout: 6000s
tool_rounds: 12
thinking: false
toolsets: [catnip-pipeline]
---

You run the daily GitHub traffic collection and record what happened.

catnip-run streams ~41 minutes of progress that reads the same on success and
on failure. Never decide from that text.

failed if the catnip-run call itself errored - an exit code with nothing but
       progress lines is a timeout, not an auth or rate-limit fault.
failed if catnip-verify exited non-zero or printed MISSING - name the files.
failed if latest_day is older than yesterday.
success otherwise.

An unchanged latest_day is not a failure. If it has not moved for three
cycles, say so in reason.

Three turns: run the pipeline, verify what landed, record. Each turn holds
only its own tools - write_state only becomes available in the last.

---
Call catnip-run once. When it returns, close the turn with one line: whether
the call itself errored.

---
toolsets: [catnip-inspect, catnip-status]
Call catnip-verify, then catnip-store-status, then call read_state with
path=.state/catnip-collect.json. It prints "none" on the first cycle. Close
the turn with one line: what the store shows and whether verify passed.

---
toolsets: [catnip-record]
Write this to .state/catnip-collect.json with write_state, on failure as well
as success:

latest_day:     {{latest_day}}
coverage:       {{coverage}}
repos:          {{repos}}
schema_version: {{schema_version}}
verified:       yes | no
action:         success | failed
reason:         <one sentence naming the evidence used>

Then reply with exactly:
DONE: collect <action> latest_day={{latest_day}}
