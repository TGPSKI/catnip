# catnip tannery

A [leather](https://github.com/TGPSKI/leather) tannery that collects, reports,
and prowls on a schedule.

```bash
cd tannery
make validate     # leather validate every agent, lifecycle and toolset
make smoke-tools  # exec the read-only tools for real
make serve        # run the scheduler
```

| agent | when | does |
|---|---|---|
| `catnip-collect` | daily 05:07 | `catnip run`, then checks it landed |
| `catnip-report` | daily 06:52 | writes the deterministic report |
| `catnip-prowl` | every 3rd day 08:22 | hunts what the report does not answer |

## Turns are the bounds

Each agent is multi-turn, and every turn replaces the tool scope rather than
extending it. An agent can only reach the tools its current turn declares.

```
catnip-collect   catnip-pipeline -> catnip-inspect -> catnip-record
catnip-report    catnip-inspect  -> catnip-report-write -> catnip-record
catnip-prowl     catnip-evidence -> catnip-file -> catnip-publish
```

That is the whole design. `catnip-collect` cannot write state during the turn
that runs the pipeline. `catnip-report` cannot reach the writer until after it
has read the store's newest day. `catnip-prowl` cannot file a finding during
the turn it gathers evidence, and cannot gather more once it starts filing -
so everything it files came from evidence already in context.

"Test before you file" is not an instruction the prowl agent is asked to
follow. There is no turn in which it can do otherwise.

## A registry that fails to load is silent

Four ways a misconfigured registry loads as zero tools while `leather validate`
still passes. The agent keeps seeing tool names in its system prompt, so it
fabricates: it emits the calls it meant to make as text and reports outcomes
that never happened. Unattended at 05:07, that writes a confident, invented
status artifact.

| symptom | upstream |
|---|---|
| duplicate tool name across two skills kills the whole registry | [#71](https://github.com/TGPSKI/leather/issues/71) |
| an agent resolving to zero tools runs anyway | [#72](https://github.com/TGPSKI/leather/issues/72) |
| `toolsets:` naming a `*.skill.yaml` silently contributes nothing | [#73](https://github.com/TGPSKI/leather/issues/73) |
| flow-style `mcp: { }` parses to an empty server | [#74](https://github.com/TGPSKI/leather/issues/74) |

These belong in the runtime, not in a lint each tannery carries a copy of, so
they are filed rather than guarded here.

## Verdicts come from tools, not from text

`catnip run` streams ~41 minutes of progress that reads identically whether it
worked. No agent judges it. `catnip-verify` decides completeness by asserting
the CSVs exist; `catnip-store-status` decides whether data landed by reading
the store's newest day.

## Provenance is argument validation

`catnip_prowl_finding` rejects any `tier` outside
`measured|inferred|speculative`, and any `evidence` not matching
`^catnip-(report-read|view|derivation|store-series|store-status)\b`. A claim
that names no tool cannot be filed. `catnip_prowl_publish` fails on an empty
cycle rather than writing an empty file.

Output is `prowl.md` beside the `report.md` it companions - separate files
because one reproduces from the store and one does not.

## Timeouts stack, innermost first

| layer | where | `catnip_run` |
|---|---|---|
| 1 | `shell-tools.json` `timeout_seconds` | 5400s, shell-mcp SIGKILLs the process |
| 2 | lifecycle `tool_timeout` | 6000s, leather abandons the MCP call |
| 3 | lifecycle `timeout` | 6600s, leather abandons the run |

Size layer 1 off a measurement. The first draft used 2100s against a pipeline
that takes 2459s, so it would have killed its own first real run at 35 minutes
and called it a timeout. Layer 1 defaults to 30s when omitted, and omitting it
fails quietly: the process dies while its child runs on as an orphan.

The schedule is a chain. A typical 41-minute collect ends 05:48 and leaves the
report 64 minutes; a pathological 90-minute one ends 06:37 and leaves 15. If it
ever overruns, the store has not advanced, the guard refuses, and the report
records `skipped`.

## Setup

Tools invoke `catnip` from `PATH`, so `make install` in the repo root first.
Nothing hardcodes a data directory: `catnip config` resolves it and
`CATNIP_CONFIG` selects between accounts.

Point `model` and `llm_endpoint` in `config.yaml` at whatever you serve. No
frontier model is needed - every number an agent reports comes back from a
tool.

`make smoke-tools` execs each read-only tool's exact argv from
`shell-tools.json`. It is what caught `catnip view why` being unable to run at
all. Writers are skipped: a smoke test that fetches the whole account is not a
smoke test.
