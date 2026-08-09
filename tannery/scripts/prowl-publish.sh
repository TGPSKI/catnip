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
corrections=".state/prowl/${cycle}.corrections.md"
watch=".state/prowl/${cycle}.watch.md"
window_file=".state/prowl/${cycle}.window"

[[ -s "$src" || -s "$corrections" || -s "$refuted" || -s "$watch" ]] || {
  echo "nothing recorded for ${cycle} - nothing to publish" >&2; exit 1; }

data_dir="$(catnip config --json | python3 -c "import json,sys;print(json.load(sys.stdin)['paths']['data_dir'])")"

# Assemble to a staged file. The editor stands between assembly and the
# published prowl.md: the recorder sends this document to the editor queue,
# and the guarded edit-publish is the only writer of the reports dir.
out=".state/prowl/${cycle}.assembled.md"
# catnip owns the naming: reports are keyed on the period they cover, and a
# promoted one sorts before its own superseded stamped siblings.
prev_published="$(catnip report --locate | python3 -c "import json,sys;print(json.load(sys.stdin).get('prowl') or '/dev/null')")"
# The config's CATNIP_OWNER is blank in auto mode; the store records who it
# actually fetched.
owner="$(python3 -c "import json;print(json.load(open('${data_dir}/stats/history/traffic_daily.json')).get('owner') or 'this account')")"

# The recorder appends, so re-recording a cycle files every finding again.
# Dedupe on the claim heading, keeping the first of each, and count what was
# actually published - counting the source would double on a queue retry.
dedupe() {
  awk 'BEGIN{RS="### "} NR==1{next} {split($0,l,"\n"); if(!seen[l[1]]++) printf "### %s", $0}' "$1"
}
findings="$(dedupe "$src")"
corrected="$([[ -s "$corrections" ]] && dedupe "$corrections" || true)"
refutations="$([[ -s "$refuted" ]] && sort -u "$refuted" || true)"
watching="$([[ -s "$watch" ]] && sort -u "$watch" || true)"
window="$([[ -s "$window_file" ]] && head -1 "$window_file" || true)"

# Snapshot the previous cycle's headings once, before this cycle's first
# publish overwrites the previous prowl. An agent's context carries no
# prowl-vs-report lineage, so no agent files corrections: a refutation whose
# hypothesis matches a previous cycle's actual heading IS a correction of
# it, promoted here deterministically.
prev_headings=".state/prowl/${cycle}.previous-headings"
if [[ ! -f "$prev_headings" ]]; then
  { grep '^### ' "$prev_published" 2>/dev/null | sed 's/^### //; s/ *`[a-z]*`$//' || true; } > "$prev_headings"
fi
promoted=""
if [[ -s "$prev_headings" && -n "$refutations" ]]; then
  tagged="$(REFUTATIONS="$refutations" python3 - "$prev_headings" <<'PYEOF'
import os, re, sys
heads = [re.sub(r"\s+", " ", h).strip().lower()
         for h in open(sys.argv[1]) if h.strip()]
for line in os.environ["REFUTATIONS"].splitlines():
    if not line.strip():
        continue
    hyp = re.sub(r"\s+", " ", line[2:].split(" - checked with ")[0]).strip().lower()
    hit = any((hyp in h or h in hyp) for h in heads if len(h) > 20)
    print(("C" if hit else "R") + line)
PYEOF
)"
  promoted="$(printf '%s\n' "$tagged" | grep '^C' | cut -c2- || true)"
  refutations="$(printf '%s\n' "$tagged" | grep '^R' | cut -c2- || true)"
fi

{
  if [[ -n "$window" ]]; then
    echo "# prowl - ${owner}, window ${window}"
  else
    echo "# prowl - ${owner}, cycle ${cycle}"
  fi
  echo
  echo "Companion to report.md. report.md is arithmetic and reproduces byte for"
  echo "byte from the store; this file is inference and does not. Every claim"
  echo "below carries the tier its recording tool required, and the evidence"
  echo "line names the tool call it came from."
  echo
  if [[ -n "$corrected" || -n "$promoted" ]]; then
    echo "## Correction to the previous cycle"
    echo
    [[ -n "$corrected" ]] && printf '%s\n' "$corrected"
    [[ -n "$promoted" ]] && { printf '%s\n' "$promoted"; echo; }
  fi
  if [[ -n "$findings" ]]; then
    echo "## Findings"
    echo
    printf '%s\n' "$findings"
  fi
  if [[ -n "$refutations" ]]; then
    echo "## Refuted"
    echo
    printf '%s\n' "$refutations"
    echo
  fi
  if [[ -n "$watching" ]]; then
    echo "## Watch next"
    echo
    printf '%s\n' "$watching"
    echo
  fi
} > "$out"

m="$(printf '%s\n' "$findings" | grep -c '^### .*`measured`' || true)"
i="$(printf '%s\n' "$findings" | grep -c '^### .*`inferred`' || true)"
s="$(printf '%s\n' "$findings" | grep -c '^### .*`speculative`' || true)"
r="$(printf '%s\n' "$refutations" | grep -c '^- ' || true)"
c="$(( $(printf '%s\n' "$corrected" | grep -c '^### ' || true) + $(printf '%s\n' "$promoted" | grep -c '^- ' || true) ))"
w="$(printf '%s\n' "$watching" | grep -c '^- ' || true)"

# The counts ride inside the document so the editor's publish can tell a
# dropped finding (fidelity failure) from a superseded document (skip).
printf '<!-- counts measured=%s inferred=%s speculative=%s -->\n' "$m" "$i" "$s" >> "$out"

printf 'wrote %s\n' "$out"
printf '  %-12s %s\n' measured "$m" inferred "$i" speculative "$s" refuted "$r"

# The digest of the days this cycle rests on, recorded the same way the
# report records its own. A finding is prose plus a tier, not a recomputable
# query, so this is the only thing that can tell a later check that the
# numbers underneath a published claim have moved. Pinned to the window's
# end day: the trailing window moves on, and comparing against it would fire
# on a new day arriving rather than on an old one being revised.
digest_json="$(catnip report --digest 2>/dev/null || echo '{}')"
read -r window_digest window_end < <(printf '%s' "$digest_json" | python3 -c \
  "import json,sys;d=json.load(sys.stdin);print(d.get('window_digest') or '-', d.get('end') or '-')")

# The writer's state file is one run's outcome; this is the cycle's
# aggregate, written deterministically on every publish.
cat > .state/prowl-cycle.json <<EOF
{
  "cycle": "${cycle}",
  "window": "${window}",
  "window_end": "${window_end}",
  "window_digest": "${window_digest}",
  "findings": {"measured": ${m}, "inferred": ${i}, "speculative": ${s}},
  "corrections": ${c},
  "refuted": ${r},
  "watch": ${w},
  "published": "${out}"
}
EOF

# Per cycle, because prowl-cycle.json holds only the newest and every
# published cycle stays re-openable until its window leaves GitHub's reach.
cat > ".state/prowl/${cycle}.digest" <<EOF
{
  "cycle": "${cycle}",
  "timeframe": "2w",
  "window_end": "${window_end}",
  "window_digest": "${window_digest}",
  "recorded": "$(date -u +%Y%m%dT%H%M%SZ)"
}
EOF
