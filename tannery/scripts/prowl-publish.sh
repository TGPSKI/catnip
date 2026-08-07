#!/usr/bin/env bash
# Assemble one prowl cycle's recorded findings into prowl.md, beside the
# deterministic report.md it companions.
#
# Kept separate from report.md on purpose: one file is reproducible from the
# store and one is not. A reader must be able to diff consecutive report.md
# without an agent's prose moving underneath them.
set -euo pipefail

cycle="${1:?usage: prowl-publish.sh YYYY-MM-DD}"
src=".state/prowl/${cycle}.md"
refuted=".state/prowl/${cycle}.refuted.md"

[[ -s "$src" ]] || { echo "no findings recorded for ${cycle} - nothing to publish" >&2; exit 1; }

data_dir="$(catnip config --json | python3 -c "import json,sys;print(json.load(sys.stdin)['paths']['data_dir'])")"
latest="$(ls -1d "${data_dir}"/reports/*/ 2>/dev/null | sort | tail -1)"
[[ -n "$latest" ]] || { echo "no report directory to publish beside - run catnip report first" >&2; exit 1; }

out="${latest}prowl.md"
owner="$(catnip config --json | python3 -c "import json,sys;print(json.load(sys.stdin).get('CATNIP_OWNER') or 'this account')")"

# The recorders append, so re-running a cycle files every finding again.
# Dedupe on the claim heading, keeping the first of each.
dedupe() {
  awk 'BEGIN{RS="### "} NR==1{next} {split($0,l,"\n"); if(!seen[l[1]]++) printf "### %s", $0}' "$1"
}

{
  echo "# prowl - ${owner}, cycle ${cycle}"
  echo
  echo "Companion to report.md. report.md is arithmetic and reproduces byte for"
  echo "byte from the store; this file is inference and does not. Every claim"
  echo "below carries the tier its recording tool required, and the evidence"
  echo "line names the tool call it came from."
  echo
  echo "## Findings"
  echo
  dedupe "$src"
  if [[ -s "$refuted" ]]; then
    echo "## Refuted"
    echo
    sort -u "$refuted"
    echo
  fi
} > "$out"

printf 'wrote %s\n' "$out"
for tier in measured inferred speculative; do
  printf '  %-12s %s\n' "$tier" "$(grep -c "\`${tier}\`" "$src" || true)"
done
printf '  %-12s %s\n' "refuted" "$([[ -s "$refuted" ]] && grep -c '^- ' "$refuted" || echo 0)"
