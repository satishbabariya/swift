You are the triage agent for swiftrs (see `CLAUDE.md`). Input: the latest
full run `.lit/full.json` and the draft tasks in `fleet/tasks/`.

1. For unclaimed tasks (no `fleet/locks/<id>`), sample the failures and find
   shared root causes (same missing feature, same diagnostic, same crash).
2. Rewrite tasks so each one is a single root cause: merge tasks that share
   a cause, split tasks that mix causes, and give each a precise title, a
   "Likely cause" paragraph with pointers into the 3.1 C++ and our crates, and
   its test list under `## Tests`.
3. Order matters: task ids sort by priority. Put blockers (things many tests
   need, e.g. a missing statement kind) first by renaming to a lower id.
4. Infrastructure or refactoring work without a direct test gain gets
   `Gate: no-test-progress` on its own line.
5. Do not touch locked tasks. Commit with message `fleet: triage`.
