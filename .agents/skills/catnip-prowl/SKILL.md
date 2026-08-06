---
name: catnip-prowl
description: "Hunt through catnip's collected traffic data for what actually happened — trends, standout anomalies and their causes, coupled repos, audience versus fetchers, and the things strict arithmetic gates out. Produces a dated markdown report. Use when someone asks what their GitHub traffic is doing, what a spike was, whether anyone is actually reading a repo, or wants a periodic analysis written down."
metadata:
  author: catnip
  version: "1.0"
compatibility: "bash, python3 >= 3.10, catnip on PATH, a populated durable store (catnip run at least twice)."
---

# catnip prowl

Go through the data slowly and come back with what is true.

`catnip report` already computes every number that arithmetic can
justify. Your job is the half it cannot do: notice what the numbers are
*about*, chase the odd ones, and say which of your findings are
measurements and which are your own ideas.

## The one rule

**Every claim carries its provenance.** Three tiers, and they are not
decoration:

| tag | means | test |
|---|---|---|
| `measured` | straight from `catnip.derive`, reproducible | it appears in `catnip report` |
| `inferred` | your hypothesis, tested against the data | you state the test and its result |
| `speculative` | a pattern worth watching, unsupported | you say what would confirm it |

A reader must never have to guess whether a sentence is arithmetic or
your idea. Promoting `speculative` to `inferred` because it sounds better
is the failure this skill exists to avoid — the tool's whole credibility
rests on it saying what it does not know.

## Phase 0 — is there anything new to say?

```bash
catnip report --stdout | head -30      # provenance block: coverage, days, run
```

The guard refuses to write a second report until the store's newest day
advances, because catnip collects daily and two reports over identical
data are one finding printed twice. Respect that unless the operator is
prototyping.

- Store has fewer than ~7 days? Say so and keep the report short. Most
  derived views are unavailable and pretending otherwise is the defect.
- No `events` section in the store? Causes are unavailable — that is not
  the same as "nothing shipped".

## Phase 1 — the deterministic floor

```bash
catnip report                          # writes <data>/reports/<stamp>/report.md
catnip report --stdout                 # or read it without writing
```

Read it fully before forming any opinion. It gives you: headline totals
and change, biggest movers with trend, attribution tiers, account events,
audience classification, clone intent with CONFLICT rows, residualized
coupling, content depth, and an explicit list of what it cannot tell you.

Everything in it is `measured`. Quote it; do not recompute it. If you
need a number it does not print, get it from `catnip.derive` rather than
deriving your own — two implementations of one formula is how the report
and the TUI end up disagreeing about the same day.

## Phase 2 — prowl

Now hunt. The report answers the questions someone thought to ask in
advance; you are looking for the ones they did not.

Useful angles, none mandatory:

- **Ratios the report shows separately.** Views over uniques: 92 views
  from 1 unique visitor is one client — often the operator checking their
  own traffic page — while 88 views from 59 uniques is a readership. The
  report prints both columns and draws no conclusion.
- **A repo behaving unlike its class.** A repo classified `audience`
  whose traffic all lands on `/graphs/traffic`. A `crawler` with genuine
  documentation depth.
- **Causes that produced nothing.** Repeated `no-effect` releases are a
  finding about reach, not about the releases.
- **Unexplained movement.** Which repos, which days, and is there a
  shape? Same weekday? Always after a coupled repo moves?
- **The store's own gaps.** A missing day is a timer that did not fire,
  and it silently shortens every window that crosses it.
- **Things that stopped.** A repo that had steady traffic and now has
  none will never appear in "biggest movers" once the decline is old.

For each candidate finding:

1. State it as a hypothesis.
2. Test it — read the raw store, the run CSVs, or call `catnip.derive`.
3. Report the test and the result, whichever way it went. A refuted
   hypothesis is worth a line; it stops the next reader chasing it.
4. Tag it `inferred` if the data supported it, `speculative` if it is
   suggestive but unproven.

```bash
# the raw material
python3 -c "import json;print(json.load(open('$(catnip config --json | python3 -c "import json,sys;print(json.load(sys.stdin)['paths']['history_file'])")')).keys())"
catnip view attribution        # tiers as text
catnip view audience
catnip view deltas
ls "$(catnip config --json | python3 -c "import json,sys;print(json.load(sys.stdin)['paths']['runs_dir'])")"/*/analysis/
```

## Phase 3 — write it

Append your findings to the report directory as `prowl.md`, beside the
deterministic `report.md`. Keep them separate files: one is reproducible
and one is not, and a reader should be able to diff consecutive
`report.md`s without your prose moving underneath them.

Structure:

```markdown
# prowl — <owner>, <window>

Companion to report.md. Findings here are tagged by provenance:
measured (arithmetic), inferred (tested hypothesis), speculative (watch).

## Findings
### <one-line claim>            `inferred`
What I thought, what I checked, what came back. Numbers with their source.

### <one-line claim>            `speculative`
Why it is suggestive, and precisely what would confirm or kill it.

## Refuted
- <hypothesis> — checked <how>, did not hold because <why>.

## Watch next
- <thing that needs more days of store before it can be answered>
```

## What not to do

- **Do not recompute what `derive` computes.** If your number disagrees
  with the report, the bug is yours until proven otherwise.
- **Do not launder speculation.** "Traffic likely came from Hacker News"
  is `speculative` unless the referrer data says so, in which case it is
  `measured`.
- **Do not present a crawler wave as adoption.** The audience view
  classifies for exactly this reason; a 541-clone spike from three unique
  visitors is a fetcher fleet.
- **Do not fill space.** A store four days old supports three paragraphs.
  Writing ten implies evidence that does not exist.
- **Do not touch the TUI.** This skill's output is a file. There is no
  in-app viewer, deliberately: a report is a point-in-time narrative and
  the TUI is live, and putting stale prose beside live numbers is the
  drift the project works hardest to avoid.
