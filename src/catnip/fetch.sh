#!/usr/bin/env bash
# Collect one run of GitHub repository + traffic data for the configured
# owner. Everything this script writes lands under a single timestamped
# run directory, so a run is either a complete artifact or an obviously
# partial one — never a half-updated shared directory.
#
#   fetch.sh [--config FILE] [--owner NAME] [--out DIR] [--dry-run]
#
# Reads its configuration through `python3 -m catnip.config --shell` so
# bash and Python cannot disagree about which owner, which data dir, or
# which repo filters are in effect.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="${CATNIP_PYTHON:-python3}"

CONFIG_ARG=()
OWNER_OVERRIDE=""
OUT_OVERRIDE=""
DRY_RUN=false

usage() {
  cat <<'USAGE'
Usage: catnip fetch [options]

Fetch repository metadata and traffic data for the configured owner.

Options:
  --config FILE   Config file to use (default: catnip's search order).
  --owner NAME    GitHub user or org to collect (overrides config).
  -o, --out DIR   Runs directory (default: <data dir>/runs).
  --dry-run       Resolve config and list the repos that would be fetched.
  -h, --help      Show this help.

Environment: any CATNIP_* key overrides the config file. GH_TOKEN, if set,
overrides gh's stored credentials.
USAGE
}

while (($#)); do
  case "$1" in
    --config) [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
              CONFIG_ARG=(--config "$2"); shift 2 ;;
    --owner)  [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
              OWNER_OVERRIDE="$2"; shift 2 ;;
    -o|--out) [[ $# -ge 2 ]] || { echo "Missing value for $1" >&2; exit 2; }
              OUT_OVERRIDE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$OWNER_OVERRIDE" ]] && export CATNIP_OWNER="$OWNER_OVERRIDE"

# ─── config ──────────────────────────────────────────────────────────────────
# One eval, one source of truth. config.py resolves the owner (including
# the `gh api user` lookup) and exits non-zero with a fixable message if
# it cannot, so this script never guesses.
if ! CONFIG_ENV="$("$PYTHON" -m catnip.config "${CONFIG_ARG[@]}" --shell)"; then
  exit 1
fi
eval "$CONFIG_ENV"

ORG_NAME="$CATNIP_OWNER"
RUNS_DIR="${OUT_OVERRIDE:-$CATNIP_PATH_RUNS_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$RUNS_DIR/$STAMP"
START_EPOCH="$(date -u +%s)"

# ─── helpers ──────────────────────────────────────────────────────────────────

# json_valid <file> — non-empty, parseable JSON body.
json_valid() {
  [[ -s "$1" ]] && "$PYTHON" -c "import json,sys; json.load(open(sys.argv[1]))" "$1" 2>/dev/null
}

# rate_limit_wait — on API failure, check the core rate limit; if exhausted,
# sleep until reset (capped) or abort.
rate_limit_wait() {
  local remaining reset now wait
  read -r remaining reset < <(gh api rate_limit \
    --jq '[.resources.core.remaining, .resources.core.reset] | @tsv' 2>/dev/null) || return 0
  if [[ "${remaining:-1}" == "0" ]]; then
    now="$(date -u +%s)"
    wait=$(( reset - now + 5 ))
    if (( wait > 300 )); then
      echo "FATAL: rate limit exhausted; resets in ${wait}s (>300s cap)" >&2
      exit 1
    fi
    (( wait > 0 )) && { echo "  rate limit exhausted — sleeping ${wait}s" >&2; sleep "$wait"; }
  fi
}

# gh_api <endpoint> <outfile>
#   Retries with backoff on transient errors; real rate-limit handling via
#   the rate_limit endpoint; bodies are JSON-validated before mv, so a
#   truncated response never lands under its final name.
gh_api() {
  local endpoint="$1" outfile="$2" retry=5 tmp
  tmp="${outfile}.gh_tmp"

  for ((i=0; i<retry; i++)); do
    if gh api "$endpoint" 2>/dev/null > "$tmp" && json_valid "$tmp"; then
      mv "$tmp" "$outfile"
      return 0
    fi
    rm -f "$tmp"
    rate_limit_wait
    sleep $(( 1 << i ))   # 1s,2s,4s,8s,16s
  done
  rm -f "$tmp"
  echo "  gh: $endpoint → failed" >&2
  return 1
}

# gh_api_paged <endpoint> <outfile> [accept-header]
#   Page through an array endpoint (per_page=100), merging pages as JSON
#   arrays in Python until a short page. Never raw-concatenates pages —
#   that produces corrupt "...}][{" bodies that only fail at analyze time.
gh_api_paged() {
  local endpoint="$1" outfile="$2" accept="${3:-}" page=1 sep tmp args
  tmp="${outfile}.gh_tmp_page"
  echo '[]' > "$outfile"
  [[ "$endpoint" == *\?* ]] && sep='&' || sep='?'
  while :; do
    args=(api "${endpoint}${sep}per_page=100&page=${page}")
    [[ -n "$accept" ]] && args+=(-H "Accept: $accept")
    if ! gh "${args[@]}" 2>/dev/null > "$tmp" || ! json_valid "$tmp"; then
      rm -f "$tmp"
      rate_limit_wait
      if ! gh "${args[@]}" 2>/dev/null > "$tmp" || ! json_valid "$tmp"; then
        rm -f "$tmp"
        return 1
      fi
    fi
    local count
    count="$("$PYTHON" - "$outfile" "$tmp" <<'MERGE'
import json, sys
a = json.load(open(sys.argv[1]))
b = json.load(open(sys.argv[2]))
if not isinstance(b, list):
    b = []
json.dump(a + b, open(sys.argv[1], "w"))
print(len(b))
MERGE
)"
    rm -f "$tmp"
    (( count < 100 )) && return 0
    page=$(( page + 1 ))
    (( page > 20 )) && return 0   # safety cap: 2000 items
  done
}

# stats_warmup <name> — fire one un-retried request at each of a repo's three
#   stats/* endpoints and discard the body. GitHub computes these
#   asynchronously and returns HTTP 202 with an empty body on the first
#   request; a single hit triggers that background computation. Run for every
#   repo up front so that by the time the collection pass reads them (minutes
#   later) the data is ready. This is why "code frequency is empty" tends to
#   fix itself on the second run, and why collection is a separate pass.
stats_warmup() {
  local name="$1" ep
  for ep in commit_activity code_frequency contributors; do
    gh api "repos/$ORG_NAME/$name/stats/$ep" >/dev/null 2>&1 || :
  done
}

# gh_api_stats <endpoint> <outfile>
#   Collect a stats/* endpoint in the final pass, long after stats_warmup
#   triggered its computation. SINGLE SHOT on purpose: the endpoints still
#   empty after the whole detail loop belong to forks / archived / empty
#   repos that GitHub never computes stats for, and retrying those costs
#   minutes per run for zero additional data. On empty, write a valid `{}`
#   so downstream JSON parsing never meets a 0-byte body.
gh_api_stats() {
  local endpoint="$1" outfile="$2" tmp body
  tmp="${outfile}.gh_tmp_stats"
  if gh api "$endpoint" 2>/dev/null > "$tmp" && json_valid "$tmp"; then
    body="$(tr -d '[:space:]' < "$tmp")"
    if [[ -n "$body" && "$body" != "{}" && "$body" != "[]" && "$body" != "null" ]]; then
      mv "$tmp" "$outfile"
      return 0
    fi
  fi
  if [[ -s "$tmp" ]] && json_valid "$tmp"; then
    mv "$tmp" "$outfile"
  else
    echo '{}' > "$outfile"
  fi
  rm -f "$tmp"
  return 0
}

is_true() { [[ "${1,,}" =~ ^(1|true|yes|on)$ ]]; }

# ─── auth ────────────────────────────────────────────────────────────────────

echo "catnip fetch — owner: $ORG_NAME" >&2
if ! gh auth status >/dev/null 2>&1; then
  echo "ERROR: gh is not authenticated. Run: gh auth login --scopes repo" >&2
  exit 1
fi

rate_before="$(gh api rate_limit --jq '.resources.core.remaining' 2>/dev/null || echo unknown)"
echo "Rate limit: $rate_before remaining" >&2

# ─── 1. repo list ────────────────────────────────────────────────────────────

mkdir -p "$OUT_DIR"/raw "$OUT_DIR"/reports

echo "Listing repositories… (1/4)" >&2
count_json() { "$PYTHON" -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$1"; }

list_ok=false
case "$CATNIP_OWNER_TYPE" in
  org)  gh_api_paged "/orgs/$ORG_NAME/repos?type=owner" "$OUT_DIR/raw/repos_listed.json" && list_ok=true ;;
  user) gh_api_paged "/users/$ORG_NAME/repos?type=owner" "$OUT_DIR/raw/repos_listed.json" && list_ok=true ;;
  auto)
    # /orgs 404s for a personal account and /users returns an empty array
    # for an org, so "non-empty result" is the only reliable signal.
    if gh_api_paged "/orgs/$ORG_NAME/repos?type=owner" "$OUT_DIR/raw/repos_listed.json" \
       && [[ "$(count_json "$OUT_DIR/raw/repos_listed.json")" != "0" ]]; then
      list_ok=true
    elif [[ "$ORG_NAME" == "$(gh api user --jq .login 2>/dev/null)" ]]; then
      # For your own account this must be /user/repos, not /users/<you>/repos:
      # the latter omits private repositories even with a token that can see them.
      gh_api_paged "/user/repos?affiliation=owner" "$OUT_DIR/raw/repos_listed.json" && list_ok=true
    else
      gh_api_paged "/users/$ORG_NAME/repos?type=owner" "$OUT_DIR/raw/repos_listed.json" && list_ok=true
    fi ;;
  *) echo "FATAL: CATNIP_OWNER_TYPE must be auto, user, or org (got '$CATNIP_OWNER_TYPE')" >&2; exit 2 ;;
esac
$list_ok || { echo "FATAL: could not list repositories for '$ORG_NAME'." >&2; exit 1; }

total_listed="$(count_json "$OUT_DIR/raw/repos_listed.json")"
if [[ "$total_listed" == "0" ]]; then
  echo "FATAL: '$ORG_NAME' has no repositories visible to this token." >&2
  echo "  If they are private, re-auth with: gh auth login --scopes repo" >&2
  exit 1
fi

# raw/repos_listed.json is everything the API returned; raw/org_repos.json is
# what survived the filters and is the list every later stage iterates.
"$PYTHON" -m catnip.config "${CONFIG_ARG[@]}" --select "$OUT_DIR/raw/repos_listed.json" \
  --rejects "$OUT_DIR/reports/skipped.tsv" \
  --selected-json "$OUT_DIR/raw/org_repos.json" > "$OUT_DIR/.repos.tsv"
selected="$(wc -l < "$OUT_DIR/.repos.tsv" | tr -d ' ')"
skipped="$(wc -l < "$OUT_DIR/reports/skipped.tsv" | tr -d ' ')"
echo "  $total_listed listed, $selected selected, $skipped skipped by filters" >&2

if [[ "$selected" == "0" ]]; then
  echo "FATAL: every repository was filtered out. See $OUT_DIR/reports/skipped.tsv" >&2
  echo "  Check CATNIP_INCLUDE / CATNIP_EXCLUDE / CATNIP_INCLUDE_FORKS." >&2
  exit 1
fi

if $DRY_RUN; then
  echo "--- would fetch ($selected) ---"
  cut -f1 "$OUT_DIR/.repos.tsv"
  echo "--- skipped ($skipped) ---"
  cat "$OUT_DIR/reports/skipped.tsv"
  rm -rf "$OUT_DIR"
  exit 0
fi

# ─── 2. stats warm-up ────────────────────────────────────────────────────────

if is_true "$CATNIP_FETCH_STATS"; then
  echo "Warming stats endpoints… (2/4)" >&2
  while IFS=$'\t' read -r name slug; do
    [[ -z "${name:-}" ]] && continue
    stats_warmup "$name"
  done < "$OUT_DIR/.repos.tsv"
fi

# ─── 3. per-repo detail ──────────────────────────────────────────────────────

echo "Fetching per-repo detail… (3/4)" >&2
traffic_denied=0
while IFS=$'\t' read -r name slug; do
  [[ -z "${name:-}" ]] && continue
  printf '  %s' "$slug" >&2

  gh_api "repos/$ORG_NAME/$name" "$OUT_DIR/raw/repo_${slug}.json" || {
    echo " — metadata unavailable, skipped" >&2; continue
  }
  gh_api "repos/$ORG_NAME/$name/languages" "$OUT_DIR/raw/repo_${slug}_langs.json" 2>/dev/null || :

  gh_api_paged "repos/$ORG_NAME/$name/contributors" "$OUT_DIR/raw/repo_${slug}_contributors.json" 2>/dev/null \
    || echo '[]' > "$OUT_DIR/raw/repo_${slug}_contributors.json"
  gh_api_paged "repos/$ORG_NAME/$name/pulls?state=all" "$OUT_DIR/raw/repo_${slug}_pulls.json" 2>/dev/null \
    || echo '[]' > "$OUT_DIR/raw/repo_${slug}_pulls.json"
  gh_api_paged "repos/$ORG_NAME/$name/releases" "$OUT_DIR/raw/repo_${slug}_releases.json" 2>/dev/null || :
  if is_true "$CATNIP_FETCH_ISSUES"; then
    gh_api_paged "repos/$ORG_NAME/$name/issues?state=all" "$OUT_DIR/raw/repo_${slug}_issues.json" 2>/dev/null || :
  fi

  if is_true "$CATNIP_FETCH_README"; then
    gh_api "repos/$ORG_NAME/$name/readme" "$OUT_DIR/raw/repo_${slug}_readme.json" 2>/dev/null || :
  fi

  # Traffic — the reason catnip exists. These endpoints require push access
  # to the repository; for a repo you do not own they 403, and the run
  # silently loses its most valuable series. Count the misses and report
  # them at the end rather than leaving an empty chart to explain.
  if gh_api "repos/$ORG_NAME/$name/traffic/clones" "$OUT_DIR/raw/repo_${slug}_clones.json" 2>/dev/null; then
    gh_api "repos/$ORG_NAME/$name/traffic/views" "$OUT_DIR/raw/repo_${slug}_views.json" 2>/dev/null || :
    gh_api "repos/$ORG_NAME/$name/traffic/popular/paths?per_page=100" "$OUT_DIR/raw/repo_${slug}_paths.json" 2>/dev/null || :
    gh_api "repos/$ORG_NAME/$name/traffic/popular/referrers" "$OUT_DIR/raw/repo_${slug}_referrers.json" 2>/dev/null || :
  else
    traffic_denied=$(( traffic_denied + 1 ))
    printf '%s\ttraffic endpoints denied (push access required)\n' "$name" \
      >> "$OUT_DIR/reports/traffic-denied.tsv"
  fi

  # Star/fork event timestamps — only worth a call when the count is nonzero.
  if is_true "$CATNIP_FETCH_EVENTS"; then
    read -r stars_now forks_now < <("$PYTHON" -c "
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get('stargazers_count', 0), d.get('forks_count', 0))
" "$OUT_DIR/raw/repo_${slug}.json" 2>/dev/null || echo "0 0")
    if [[ "${stars_now:-0}" != "0" ]]; then
      gh_api_paged "repos/$ORG_NAME/$name/stargazers" "$OUT_DIR/raw/repo_${slug}_stargazers.json" \
        "application/vnd.github.star+json" 2>/dev/null || :
    fi
    if [[ "${forks_now:-0}" != "0" ]]; then
      gh_api_paged "repos/$ORG_NAME/$name/forks?sort=newest" "$OUT_DIR/raw/repo_${slug}_forks.json" 2>/dev/null || :
    fi
  fi

  # Decode the base64 README body. analyze.py only needs its existence, but
  # the text is what makes a run self-contained for later reading.
  if [[ -f "$OUT_DIR/raw/repo_${slug}_readme.json" ]]; then
    "$PYTHON" -c "
import base64, json, pathlib, sys
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
content = json.loads(src.read_text()).get('content', '')
if content:
    dst.write_text(base64.b64decode(content).decode('utf-8', 'replace'))
" "$OUT_DIR/raw/repo_${slug}_readme.json" "$OUT_DIR/raw/repo_${slug}_readme.md" 2>/dev/null || :
  fi

  echo " ✓" >&2
done < "$OUT_DIR/.repos.tsv"

# ─── 4. collect stats ────────────────────────────────────────────────────────
# Now that the whole detail loop has elapsed since warm-up, GitHub has had
# minutes to compute each repo's stats. Collecting them in one final pass
# gives even the first-warmed repo maximal compute time.

if is_true "$CATNIP_FETCH_STATS"; then
  echo "Collecting stats endpoints… (4/4)" >&2
  while IFS=$'\t' read -r name slug; do
    [[ -z "${name:-}" ]] && continue
    gh_api_stats "repos/$ORG_NAME/$name/stats/commit_activity" "$OUT_DIR/raw/repo_${slug}_commits.json" 2>/dev/null || :
    gh_api_stats "repos/$ORG_NAME/$name/stats/code_frequency"  "$OUT_DIR/raw/repo_${slug}_freq.json" 2>/dev/null || :
    gh_api_stats "repos/$ORG_NAME/$name/stats/contributors"    "$OUT_DIR/raw/repo_${slug}_contstats.json" 2>/dev/null || :
  done < "$OUT_DIR/.repos.tsv"
fi

rm -f "$OUT_DIR/.repos.tsv"

rate_after="$(gh api rate_limit --jq '.resources.core.remaining' 2>/dev/null || echo unknown)"
end_epoch="$(date -u +%s)"
meta_count="$("$PYTHON" -c "
import pathlib, re, sys
raw = pathlib.Path(sys.argv[1])
# repo_<slug>.json only — the per-endpoint files carry a _suffix.
print(sum(1 for p in raw.glob('repo_*.json')
          if not re.search(r'_(langs|contributors|pulls|releases|issues|readme|clones|views|paths|referrers|stargazers|forks|commits|freq|contstats)\.json$', p.name)))
" "$OUT_DIR/raw")"

# A run manifest turns "what did this run actually do?" from an archaeology
# exercise into one file read — the first thing catnip's triage skill asks for.
CATNIP_MF_STAMP="$STAMP" CATNIP_MF_OWNER="$ORG_NAME" \
CATNIP_MF_LISTED="$total_listed" CATNIP_MF_SELECTED="$selected" \
CATNIP_MF_SKIPPED="$skipped" CATNIP_MF_FETCHED="$meta_count" \
CATNIP_MF_DENIED="$traffic_denied" CATNIP_MF_BEFORE="$rate_before" \
CATNIP_MF_AFTER="$rate_after" CATNIP_MF_DURATION="$(( end_epoch - START_EPOCH ))" \
"$PYTHON" - "$OUT_DIR/manifest.json" <<'MANIFEST'
import json, os, sys
env = os.environ.get
json.dump({
    "schema_version": 1,
    "run_id": env("CATNIP_MF_STAMP"),
    "owner": env("CATNIP_MF_OWNER"),
    "owner_type": env("CATNIP_OWNER_TYPE"),
    "config_file": env("CATNIP_PATH_CONFIG_FILE", ""),
    "repos_listed": int(env("CATNIP_MF_LISTED", "0")),
    "repos_selected": int(env("CATNIP_MF_SELECTED", "0")),
    "repos_skipped": int(env("CATNIP_MF_SKIPPED", "0")),
    "repos_fetched": int(env("CATNIP_MF_FETCHED", "0")),
    "traffic_denied": int(env("CATNIP_MF_DENIED", "0")),
    "rate_remaining_before": env("CATNIP_MF_BEFORE"),
    "rate_remaining_after": env("CATNIP_MF_AFTER"),
    "duration_seconds": int(env("CATNIP_MF_DURATION", "0")),
    "fetched": {
        "stats": env("CATNIP_FETCH_STATS"),
        "readme": env("CATNIP_FETCH_README"),
        "events": env("CATNIP_FETCH_EVENTS"),
        "issues": env("CATNIP_FETCH_ISSUES"),
    },
}, open(sys.argv[1], "w"), indent=2)
MANIFEST

echo "Done in $(( end_epoch - START_EPOCH ))s — $meta_count repos → $OUT_DIR" >&2
if (( traffic_denied > 0 )); then
  echo "  NOTE: $traffic_denied repo(s) denied traffic data (push access required)." >&2
  echo "        See $OUT_DIR/reports/traffic-denied.tsv" >&2
fi
echo "$OUT_DIR"
