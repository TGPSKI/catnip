# catnip tannery

A [leather](https://github.com/TGPSKI/leather) tannery that runs catnip
unattended: collect the account's GitHub traffic daily, write the
deterministic report when the data lands, and every third day send an
analyst hunting for what the report cannot say.

| agent | when | does |
|---|---|---|
| `catnip-collect` | daily 05:07 | `catnip run`, then checks the data landed |
| `catnip-report` | when collect delivers | writes the deterministic report |
| `catnip-prowl-meta` | every 3rd day 08:22 | sizes the cycle, seeds N analyst briefs |
| `catnip-prowl` | one run per brief | investigates one angle |
| `catnip-prowl-write` | one run per package | records into the cycle's assembly |
| `catnip-prowl-editor` | one run per package | edits the assembly into one document |

Only the fetch and the meta-analyst are on cron. Everything downstream runs
when its input arrives: collect's output feeds the `report` curing, each
seed feeds an analyst run, each analyst package feeds the writer. N is the
meta's judgment of the evidence — a quiet window seeds one routine pass, a
rich one seeds an angle per phenomenon — and collation is free: the writer's
per-cycle files accumulate every package, and publishing dedupes into a
staged assembly. The assembly goes to the editor before anything reaches
`prowl.md`: the editor merges, orders and cuts, and its guarded publish —
the only writer of the published file — refuses a document whose tier
counts changed and skips one the cycle has advanced past.

Output lands where catnip already puts reports: `report.md` from the report
agent, `prowl.md` beside it from the prowl chain. They are separate files
because one reproduces byte-for-byte from the store and one is inference.

## What this shows

- **Verdicts come from tools, not from text.** `catnip run` streams ~41
  minutes of progress that reads identically on success and failure, so no
  agent judges it: `catnip-verify` asserts the CSVs exist,
  `catnip-store-status` reads the store's newest day, and the collect agent's
  success/failed verdict cites those.
- **Per-turn tool scoping.** Each agent is multi-turn and a turn *replaces*
  the tool scope rather than extending it. `catnip-collect` cannot write
  state during the turn that runs the pipeline; `catnip-report` cannot reach
  the report writer before reading the store; `catnip-prowl` holds only
  read tools, ever.
- **Stages are joined by queues, not by clock arithmetic.** A producing
  agent's lifecycle POSTs its output to leather's `/intake`, which stores it
  as a hide and enqueues it; the consuming curing does the rest. The queue
  name is the whole routing fact — the producer names it in the intake URL,
  the curing names the same queue as its consumer end. Collect feeds
  `report-in`; prowl feeds `prowl-write-in`. The writer's entire input is
  the analysis, so it cannot re-derive anything — the evidence was never in
  its context.
- **Recording is one deterministic call.** The number of findings varies per
  cycle, so a writer making one tool call per block silently drops one
  whenever its count is off. `catnip-prowl-record` takes the whole analysis,
  parses the blocks itself, and files them all or fails naming the block it
  refused.
- **Provenance is argument validation.** The recorder rejects a `tier`
  outside `measured|inferred|speculative` and any `evidence` that does not
  open with the catnip tool the claim rests on. A claim that names no tool
  cannot be filed. Publishing fails on an empty cycle rather than writing an
  empty file.
- **`{{...}}` means a tool measured it; `<...>` means the model writes it.**
  Extract rules on the skill carry `latest_day`, coverage, report meta and
  the recorder's counts into later turns verbatim, so state files never
  contain a model's transcription of a number. Only judgment fields —
  `action`, `reason`, a finding's prose — are the model's to write.

## Requirements

- `catnip` on `PATH` — `make install` in the repo root. Nothing hardcodes a
  data directory: `catnip config` resolves it, and `CATNIP_CONFIG` selects
  between accounts.
- Any OpenAI-compatible endpoint — point `model` and `llm_endpoint` in
  `config.yaml` at whatever you serve. No frontier model is needed: every
  number an agent reports comes back from a tool.
- `leather` v0.5.3 or newer on `PATH`. Older builds parse `require_tool:` as
  the first line of the prompt instead of enforcing it, which is the whole
  guard below.

## Run

```bash
cd tannery
make validate     # leather validate every agent, lifecycle and toolset
make smoke-tools  # exec each read-only tool's exact argv, for real
make serve        # run the scheduler
```

Each stage also runs one-shot, for testing and agent validation — the served
chain never needs these:

```bash
make run-collect      # fetch now
make run-report       # write the report now
make run-meta         # size the cycle and seed briefs now (serve must be up)
make run-prowl-write  # ingest the newest analysis and drain it once
```

`make smoke-tools` execs each read-only tool's argv straight from
`shell-tools.json`, so an argv or quoting regression surfaces here instead of
at 05:07 with nobody watching. Writers are deliberately skipped: a smoke test
that fetches the whole account is not a smoke test.

## Timeouts stack, innermost first

| layer | where | `catnip_run` |
|---|---|---|
| 1 | `shell-tools.json` `timeout_seconds` | 5400s, shell-mcp SIGKILLs the process |
| 2 | lifecycle `tool_timeout` | 6000s, leather abandons the MCP call |
| 3 | lifecycle `timeout` | 6600s, leather abandons the run |

Size layer 1 from a measurement: a real run took 2459s against a first-draft
bound of 2100s, which would have killed the pipeline at 35 minutes and called
it a timeout. Layer 1 defaults to 30s when omitted, and omitting it fails
quietly — the process dies while its child runs on as an orphan.

The chain cannot race itself: the report runs when collect delivers, however
long the fetch took. If a fetch fails outright and the store has not
advanced, the report writer refuses and the report records `skipped` rather
than describing yesterday as today.

## Acting turns must act

Every turn whose job is to call one tool declares `require_tool:`. A text
response arriving with none of them called is refused and the turn continues;
if its rounds run out the run fails, naming the turn and the tool.

This is not defensive coding. On 2026-08-08 and 2026-08-09 the collect agent
answered turn 1 with "catnip-run call succeeded (no error)" without calling
anything, finished in 1.4s instead of 41 minutes, and wrote
`latest_day: 2024-01-15, coverage: 100%, repos: 5000` into its state file —
every figure invented, and the run recorded `success`. Replaying that turn
against the same served model, the tool was in scope every time and the model
answered in prose in 4 of 16 samples at the configured temperature. A prompt
sentence cannot close that; the turn header can.

The record turns carry a second rule: a `{{value}}` that still reads as
literal braces means its tool never ran, and the field is written
`unmeasured` with `action: failed`. Substitution is the only channel a
measured number travels on, so an unsubstituted one is evidence of absence,
not an invitation to supply it.

## Known upstream issues

A misconfigured tool registry can load as zero tools while `leather validate`
passes; the agent keeps seeing tool names in its system prompt and fabricates
the calls as text. These are filed upstream rather than guarded here, because
they belong in the runtime, not in a lint every tannery carries a copy of:

| symptom | upstream |
|---|---|
| duplicate tool name across two skills kills the whole registry | [#71](https://github.com/TGPSKI/leather/issues/71) |
| an agent resolving to zero tools runs anyway | [#72](https://github.com/TGPSKI/leather/issues/72) |
| `toolsets:` naming a `*.skill.yaml` silently contributes nothing | [#73](https://github.com/TGPSKI/leather/issues/73) |
| flow-style `mcp: { }` parses to an empty server | [#74](https://github.com/TGPSKI/leather/issues/74) |

## Files

| file | purpose |
|---|---|
| `config.yaml` | leather config: model endpoint, API on `127.0.0.1:7751`, scheduler |
| `tannery.yaml` | hide/artifact dirs and the `report-in` / `prowl-write-in` queues |
| `mcp-servers.yaml` | registers shell-mcp, which serves `shell-tools.json` |
| `shell-tools.json` | the `catnip` CLI wrapped as tools, with argv patterns and caps |
| `tools/catnip-tools.skill.yaml` | tool wiring plus every extract rule |
| `tools/*.toolset.yaml` | the per-turn scopes agents declare |
| `agents/catnip-collect.agent.md` | run the pipeline, verify, record |
| `agents/catnip-report.agent.md` | curing-driven: status, write report, record |
| `agents/catnip-prowl-meta.agent.md` | reads the report, sizes the cycle, seeds briefs |
| `agents/catnip-prowl.agent.md` | curing-driven analyst: one brief in, one package out |
| `agents/catnip-prowl-write.agent.md` | curing-driven: one record call, one state write |
| `agents/catnip-prowl-editor.agent.md` | curing-driven: compose the edit, guarded publish |
| `agents/*.lifecycle.yaml` | the two cron agents' schedules, budgets and output routes |
| `curings/report.curing.yaml` | binds `report-in` to the report agent |
| `curings/analyze.curing.yaml` | binds `prowl-analyze-in` to the analyst, packages → writer |
| `curings/prowl-write.curing.yaml` | binds `prowl-write-in` to the writer |
| `curings/editor.curing.yaml` | binds `editor-in` to the editor |
| `scripts/prowl-dispatch.py` | parses SEED blocks, one intake POST per brief |
| `scripts/prowl-record.py` | parses the analysis blocks, files them, hands to the editor |
| `scripts/prowl-edit-publish.py` | the only writer of `prowl.md`; refuses changed counts |
| `scripts/prowl-reopen.py` | queues a re-run of any published cycle whose window was revised |
| `scripts/store-sweep.py` | every repo with traffic on a given day, straight from the store |
| `scripts/store-status.py` | the store's settled day, coverage and staleness as JSON |
| `scripts/prowl-publish.sh` | assembles a cycle's `prowl.md`, records its window digest |
| `scripts/tool-smoke.sh` | execs the read-only tools' real argvs |
