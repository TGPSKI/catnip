# Security Policy

## Reporting a vulnerability

Report privately through [GitHub Security
Advisories](https://github.com/TGPSKI/catnip/security/advisories/new).
Do not open a public issue for a vulnerability.

Include what an attacker gains, the commands or configuration that
trigger it, and the platform. If a proof of concept touches real
repositories, describe it rather than attaching output — GitHub logins
and private repository names are exactly the data catnip is meant to keep
local.

| Stage | Target |
|---|---|
| Acknowledgement | 3 business days |
| Initial assessment | 10 business days |
| Fix or documented mitigation | 30 days for high severity |

## What catnip is, for threat-modeling purposes

A local command-line tool. It reads a config file, shells out to `gh`,
writes JSON and CSV under a data directory, renders a terminal UI, and
optionally installs a systemd **user** unit that runs itself on a timer.
It exposes no network service, opens no ports, and sends data nowhere:
the only outbound traffic is `gh` talking to the GitHub API on your
behalf.

## In scope

- **Credential exposure.** catnip never reads, stores, or logs a token —
  it delegates entirely to `gh`, which owns the credential. A path that
  causes a token to reach disk, a log, a run manifest, or `doctor`
  output is a vulnerability.
- **Command injection.** `fetch.sh` `eval`s the output of
  `python3 -m catnip.config --shell`, which is `shlex.quote`d for
  exactly this reason. Any way to make config values, repository names,
  or API responses execute as shell is in scope. Repository names in
  particular are attacker-influenceable if you administer an org that
  accepts outside contributions.
- **Path traversal.** Repository names become filenames via `slug_for`.
  A name that escapes the run directory, or that collides with another
  repo's files, is in scope.
- **Privilege and persistence.** The systemd units are user units by
  design. Anything that causes catnip to install a system-level unit, run
  as another user, or execute a path an unprivileged user can rewrite is
  in scope.
- **Unsafe defaults.** A default that transmits data off the machine, or
  that widens the token scopes catnip asks for beyond what traffic
  collection requires, is in scope.
- **Destructive retention bugs.** `catnip prune` deleting a run whose
  data exists nowhere else is treated as a security-severity defect: the
  loss is silent and permanent.

## Out of scope

- Data you chose to collect being readable in your own data directory.
  Runs are stored unencrypted under `CATNIP_DATA_DIR` with your user's
  permissions. If you collect private repositories, that directory is as
  sensitive as they are — back it up and share it accordingly.
- GitHub API behavior, rate limits, or the accuracy of GitHub's own
  traffic figures.
- Vulnerabilities in `gh`, Python, systemd, or your terminal emulator.
  Report those upstream.
- The `repo` scope being broad. It is what GitHub requires for
  `/traffic/*`; catnip cannot narrow it. Fine-grained tokens with
  Administration: read and Metadata: read are the narrower alternative,
  and are supported.

## Known limitations

- **Traffic requires push access.** Collecting an organization means
  holding a token that can push to its repositories. That is GitHub's
  requirement, not catnip's choice; scope the token to the org you
  actually administer.
- **`--config` runs arbitrary configuration.** A config file is trusted
  input, like a shell rc file. Do not point catnip at one you did not
  write.
- **The vendored drawing layer** in `src/catnip/tui/` is byte-identical
  to [pane](https://github.com/TGPSKI/pane). Issues there are fixed
  upstream and re-vendored; report them here if you find them through
  catnip and we will route them.

## Supported versions

The latest release on `main`. There are no long-term support branches
yet; fixes ship forward.
