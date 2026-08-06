---
name: phase-02-configuration
description: "Write the config file: owner, repo selection, data directory — verified against a real dry run before anything is collected."
parent: catnip-onboarding
---

# Phase 2: Configuration

Produces: a config file that `catnip config` reports as live, whose repo
selection the user has actually seen.

**Carry forward from prior phases**

- The authenticated login, from Phase 1 (`catnip doctor` → `authenticated as`).
- The owner the user named in the router's entry point, if it differs.

## Step 1: Decide where the config lives

**Inspect**

```bash
catnip config | head -2
```

| Status | Action |
|---|---|
| `config file : (defaults only …)` | No config yet — continue to Step 2 |
| A path is printed | A config already exists. Show it to the user and ask whether to modify or replace it. Do not overwrite silently |

**Decide** — two locations, and the choice has consequences:

| Location | Command | Choose when |
|---|---|---|
| `~/.config/catnip/catnip.conf` | `catnip init` | Default. Works from any directory, which is what the timer needs |
| `./catnip.conf` | `catnip init --local` | The user wants a per-project config, or is collecting more than one account |

A repo-local config is only found when catnip runs from that directory.
The timer pins an absolute path at install time, so it still works — but
the user typing `catnip run` from `~` will silently get different
settings. Say that out loud if they choose local.

## Step 2: Determine the owner and its type

**Inspect** — never guess this. A wrong owner produces runs that succeed
and collect nothing.

```bash
gh api user --jq .login                      # who the token is
gh api "users/<candidate>" --jq .type        # User or Organization
```

| Observation | Config |
|---|---|
| Owner == authenticated login | `CATNIP_OWNER` may be left blank (auto-detects), but writing it explicitly is clearer |
| Owner is an Organization the user administers | `CATNIP_OWNER=<org>` and `CATNIP_OWNER_TYPE=org` |
| `gh api users/<candidate>` 404s | The name is wrong. Ask again; do not proceed |
| Owner is an org the user does **not** administer | Stop. Traffic will be denied for every repo. Say so rather than producing an empty collection |

Setting `CATNIP_OWNER_TYPE` explicitly (rather than `auto`) saves one API
probe per run and removes a class of ambiguity. Set it when you know.

## Step 3: See what the default selection would collect

**Inspect** — before asking the user anything about filtering, show them
the actual answer for their account:

```bash
gh api "user/repos?affiliation=owner&per_page=1" -i --jq 'empty' | grep -i '^link:'
```

Or more simply, write a first-draft config and dry-run it (Step 4 makes
this cheap — a dry run costs one listing call and fetches no detail).

**Decide** — filtering by repo count:

| Repos owned | Recommendation |
|---|---|
| Under ~200 | Collect everything. Leave `CATNIP_INCLUDE` empty. Forks stay off by default |
| 200–500 | Everything still fits in the hourly limit (~10 calls/repo against 5,000). Consider `CATNIP_FETCH_ISSUES=false` if runs feel slow |
| Over ~500 | Narrow it. Either `CATNIP_INCLUDE` with globs for the repos they care about, or turn off `CATNIP_FETCH_STATS` (6 calls/repo) |

Ask about exclusions in the user's own terms — "are there repositories
whose traffic you do not care about?" — rather than asking them to write
globs. Then translate.

## Step 4: Generate the config

**Generate** — use `catnip init`, not a hand-written file. It writes the
annotated template, and it parses the result back to prove the file is
readable.

```bash
catnip init \
  --owner "<owner>" \
  --owner-type <auto|user|org> \
  --exclude "<globs or empty>" \
  --retain-days 30 \
  --no-input
```

Write to: `~/.config/catnip/catnip.conf` (or `./catnip.conf` with
`--local`).

The full key reference, with what each one costs, is
`docs/configuration.md` and `catnip.conf.example`. Do not invent keys —
an unrecognized key is a hard error, deliberately, so that a typo cannot
silently collect the wrong thing.

## Step 5: Prove the selection is what the user expects

**Inspect**

```bash
catnip fetch --dry-run
```

This resolves the config, lists the account's repositories, applies every
filter, and prints both sides without fetching any detail.

```
--- would fetch (24) ---
catnip
pane
…
--- skipped (151) ---
old-fork        fork (CATNIP_INCLUDE_FORKS=false)
scratch         matched CATNIP_EXCLUDE
```

| Observation | Action |
|---|---|
| The list matches what the user expects | Phase complete |
| A repo they care about is in `skipped` | Read the reason — it names the exact key. Adjust that key and re-run |
| Everything was filtered out | The command fails loudly. Almost always an over-narrow `CATNIP_INCLUDE` |
| Far more repos than expected | Forks or archived repos. `CATNIP_INCLUDE_FORKS` / `CATNIP_INCLUDE_ARCHIVED` |

Show the user the selected list. It is the last cheap moment to catch a
misunderstanding — after this, the next command spends real API calls.

## Checkpoint

**Files written**:

- `~/.config/catnip/catnip.conf` (or `./catnip.conf`)

**Verify**:

```bash
catnip config           # names the file, the owner, and every derived path
catnip fetch --dry-run  # selection matches expectations
```

**Next phase**: @phase-03-first-collection.md — collect and verify a real
run.
