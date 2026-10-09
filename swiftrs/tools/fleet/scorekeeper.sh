#!/usr/bin/env bash
# Nightly (or after every N landings): run the whole in-scope suite on
# fleet/main, grow the scoreboard, and publish progress.
#
#   tools/fleet/scorekeeper.sh
#
# This is the only writer of scoreboard/*.json, so workers never conflict on
# it. If the full run shows a regression the per-change sampled ratchet
# missed, it fails loudly and a human (or a fix-regression task) handles it.
set -euo pipefail
REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
WT="$REPO/swiftrs/.fleet/worktrees/scorekeeper"
HUB="refs/heads/fleet/main"

if [ ! -d "$WT" ]; then
  git -C "$REPO" worktree add -q --detach "$WT" "$HUB"
  ln -s "$REPO/swiftrs/.corpus" "$WT/swiftrs/.corpus"
  ln -s "$REPO/swiftrs/.venv" "$WT/swiftrs/.venv"
fi
cd "$WT/swiftrs"
git checkout -q --detach "$HUB"
base="$(git rev-parse HEAD)"

cargo build --workspace --release --quiet
python3 tools/test/run.py --all --param "swiftrs_bin=$PWD/target/release" --output .lit/full.json || true
python3 tools/fleet/ratchet.py update .lit/full.json
python3 tools/fleet/ratchet.py status | tee .lit/status.txt

if ! git diff --quiet -- scoreboard; then
  git add scoreboard
  git commit -q -m "fleet: scoreboard $(date -u +%F) — $(tail -1 .lit/status.txt | awk '{print $2"/"$3}') passing"
  git update-ref "$HUB" HEAD "$base" || echo "fleet/main moved during the run; scoreboard lands next time"
fi
