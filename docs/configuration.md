# Configuration

Every knob is a `CATNIP_*` key. The bash fetcher and the Python pipeline
read them through the same resolver, so there is exactly one answer to
"which owner is catnip collecting?" at any moment.

## Resolution order

First match wins, per key:

1. the process environment
2. the file named by `--config` or `$CATNIP_CONFIG`
3. `./catnip.conf` in the working directory
4. `$XDG_CONFIG_HOME/catnip/catnip.conf` (default `~/.config/...`)
5. built-in defaults

```bash
catnip config          # what is in effect right now, and every derived path
```

A file named explicitly that does not exist is a hard error rather than a
fall-through — otherwise a typo in `--config` silently runs a different
account's collection and reports success. An unrecognized key is also a
hard error, for the same reason: a misspelled `CATNIP_EXCLUDE` that
quietly fetches four hundred repositories looks like catnip ignoring your
config.

## Keys

### Who and where

| Key | Default | Notes |
|---|---|---|
| `CATNIP_OWNER` | *(empty)* | Blank means the authenticated `gh` account, resolved at run time. |
| `CATNIP_OWNER_TYPE` | `auto` | `auto` tries the org endpoint, then falls back. Set `user`/`org` to skip the probe. |
| `CATNIP_DATA_DIR` | `~/.local/share/catnip` | Everything catnip writes: `runs/`, `stats/`, `logs/`. |

`auto` has one subtlety worth knowing: for your *own* account catnip
lists through `/user/repos`, not `/users/<you>/repos`, because the latter
omits private repositories even when your token can see them.

### Which repositories

| Key | Default | Notes |
|---|---|---|
| `CATNIP_INCLUDE` | *(empty)* | Space-separated globs against the bare repo name. Empty = everything not excluded. |
| `CATNIP_EXCLUDE` | *(empty)* | Applied after include. |
| `CATNIP_INCLUDE_FORKS` | `false` | Forks rarely have interesting traffic and cost calls. |
| `CATNIP_INCLUDE_ARCHIVED` | `true` | Archived repos still receive clones. |
| `CATNIP_INCLUDE_PRIVATE` | `true` | Private repos have traffic data too. |

Globs match the repo name only (`docs-site`), never `owner/name`.

```ini
CATNIP_INCLUDE=catnip pane run-*
CATNIP_EXCLUDE=*-private scratch
```

Preview the effect without spending a single API call on detail:

```bash
catnip fetch --dry-run
```

Every rejected repository is written to `reports/skipped.tsv` in the run
directory, with the rule that rejected it.

### What to collect per repository

Each of these costs API calls per repo per run; turn them off first if a
large account bumps the hourly limit.

| Key | Default | Cost | What you lose |
|---|---|---|---|
| `CATNIP_FETCH_STATS` | `true` | 6/repo | Code frequency, commit activity, contributor stats views |
| `CATNIP_FETCH_README` | `true` | 1/repo | The `has_readme` column and the archived README text |
| `CATNIP_FETCH_EVENTS` | `true` | 1–2/repo, only when non-zero | Star/fork timestamps, and so the history view's monthly bars |
| `CATNIP_FETCH_ISSUES` | `true` | 1+/repo | Issue counts in the deltas view |

Traffic endpoints are never optional — they are the point.

Rough budget: about 10 API calls per repository per run with everything
on, against a 5,000/hour authenticated limit. A 400-repo account runs
comfortably; a 2,000-repo account should narrow `CATNIP_INCLUDE` or turn
off stats.

### Retention and schedule

| Key | Default | Notes |
|---|---|---|
| `CATNIP_RETAIN_DAYS` | `30` | How long run directories survive `catnip prune`. |
| `CATNIP_SETTLE_HOURS` | `36` | How long after a day closes GitHub is still adding counts to it. No window ends inside this wait. 36 is a measured floor; a lower value is ignored. `catnip settle` measures what this account actually does. |
| `CATNIP_SETTLE_LOG_DAYS` | `90` | How many days of settle readings to keep in `stats/history/daily_snapshots.jsonl`. That log is a measurement, not a record: every day in it is already in the store at its settled value. |
| `CATNIP_TIMER_ONCALENDAR` | `daily` | systemd `OnCalendar=` syntax. |
| `CATNIP_TIMER_RANDOM_DELAY` | `1h` | Jitter, so you are not hitting the API at the same second as everyone else. |

Retention applies to run directories only. The history store is never
pruned, and `catnip prune` will not delete a run whose days are not in it
— see [retention](#retention-safety) below.

## Tokens and access

Traffic endpoints require **push access** to each repository. A token
without it fetches metadata perfectly and returns no traffic at all,
which looks like a working setup with mysteriously empty charts.

```bash
gh auth login --scopes repo        # classic token
gh auth refresh --scopes repo      # if you already authenticated narrowly
```

Fine-grained tokens work too, with repository permissions **Administration:
read** (traffic) and **Metadata: read**. `catnip doctor` probes an actual
traffic endpoint rather than trusting the scope string, because a
fine-grained token reports no scopes at all.

Collecting an organization means holding a token that can push to it.
That is GitHub's requirement, not catnip's.

## Retention safety

`catnip prune` is a dry run unless you pass `--yes`, and it refuses —
regardless of age — to delete any run whose ID is missing from
`stats/history/traffic_daily.json`:

```
  ! keep   20260601T031500Z  (NOT INGESTED — deleting would lose 66d-old traffic days)
    would delete 20260615T031500Z  (52d old, ingested)
```

If you see that warning, run `catnip history` and prune again. GitHub
will not serve those days a second time.

## Multiple accounts

Each account gets its own config file and its own data directory:

```bash
catnip init --path ~/.config/catnip/work.conf --owner my-employer \
            --data-dir ~/.local/share/catnip-work
catnip run --config ~/.config/catnip/work.conf
```

`--config` anywhere on the command line applies to every stage of the
pipeline, not just the subcommand it sits next to. The equivalent
environment form works the same way:

```bash
CATNIP_CONFIG=~/.config/catnip/work.conf catnip run
```

Give the two configs different `CATNIP_DATA_DIR` values. Sharing a data
directory between owners would interleave their runs, and the history
store keys on repo name alone.

For a second timer, install from a checkout with that config active —
`catnip timer install` pins the resolved config path into the unit, so
the two timers never read each other's settings.
