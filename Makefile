PYTHON ?= python3
PY      = PYTHONPATH=src $(PYTHON)
CATNIP  = ./bin/catnip

# Where `make install` puts the `catnip` symlink. bin/catnip resolves its own
# root through `readlink -f`, so a link from anywhere finds src/ correctly —
# there is nothing to copy and nothing to rebuild after a `git pull`.
#
# Prefer a conventional user bin directory that is ALREADY on PATH, so the
# install needs no shell-profile edit. Deliberately not chosen: a version
# manager's shim directory (asdf, mise, rbenv, pyenv). Those are generated —
# `asdf reshim` rewrites ~/.asdf/shims wholesale — so a hand-placed file
# there works until the day it silently disappears. Override freely:
# `make install BINDIR=/usr/local/bin`.
#
# A `case` statement cannot be used here: its `)` would close $(shell early
# and Make would splice the rest of the script into the path.
BINDIR ?= $(shell \
  for d in "$$XDG_BIN_HOME" "$$HOME/.local/bin" "$$HOME/bin"; do \
    [ -n "$$d" ] || continue; \
    if printf ':%s:' "$$PATH" | grep -qF ":$$d:"; then printf '%s' "$$d"; exit 0; fi; \
  done; \
  printf '%s' "$${XDG_BIN_HOME:-$$HOME/.local/bin}")

# Agent harnesses that discover skills at <dir>/skills. `.agents/` is the
# canonical location in this portfolio; each harness gets a symlink rather
# than a copy, so a skill is edited in exactly one place.
HARNESSES ?= .claude .cursor .opencode

# Where a pane checkout lives, for the vendor byte-identity check.
PANE ?= ../pane

.DEFAULT_GOAL := help

.PHONY: help check quick compile test test-fast lint smoke shellcheck \
        install uninstall link-agents unlink-agents \
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

# ── install ──────────────────────────────────────────────────────────────────

install: ## Symlink bin/catnip into a user bin dir on PATH (override BINDIR=)
	@mkdir -p "$(BINDIR)"
	@ln -sfn "$(CURDIR)/bin/catnip" "$(BINDIR)/catnip"
	@echo "linked $(BINDIR)/catnip -> $(CURDIR)/bin/catnip"
	@case ":$$PATH:" in \
	  *":$(BINDIR):"*) ;; \
	  *) echo; \
	     echo "  $(BINDIR) is not on your PATH. Add it:"; \
	     echo "    export PATH=\"$(BINDIR):\$$PATH\"";; \
	esac
	@# A version manager's shims come first on PATH in most setups. If one
	@# already answers to `catnip`, the link just installed is shadowed and
	@# the operator would otherwise debug a stale binary.
	@found=$$(command -v catnip 2>/dev/null || true); \
	if [ -n "$$found" ] && [ "$$found" != "$(BINDIR)/catnip" ]; then \
	  echo; \
	  echo "  WARNING: '$$found' comes first on PATH and shadows this install."; \
	  echo "  Remove it, or put $(BINDIR) earlier in PATH."; \
	fi
	@command -v gh >/dev/null 2>&1 || { \
	  echo; echo "  catnip needs the GitHub CLI: https://cli.github.com"; }

uninstall: ## Remove the BINDIR symlink (never touches collected data)
	@if [ -L "$(BINDIR)/catnip" ]; then \
	  rm "$(BINDIR)/catnip"; echo "removed $(BINDIR)/catnip"; \
	elif [ -e "$(BINDIR)/catnip" ]; then \
	  echo "$(BINDIR)/catnip is not a symlink — leaving it alone"; exit 1; \
	else echo "nothing at $(BINDIR)/catnip"; fi

# ── agent harnesses ──────────────────────────────────────────────────────────
# One canonical .agents/skills/, linked into whichever harness you use, so a
# skill has one copy on disk. Copies drift: the whole point of shipping the
# skills beside the tool is that they describe *this* checkout's commands.

# Per skill, not one link for the directory: a harness directory usually
# holds other things too (settings, other projects' skills), so claiming
# the whole `skills/` name would either fail or bury them.
link-agents: ## Symlink each .agents/skill into .claude/.cursor/.opencode (HARNESSES=...)
	@for dir in $(HARNESSES); do \
	  mkdir -p "$$dir/skills"; \
	  for skill in .agents/skills/*/; do \
	    name=$$(basename "$$skill"); \
	    target="$$dir/skills/$$name"; \
	    if [ -L "$$target" ]; then \
	      echo "ok    $$target"; \
	    elif [ -e "$$target" ]; then \
	      echo "SKIP  $$target exists and is not a symlink"; \
	    else \
	      ln -s "../../$$skill" "$$target" && echo "link  $$target"; \
	    fi; \
	  done; \
	done
	@echo
	@echo "Skills live in .agents/skills/ and are edited there."

unlink-agents: ## Remove the harness symlinks (leaves .agents/ untouched)
	@for dir in $(HARNESSES); do \
	  for skill in .agents/skills/*/; do \
	    target="$$dir/skills/$$(basename "$$skill")"; \
	    if [ -L "$$target" ]; then rm "$$target"; echo "removed $$target"; fi; \
	  done; \
	  rmdir "$$dir/skills" "$$dir" 2>/dev/null || true; \
	done

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
