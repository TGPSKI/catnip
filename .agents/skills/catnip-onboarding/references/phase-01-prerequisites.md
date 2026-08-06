---
name: phase-01-prerequisites
description: "Tooling and credentials: python, curses, gh, and a token that can actually read traffic data."
parent: catnip-onboarding
---

# Phase 1: Prerequisites and Credentials

Produces: a machine where `catnip doctor` reaches `traffic access: PASS`.

Nothing later in onboarding can succeed without that. A token that lists
repositories but cannot read `/traffic/clones` yields runs that complete
successfully and contain no traffic — the failure catnip is most often
asked to explain.

## Step 1: Tooling

**Inspect**

```bash
catnip doctor --offline
```

This checks the interpreter, curses, `gh`, and authentication without
making any GitHub calls beyond auth status.

| Status | Action |
|---|---|
| `python` FAIL | Python is older than 3.10. Install a newer one; do not proceed. |
| `curses` WARN | The pipeline works; only `catnip tui` needs it. Note it and continue. On Debian/Ubuntu: `apt install python3-curses`. |
| `gh cli` FAIL | Install the GitHub CLI: https://cli.github.com — package manager or the project's own instructions. Then re-run. |
| All PASS | Continue to Step 2. |

**Do not** install anything with `pip`. catnip is stdlib-only by design;
if something appears to need a package, that is a bug worth reporting,
not a dependency to add.

## Step 2: Authentication

**Inspect**

```bash
gh auth status
```

| Status | Action |
|---|---|
| Not logged in | Have the **user** run `gh auth login --scopes repo` — it opens a browser and you cannot complete it for them. |
| Logged in | Continue; the scope check in Step 3 decides whether it is sufficient. |

**Decide** — if they are logging in fresh, tell them why the scope is
what it is before they pick options:

> GitHub only returns traffic data to someone with push access to the
> repository, and for a classic token that means the `repo` scope. It is
> broader than reading stars — it is what the traffic endpoints require,
> and catnip cannot narrow it. If you would rather scope tightly, a
> fine-grained token with **Administration: read** and **Metadata: read**
> on the repositories you care about is the narrower alternative.

Nothing catnip writes ever contains the token; `gh` owns the credential
and catnip shells out to it.

## Step 3: Prove traffic is readable

**Inspect**

```bash
catnip doctor
```

Read three checks together — they are the ones that catch a
misconfiguration that would otherwise surface as an empty chart days
later.

| Check | FAIL/WARN means | Action |
|---|---|---|
| `token scopes` WARN, no `repo` | Classic token too narrow | `gh auth refresh --scopes repo` |
| `token scopes` WARN, "not reported" | Fine-grained token — scopes are invisible by design | Not a problem in itself; the `traffic access` probe below is the real test |
| `authenticated as` vs `configured owner` differ | The token belongs to a different account than the one being collected | Expected when collecting an org you administer. Otherwise, a typo — fix in Phase 2 |
| `traffic access` FAIL | The probe hit `/traffic/clones` on a real repo and was denied | Stop here. Nothing downstream can work |

**When `traffic access` fails**, work through these in order:

| Cause | How to tell | Fix |
|---|---|---|
| Token lacks `repo` | `token scopes` shows only `public_repo` or similar | `gh auth refresh --scopes repo` |
| Fine-grained token missing a permission | Scopes "not reported" and the probe 403s | Add **Administration: read** to the token's repository permissions |
| No push access to the probed repo | The user does not own or administer it | Collect an owner they administer instead |
| SSO not authorized for an org | GitHub's error mentions SAML or SSO | Authorize the token for that organization in GitHub's settings |

**Verify**

```bash
catnip doctor --offline && catnip doctor | grep "traffic access"
```

Phase 1 is complete when that line reads `✓ traffic access` with a real
clone count beside it.

## Checkpoint

Nothing was written to disk in this phase — it changed credentials, not
files. Confirm with the user:

- which account they are authenticated as (`catnip doctor` prints it);
- that traffic is readable, with the repo the probe used as evidence.

**Next phase**: @phase-02-configuration.md — decide what to collect and
write it down.
