# swiftrs

A Rust implementation of the **Swift 3.1** compiler and runtime, for pure
Swift (no Objective-C or C interop), with no C or C++ in the product, for
every platform LLVM targets. Built largely by parallel Claude Code agents
against the original 3.1 test suite.

- [docs/DESIGN.md](docs/DESIGN.md): scope, decisions, architecture, milestones
- [docs/AUTOMATION.md](docs/AUTOMATION.md): how the agent fleet runs
- [corpus/swift-3.1.summary.md](corpus/swift-3.1.summary.md): the test corpus

## Status

Milestone M0 (harness) is in place. The first compiler component, the
Swift 3.1 lexer, is in `crates/lexer`.

## Quick start

```sh
cd swiftrs
python3 -m venv .venv && .venv/bin/pip install -r tools/test/requirements.txt
python3 -I tools/corpus/extract.py      # extract + classify the 3.1 tests
cargo build --workspace && cargo test --workspace
tools/test/run.py --stage sema --sample 20
tools/fleet/ratchet.py status
```

Requirements: Rust stable, Python 3.9+, LLVM 17+ (`llc`, `ld.lld`) for code
generation, and Docker for the reference Swift 3.1 compiler
(`--param compiler=reference`, `tools/reference/goldens.py`).

This directory lives in a fork of the Swift repository only so the 3.1
sources are reachable through git history (`swift-3.1-RELEASE`).
