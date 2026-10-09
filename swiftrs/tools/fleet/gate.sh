#!/usr/bin/env bash
# The gate every change must pass before it lands on fleet/main.
#
#   tools/fleet/gate.sh [--quick] [fleet/tasks/<id>.md]
#
#  1. cargo fmt --check, clippy -D warnings (skipped with --quick), unit tests
#  2. the task's own tests: at least one must pass, unless the task file says
#     "Gate: no-test-progress" (infrastructure and refactoring tasks)
#  3. the ratchet: a random sample of already-passing tests must still pass
#     (1000 tests, or 200 with --quick; a different sample on every run, so
#     the fleet as a whole covers the scoreboard many times a day)
#
# The nightly scorekeeper runs the full suite on fleet/main and is the only
# thing that adds tests to the scoreboard.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

QUICK=""
if [ "${1:-}" = "--quick" ]; then QUICK=1; shift; fi
TASK="${1:-}"
step() { echo "== gate: $*"; }

step fmt
cargo fmt --all --check
if [ -z "$QUICK" ]; then
  step clippy
  cargo clippy --workspace --all-targets --quiet -- -D warnings
fi
step build
cargo build --workspace --quiet
step unit tests
if ! out="$(cargo test --workspace --quiet 2>&1)"; then
  echo "$out" | tail -40
  exit 1
fi
echo "$out" | grep -cE '^test result: ok' | xargs -I{} echo "{} test binaries ok"

if [ -n "$TASK" ]; then
  tests="$(sed -n '/^## Tests/,/^## /s/^- \(\S*\).*/\1/p' "$TASK")"
  if [ -n "$tests" ] && ! grep -q '^Gate: no-test-progress' "$TASK"; then
    step task tests
    # shellcheck disable=SC2086
    python3 tools/test/run.py $tests --output .lit/task.json || true
    python3 - .lit/task.json <<'EOF'
import json, sys
tests = json.load(open(sys.argv[1])).get("tests", [])
passed = sum(t["code"] in ("PASS", "XFAIL") for t in tests)
print(f"task tests: {passed}/{len(tests)} passing")
sys.exit(0 if passed else 1)
EOF
  fi
fi

step ratchet
if [ -s scoreboard/swift-3.1.json ] && python3 tools/test/run.py --scoreboard --list >/dev/null 2>&1; then
  python3 tools/test/run.py --scoreboard --sample "$([ -n "$QUICK" ] && echo 200 || echo 1000)" \
    --seed "$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')" --output .lit/ratchet.json || true
  python3 tools/fleet/ratchet.py check .lit/ratchet.json
else
  echo "scoreboard empty; nothing to protect yet"
fi
step passed
