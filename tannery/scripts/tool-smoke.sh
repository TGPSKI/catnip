#!/usr/bin/env bash
# Exec the read-only tools exactly as shell-mcp would, straight from
# shell-tools.json. Writers are skipped on purpose - see the Makefile.
set -uo pipefail

cfg="$(dirname "$0")/../shell-tools.json"
readonly_tools=(catnip_verify catnip_store_status catnip_settle catnip_report_meta)
templated=(catnip_view:view=audience catnip_derivation:view=audience)
fail=0

run() {
  local name="$1"; shift
  local out rc
  out=$(python3 - "$cfg" "$name" "$@" <<'PY' 2>&1
import json, subprocess, sys
cfg, name = sys.argv[1], sys.argv[2]
subs = dict(a.split("=", 1) for a in sys.argv[3:])
tool = next(t for t in json.load(open(cfg))["tools"] if t["name"] == name)
argv = [tool["command"]] + [subs.get(a[2:-2], a) if a.startswith("{{") else a
                            for a in tool.get("args", [])]
r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
sys.stdout.write((r.stdout or r.stderr)[:400])
sys.exit(r.returncode)
PY
  ); rc=$?
  if [[ $rc -eq 0 ]]; then
    printf '  ok    %-22s %s\n' "$name" "$(head -c 60 <<<"${out//$'\n'/ }")"
  else
    printf '  FAIL  %-22s rc=%s %s\n' "$name" "$rc" "$(head -c 90 <<<"${out//$'\n'/ }")"
    fail=1
  fi
}

echo "read-only tool smoke (writers skipped):"
for t in "${readonly_tools[@]}"; do run "$t"; done
for spec in "${templated[@]}"; do run "${spec%%:*}" "${spec#*:}"; done
[[ $fail -eq 0 ]] && echo "all read-only tools ok" || echo "one or more tools failed"
exit $fail
