# Running the swiftrs agent fleet

swiftrs is built mostly by parallel, headless Claude Code agents. Humans own
the design ([DESIGN.md](DESIGN.md)), the harness (`tools/test`,
`tools/corpus`), the scoreboard rules and code review of anything
architectural. Agents own the volume: porting behaviour test by test.

The setup follows what has worked for large agent-built compilers: the test
harness is the product manager, coordination is plain git, and one rule (the
scoreboard never shrinks) keeps many agents from undoing each other's work.

## Moving parts

```
fleet/tasks/<id>.md   work items, sorted by priority (id prefix = pipeline stage)
fleet/locks/<id>      claimed by a worker; committed, so claims are atomic
fleet/done/<id>       landed
fleet/notes/<id>.md   what each attempt learned; survives rejected attempts
scoreboard/*.json     every test that has ever passed (only the scorekeeper writes it)

refs/heads/fleet/main integration branch; advanced only by compare-and-swap
.fleet/worktrees/<w>  one git worktree per worker (gitignored)
```

| Script | Role |
|---|---|
| `tools/corpus/extract.py` | extract the 3.1 sources/tests from the tag, classify scope, install lit shims |
| `tools/test/run.py` | run a selection of lit tests (stage/tag/path/sample/shard), terse summary |
| `tools/fleet/gate.sh` | fmt, clippy, unit tests, the task's tests, sampled ratchet |
| `tools/fleet/ratchet.py` | `check` (no regressions), `update` (grow scoreboard), `status` |
| `tools/fleet/mktasks.py` | draft tasks from failing tests (refined by the triage agent) |
| `tools/fleet/worker.sh` | the worker loop: claim, run Claude, gate, land |
| `tools/fleet/scorekeeper.sh` | full run on fleet/main, scoreboard update |
| `tools/reference/` | reference Swift 3.1 in Docker; golden outputs |

## The worker loop

`tools/fleet/worker.sh <id>`:

1. **Claim.** Take the first task (by id) with no lock and not done: commit
   `fleet/locks/<task>` on top of `fleet/main` and advance `fleet/main` with
   `git update-ref <new> <old>`. If another worker moved the ref first, the
   CAS fails and the worker tries the next task. No server, no races.
2. **Work.** `claude -p` with `tools/fleet/prompts/implement.md`, the task
   file and earlier notes, in the worker's own worktree, permissions
   bypassed, `--max-turns` capped. The agent must commit its work and its
   notes.
3. **Gate.** `tools/fleet/gate.sh fleet/tasks/<task>.md`: at least one task
   test newly passes, no sampled scoreboard test regresses, lint and unit
   tests clean.
4. **Land.** Rebase onto `fleet/main`; if the base moved, re-run the quick
   gate on the rebased result; CAS the ref. On conflict the work is dropped,
   the notes are kept, and the task goes back in the queue.

Run workers **only in isolated containers or VMs** (permissions are
bypassed). Each container needs: Rust stable, LLVM 17+ (`llc`, `ld.lld`),
Python 3 with `pip install -r tools/test/requirements.txt`, Claude Code, and
`ANTHROPIC_API_KEY`. Nothing else: no GitHub credentials. A human (or a cron
job with its own credentials) pushes `fleet/main` to GitHub.

## Roles

| Role | How often | Prompt |
|---|---|---|
| Implementer | N parallel workers, continuously | `prompts/implement.md` |
| Spec writer | once per subsystem, before its tasks open | `prompts/spec.md` |
| Triage | after each scorekeeper run | `prompts/triage.md` |
| Reviewer | every ~20 landings | `prompts/review.md` |
| Scorekeeper | nightly / every ~50 landings | `scorekeeper.sh` (no agent) |

Humans: approve specs for the constraint solver, SIL data structures,
IRGen type layout and the runtime ABI before the fleet starts on them; read
review output; own `docs/DESIGN.md` and the harness.

## Lessons the setup encodes

- **The harness is the spec.** Agents optimize what the tests measure.
  Everything else (held-out tests, goldens, reviewer, generated programs)
  exists to measure what tests miss.
- **Terse, deterministic output.** Agents waste context on noise. `run.py`
  prints a one-screen table; `-v` is for single tests. Sampling is seeded.
- **Work queue by pipeline order.** A sema task is useless while the parser
  can't read the test. Task ids sort by stage.
- **Small tasks, written notes.** An agent's context ends; its notes don't.
  Failed attempts are cheap if the next one starts where they stopped.
- **Interfaces are human-designed.** Two agents inventing two type
  representations is the expensive failure. Core data structures land first,
  by hand or under close review, then the fleet fills them in.
- **Never edit tests to pass.** Adaptations are scripted and reviewed.

## Starting it

```sh
cd swiftrs
python3 -m venv .venv && .venv/bin/pip install -r tools/test/requirements.txt
python3 -I tools/corpus/extract.py            # corpus + manifest + lit shims
cargo build --workspace
tools/test/run.py --all --output .lit/full.json
tools/fleet/ratchet.py update .lit/full.json  # first scoreboard
tools/fleet/mktasks.py .lit/full.json --stage parse --stage sema
git add fleet scoreboard && git commit -m "fleet: initial tasks and scoreboard"
git branch -f fleet/main HEAD                 # the integration branch
for i in $(seq 1 8); do tools/fleet/worker.sh w$i & done
```

## Budget model

Order-of-magnitude only; measure and update after the first week.
The port is roughly 300K lines of C++ behaviour plus 19K lines of runtime.
Expect on the order of 150-250K lines of Rust. Cost scales with *attempts*,
not lines: every task is read, implemented, gated and often retried. Track
three numbers from the logs and adjust worker count to them:
landed tasks per day, API spend per landed task, and new passing tests per
day by stage. Stop and redesign (don't add workers) when tests-per-day
flattens for a stage: that means the remaining failures share a cause the
current architecture or task split doesn't let agents fix.
