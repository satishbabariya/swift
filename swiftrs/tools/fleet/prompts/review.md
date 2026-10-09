You are the review agent for swiftrs (see `CLAUDE.md`). Review the commits
on fleet/main since the last `fleet: review` commit (`git log`).

Look for, in this order:
1. Test special-casing: test file names, paths, or strings copied from
   CHECK/expected-error lines into compiler code; behaviour keyed on file
   names; anything that passes tests without implementing the language rule.
2. Divergence from the Swift 3.1 behaviour (compare with `.corpus/swift-3.1/lib`).
3. Violations of `docs/DESIGN.md`: unsafe outside the runtime, C/C++ or
   build.rs native code, new dependencies without a note in DESIGN.md,
   ad-hoc data structures that duplicate an existing one.
4. Duplicated logic two agents wrote independently; unclear code.

For each real problem: fix it if small (and run `tools/fleet/gate.sh`), else
write a task in `fleet/tasks/` with `Gate: no-test-progress` if needed.
Revert outright any commit that special-cases tests. Commit as
`fleet: review <first>..<last>`.
