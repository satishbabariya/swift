#!/usr/bin/env bash
# swiftrs fleet worker: claim a task, let a headless Claude Code session work
# on it, gate the result, and land it on the shared integration branch.
#
#   tools/fleet/worker.sh <worker-id> [--once]
#
# Coordination is plain git. All workers are worktrees of one repository and
# share its refs; refs/heads/fleet/main is the integration branch and is only
# ever advanced with a compare-and-swap (`git update-ref NEW OLD`), so two
# workers can never both claim a task or both land on the same base.
#
#   swiftrs/fleet/tasks/<id>.md   work items (tools/fleet/mktasks.py, triage agent)
#   swiftrs/fleet/locks/<id>      claimed by a worker (committed to fleet/main)
#   swiftrs/fleet/notes/<id>.md   what previous attempts learned
#
# The agent runs with permissions bypassed. Run workers ONLY inside an
# isolated container or VM with no credentials beyond what the task needs.
set -euo pipefail

ID="${1:?usage: worker.sh <worker-id> [--once]}"
ONCE="${2:-}"
REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
HUB="refs/heads/fleet/main"
WT="$REPO/swiftrs/.fleet/worktrees/$ID"
LOGS="$REPO/swiftrs/.fleet/logs/$ID"
CLAUDE="${CLAUDE:-claude}"
MAX_TURNS="${SWIFTRS_MAX_TURNS:-150}"
mkdir -p "$LOGS"

log() { echo "[$(date -u +%H:%M:%S) $ID] $*" | tee -a "$LOGS/worker.log"; }

if ! git -C "$REPO" rev-parse -q --verify "$HUB" >/dev/null; then
  echo "no $HUB; create it with: git branch fleet/main <start-commit>" >&2
  exit 1
fi
if [ ! -d "$WT" ]; then
  git -C "$REPO" worktree add -q --detach "$WT" "$HUB"
  # Share the extracted corpus and the harness venv with the main checkout.
  ln -s "$REPO/swiftrs/.corpus" "$WT/swiftrs/.corpus"
  ln -s "$REPO/swiftrs/.venv" "$WT/swiftrs/.venv"
fi
cd "$WT/swiftrs"

# Lands HEAD on $HUB with a CAS, rebasing when the hub has moved. With
# --gate, a rebased result must pass the quick gate again before landing.
land_head() {
  local gate="${1:-}" tries=0 base
  while [ $tries -lt 8 ]; do
    base="$(git rev-parse "$HUB")"
    if ! git merge-base --is-ancestor "$base" HEAD; then
      git rebase -q "$base" || { git rebase --abort; return 1; }
      if [ -n "$gate" ]; then
        tools/fleet/gate.sh --quick >> "$LOGS/gate.log" 2>&1 || return 1
      fi
    fi
    git update-ref "$HUB" HEAD "$base" 2>/dev/null && return 0
    tries=$((tries + 1)); sleep $((RANDOM % 5 + 1))
  done
  return 1
}

# Commits staged bookkeeping (locks, notes) and lands it; no gate needed.
land() {
  git commit -q -m "$1" && land_head
}

claim() {
  local task
  git checkout -q --detach "$HUB"
  git reset -q --hard "$HUB"
  for task in $(ls fleet/tasks/ 2>/dev/null | sed -n 's/\.md$//p' | sort); do
    [ -e "fleet/locks/$task" ] && continue
    [ -e "fleet/done/$task" ] && continue
    mkdir -p fleet/locks
    printf '%s %s\n' "$ID" "$(date -u +%FT%TZ)" > "fleet/locks/$task"
    git add "fleet/locks/$task"
    # CAS without rebase: if the hub moved, someone may have claimed it too.
    git commit -q -m "fleet: $ID claims $task"
    if git update-ref "$HUB" HEAD "$(git rev-parse HEAD~1)" 2>/dev/null; then
      echo "$task"; return 0
    fi
    git reset -q --hard "$HUB"
  done
  return 1
}

release() {  # release <task> <done|retry> <message>
  local task="$1" outcome="$2" msg="$3"
  git rm -q -f "fleet/locks/$task" 2>/dev/null || true
  if [ "$outcome" = done ]; then
    mkdir -p fleet/done && echo "$ID $(date -u +%FT%TZ)" > "fleet/done/$task" && git add "fleet/done/$task"
  fi
  land "fleet: $ID $msg $task" || log "WARN: could not release $task"
}

while true; do
  if ! task="$(claim)"; then
    log "no unclaimed tasks"; [ -n "$ONCE" ] && exit 0; sleep 300; continue
  fi
  start="$(git rev-parse HEAD)"
  log "claimed $task"

  prompt="$(cat "$REPO/swiftrs/tools/fleet/prompts/implement.md")

## Your task: $task

$(cat "fleet/tasks/$task.md")

## Notes from earlier attempts
$(cat "fleet/notes/$task.md" 2>/dev/null || echo "(none)")"

  set +e
  "$CLAUDE" -p "$prompt" --permission-mode bypassPermissions --max-turns "$MAX_TURNS" \
    --output-format stream-json --verbose > "$LOGS/$task.$(date +%s).jsonl" 2>&1
  set -e

  # The agent must leave its work committed; anything uncommitted is dropped.
  git reset -q --hard
  git clean -qfd

  if [ "$(git rev-parse HEAD)" = "$start" ]; then
    log "no commits for $task"
    release "$task" retry "gave up on"
  elif tools/fleet/gate.sh "fleet/tasks/$task.md" >> "$LOGS/gate.log" 2>&1; then
    git rm -q -f "fleet/locks/$task"
    mkdir -p fleet/done && echo "$ID $(date -u +%FT%TZ)" > "fleet/done/$task"
    git add "fleet/done/$task"
    git commit -q -m "fleet: $ID completes $task"
    if land_head --gate; then
      log "landed $task"
    else
      log "could not land $task (conflict or post-rebase gate failure)"
      git reset -q --hard "$HUB"
      mkdir -p fleet/notes
      echo "- $(date -u +%FT%TZ) $ID: work passed the gate but conflicted with fleet/main; redo on the new base." >> "fleet/notes/$task.md"
      git add "fleet/notes/$task.md"
      release "$task" retry "failed to land"
    fi
  else
    log "gate failed for $task"
    # Keep the agent's notes (it writes fleet/notes/<task>.md) but drop code.
    notes="$(git show HEAD:swiftrs/fleet/notes/$task.md 2>/dev/null || true)"
    git reset -q --hard "$HUB"
    if [ -n "$notes" ]; then
      mkdir -p fleet/notes && printf '%s\n' "$notes" > "fleet/notes/$task.md" && git add "fleet/notes/$task.md"
    fi
    release "$task" retry "gate failed on"
  fi
  [ -n "$ONCE" ] && exit 0
done
