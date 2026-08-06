PYTHON ?= python3
PY      = PYTHONPATH=src $(PYTHON)
CATNIP  = ./bin/catnip

# Where a pane checkout lives, for the vendor byte-identity check.
PANE ?= ../pane

.DEFAULT_GOAL := help

.PHONY: help check quick compile test test-fast lint smoke shellcheck \
        doctor run fetch analyze history totals tui summary verify prune \
        timer-install timer-status vendor-check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── quality gates ────────────────────────────────────────────────────────────

check: compile test shellcheck ## Compile + full test suite + shell syntax (what CI gates on)

quick: compile test-fast shellcheck ## Everything except the pty smoke — for iterating
	@echo "quick: offline suite only. Run 'make check' before committing."


compile: ## Byte-compile the package and tests
	$(PY) -m compileall -q src tests

test: ## unittest discovery: config, pipeline, retention, units, TUI pty smoke
	$(PY) -m unittest discover -s tests -v

# The pty smoke spawns real terminals and sleeps 0.35s per keypress to let
# curses settle, which is ~95% of `make check`'s wall clock. Everything it
# proves about LAYOUT is already asserted offline against a character grid;
# what only a terminal can prove is that curses does not raise. So iterate
# on this target and gate on `check`.
test-fast: ## Every test except the pty smoke (~4s instead of ~45s)
	CATNIP_SKIP_PTY=1 $(PY) -m unittest discover -s tests -v

lint: ## ruff check (dev-time only; catnip itself is stdlib-only)
	ruff check .

shellcheck: ## Parse every shell entry point (catches quoting bugs without running them)
	@bash -n bin/catnip && bash -n src/catnip/fetch.sh && echo "shell syntax ok"

smoke: ## Drive the TUI in a pty at three terminal sizes over synthetic data
	$(PY) -m unittest tests.test_tui_smoke -v

vendor-check: ## Diff src/catnip/tui against an upstream pane checkout (PANE=../pane)
	@if [ ! -d "$(PANE)/src/pane" ]; then \
		echo "No pane checkout at $(PANE) — skipping (pass PANE=/path/to/pane)"; exit 0; fi; \
	fail=0; for f in framework.py charts.py fmt.py windows.py interact.py; do \
		if diff -q "$(PANE)/src/pane/$$f" "src/catnip/tui/$$f" >/dev/null 2>&1; then \
			echo "OK    tui/$$f"; \
		else \
			echo "DRIFT tui/$$f"; fail=1; fi; done; \
	if [ $$fail -ne 0 ]; then \
		echo; echo "Re-vendor rather than editing in place:  $(PANE)/tools/vendor.sh \
$(CURDIR)/src/catnip/tui"; exit 1; fi

# ── operating catnip ─────────────────────────────────────────────────────────
# Thin wrappers. `bin/catnip` is the real interface; these exist so a
# contributor can drive the tool without leaving the Makefile.

doctor: ## Check tooling, credentials, access, and data
	@$(CATNIP) doctor

run: ## Full pipeline: fetch -> analyze -> history -> totals -> verify
	@$(CATNIP) run

fetch: ## Collect a new run from the GitHub API
	@$(CATNIP) fetch

analyze: ## Re-derive analysis CSVs for the newest run
	@$(CATNIP) analyze

history: ## Ingest runs into the permanent history store
	@$(CATNIP) history

totals: ## Rebuild account-wide totals
	@$(CATNIP) totals

verify: ## Assert every artifact the TUI reads exists
	@$(CATNIP) verify

summary: ## Print the newest run's summary.md
	@$(CATNIP) summary

tui: ## Launch the interactive terminal UI
	@$(CATNIP) tui

prune: ## Dry-run retention (add YES=1 to actually delete)
	@$(CATNIP) prune $(if $(YES),--yes,)

timer-install: ## Install and enable the systemd user timer
	@$(CATNIP) timer install

timer-status: ## Show the timer's schedule and last run
	@$(CATNIP) timer status

clean: ## Remove caches (never touches collected data)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache
