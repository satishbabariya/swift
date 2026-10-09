# swiftrs: rules for agents

swiftrs is a Rust rewrite of the Swift 3.1 compiler, pure Swift only (no
Objective-C, no Clang importer, no C/C++ in the product). This directory is
self-contained; the rest of the repository is the upstream Swift compiler
and is NOT what you are working on. Never open pull requests against the
upstream Swift repository.

Read first: `docs/DESIGN.md` (decisions; follow them), `docs/AUTOMATION.md`
(how the fleet works), and `docs/specs/<subsystem>.md` for your area.

## Reference material
- `.corpus/swift-3.1/` is the extracted `swift-3.1-RELEASE` tree (run
  `python3 -I tools/corpus/extract.py` if missing): `lib/`, `include/`,
  `stdlib/`, `test/`, `validation-test/`. Read-only.
- `corpus/swift-3.1.tsv` lists every test, in or out of scope, with its stage.

## Commands
- Build: `cargo build --workspace`
- Unit tests: `cargo test --workspace`
- Lit tests: `tools/test/run.py <path>... [-v]`, `--stage sema --sample 50`
- Before committing: `tools/fleet/gate.sh [fleet/tasks/<task>.md]`

## Rules
- Port behaviour from the 3.1 C++, not its structure. Arenas + typed ids,
  no `Rc<RefCell>` graphs, no `unsafe` outside `crates/runtime`.
- Diagnostics must match 3.1 text exactly (tests use `-verify`).
- Never special-case tests (names, paths, expected strings in code).
- Never edit `.corpus/`, `corpus/`, `scoreboard/`, `tools/test/` or tests.
- No new dependencies unless listed in `docs/DESIGN.md` §9.
- No C, C++, assembly, or native-code `build.rs`.
- Keep tool output short: grep and summarize, don't dump logs.
