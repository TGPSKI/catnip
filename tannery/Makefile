.PHONY: all validate serve smoke-tools run-collect run-report run-prowl run-prowl-write clean

all: validate

validate: ## leather-validate every agent, lifecycle and toolset
	leather validate --config config.yaml

serve: ## run the scheduler (collect 05:07, report 06:52, prowl every 3d 08:22)
	leather serve --pretty --stats --config config.yaml --tannery tannery.yaml

# Exec every read-only tool's exact argv straight from shell-tools.json. This
# catches argv and quoting regressions before the scheduler ever runs them  - 
# a tool that is wrong here fails at 05:07 with nobody watching. The writers
# (catnip-run, catnip-report-write, the prowl recorders) are deliberately not
# exercised: they have side effects, and a smoke test that fetches the whole
# account is not a smoke test.
smoke-tools: ## exec the read-only tools for real
	@./scripts/tool-smoke.sh

run-collect: ## run the collect agent once, now
	leather run --config config.yaml agents/catnip-collect.agent.md

run-report: ## run the report agent once, now
	leather run --config config.yaml agents/catnip-report.agent.md

run-prowl: ## run the prowl analysis once, now (writes nothing; feeds prowl-write-in)
	leather run --config config.yaml agents/catnip-prowl.agent.md

# The writer only ever runs from the queue - it has no schedule, and running it
# standalone would give it no analysis to record. This ingests the newest prowl
# analysis and lets the curing worker drain it, which is what happens unattended.
run-prowl-write: ## record the newest prowl analysis through the curing queue
	leather ingest --config config.yaml --tannery tannery.yaml \
	  --kind prowl.analysis --source cli \
	  --curing prowl-write --queue prowl-write-in \
	  $$(ls -t .state/artifacts/*-catnip-prowl.txt | head -1)
	leather serve --config config.yaml --tannery tannery.yaml \
	  --stats --run-duration 90s

clean: ## remove per-cycle prowl scratch (published prowl.md files are kept)
	@rm -rf .state/prowl && echo "cleaned .state/prowl"
