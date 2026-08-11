.PHONY: all validate serve smoke-tools run-collect run-report run-prowl run-prowl-write run-reopen run-onsettle clean

all: validate

validate: ## leather-validate every agent, lifecycle and toolset
	leather validate --config config.yaml

serve: ## run the scheduler (cron: collect every 6h at :07; the rest run when their queues deliver)
	leather serve --pretty --stats --config config.yaml --tannery tannery.yaml

# Exec every read-only tool's exact argv straight from shell-tools.json. This
# catches argv and quoting regressions before the scheduler ever runs them  - 
# a tool that is wrong here fails at 00:07 with nobody watching. The writers
# (catnip-run, catnip-report-write, the prowl recorder) are deliberately not
# exercised: they have side effects, and a smoke test that fetches the whole
# account is not a smoke test.
smoke-tools: ## exec the read-only tools for real
	@./scripts/tool-smoke.sh

run-collect: ## run the collect agent once, now
	leather run --config config.yaml agents/catnip-collect.agent.md

run-report: ## run the report agent once, now
	leather run --config config.yaml agents/catnip-report.agent.md

run-reopen: ## report which published cycles the store has since revised (queues nothing)
	@python3 scripts/prowl-reopen.py --dry-run

run-onsettle: ## report whether a settled period owes an inference cycle (queues nothing)
	@python3 scripts/prowl-onsettle.py --dry-run

# The served chain runs this from prowl-meta-in when a report settles; this
# target is the by-hand version of the same leg.
run-meta: ## size the cycle and seed analyst briefs, now (needs serve up for intake)
	leather run --config config.yaml agents/catnip-prowl-meta.agent.md

run-editor: ## one-shot: edit the newest assembled cycle through the editor queue
	leather ingest --config config.yaml --tannery tannery.yaml \
	  --kind prowl.assembled --source cli --curing editor --queue editor-in \
	  $$(ls -t .state/prowl/*.assembled.md | head -1)
	leather serve --config config.yaml --tannery tannery.yaml \
	  --stats --run-duration 90s

# One-shot testing only - the served chain feeds this queue from the prowl
# lifecycle's intake route. This ingests the newest prowl analysis and lets
# the curing worker drain it, to validate the writer without a schedule.
run-prowl-write: ## one-shot: record the newest prowl analysis through the curing queue
	leather ingest --config config.yaml --tannery tannery.yaml \
	  --kind prowl.analysis --source cli --curing prowl-write --queue prowl-write-in \
	  $$(ls -t .state/artifacts/*-catnip-prowl.txt | head -1)
	leather serve --config config.yaml --tannery tannery.yaml \
	  --stats --run-duration 90s

clean: ## remove per-cycle prowl scratch (published prowl.md files are kept)
	@rm -rf .state/prowl && echo "cleaned .state/prowl"
