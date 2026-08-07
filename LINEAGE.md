# Lineage

catnip was not designed from scratch; it is a working private pipeline
taken apart and put back together for people other than its author. This
file records where each part came from, and what changed on the way out.

## Timeline

| Date | Event |
|---|---|
| 2026-07-11 | Generation 0: `sh-github-analytics` — a private single-account pipeline (`fetch-github.sh` + `analyze-github.py`) with its own copies of `put`, bar drawing, and formatters, alongside a sibling nginx dashboard |
| 2026-07-13 | The drawing layer is extracted out of both dashboards into `shared/tui/{framework,charts,fmt,windows}.py` in a private operations repo |
| ~2026-07-16 | The 14-day problem is met in production: fetch dirs pruned before ingest lose traffic days permanently. `ingest-history.py` and the max-merge rule are written in response |
| 2026-07-17 | The stats warm-up split (fire `stats/*` up front, collect in a final pass) replaces ~118 minutes of per-endpoint retry-sleep per run |
| 2026-08-04 | The drawing layer becomes [pane](https://github.com/TGPSKI/pane), its own public repository and canonical upstream |
| 2026-08-05 | The pipeline is generalized: configuration, repo selection, owner auto-detection, scheduling, retention with a data-loss guard |
| 2026-08-06 | **catnip 0.2.0** goes public — views rebuilt on the durable store, `catnip report`, the agent skills |

## What was inherited

The parts that had already survived contact with production, ported with
their behavior intact:

- **`fetch.sh`** — the retry/backoff wrapper, JSON-validate-before-`mv`,
  page merging in Python rather than by concatenation (raw concatenation
  produced corrupt `...}][{` bodies that only failed at analyze time),
  the rate-limit wait, and the stats warm-up/collect split.
- **`analyze.py`** and the five deep-traffic stages — anomaly detection
  (MAD), clone-intent profiling, path-taxonomy funnels, cross-repo
  Pearson correlation, and cosine clustering.
- **`history.py` / `totals.py`** — the max-merge dedupe and the
  pure-rebuild rule, both of which exist because the naive versions
  (sum-over-snapshots, accumulate-into-own-output) inflated clone counts
  roughly threefold before anyone noticed.
- **`ui.py`** — twelve views, the timeframe windowing, and the rule that
  the top lists must describe the same window as the chart above them.
  `tests/test_tui_data.py` came along with it.

## What is new in catnip

Everything that makes it usable by someone other than its author:

- `config.py` — one resolution order shared by bash and Python, repo
  include/exclude globs, fork/archived/private toggles, owner
  auto-detection from the authenticated `gh` account, and unknown keys
  as hard errors.
- `bin/catnip` — a single entry point over what had been eight scripts
  and a Makefile of ad-hoc targets.
- `doctor.py` — the checks that used to live in the author's head:
  token scopes, the push-access requirement for traffic, whether the
  configured owner matches the token, run freshness, artifact
  completeness.
- `prune.py` — retention with the guard that the original lacked. The
  old `make clean-runs` trusted you to run `make history` first; this
  one refuses to delete a run that is not in the store.
- `timer.py` + `systemd/` — the schedule, which had been a person
  remembering to run `make auto`.
- `init.py`, the two agent skills, and an offline test suite that builds
  synthetic runs so CI needs no token.

## Provenance of the vendored drawing layer

`src/catnip/tui/` is copied byte-identically from
[pane](https://github.com/TGPSKI/pane) at commit `9099e39` (2026-08-04)
by pane's own `tools/vendor.sh`. SHA-256 at copy time:

```
5536c966b65b811f6f54e03612b766d56879f85d93319a05e0158ba13d030644  charts.py
fa0de46ff0c8accd065febbcc911866fd610470659fe88c7b8ab671b2cd31597  framework.py
7819beb686d702c5f7b9585b64ea453f3c8b6a2004f339bf52f1812d9d3fb85f  fmt.py
b01658da17f6f1bac21f3eac9431fdbcee17a3f2600d1ed1ac5992b4691c008c  interact.py
(windows.py as shipped at that commit)
```

`make vendor-check PANE=../pane` re-proves the claim in one command.
`tests/pty_smoke.py` is also from pane, copied deliberately rather than
vendored: pane excludes it from the vendor set because it is test
infrastructure with exactly one home per consumer.

The relationship runs one way. pane owns the API; catnip re-vendors
rather than diverging. Note the license consequence: pane is GPL-3.0 and
catnip vendors it, so catnip is GPL-3.0 too.

## Sibling projects

[run-watcher](https://github.com/TGPSKI/run-watcher) is the skill that
teaches building watchers and live views on top of pane; catnip's UI is
one instance of what it describes. The private nginx dashboard that
`sh-github-analytics` grew up beside is where the max-merge rule was
first written, and is not public.
