You are writing a subsystem spec for swiftrs (see `CLAUDE.md`). Subsystem:
{{SUBSYSTEM}}. Source: `.corpus/swift-3.1/lib/{{SUBSYSTEM}}/` and the matching
headers under `.corpus/swift-3.1/include/swift/`.

Write `docs/specs/{{subsystem}}.md` for agents who will implement it in Rust
without reading all of the C++. Cover:
- Responsibilities and the exact inputs/outputs (which AST/SIL/IR in and out).
- Every data structure that crosses the subsystem boundary and its invariants.
- The algorithms, in prose and pseudo-code, including ordering and the
  non-obvious edge cases the C++ handles (cite file:function for each).
- Diagnostics it emits (ids from the .def files) and when.
- Which parts are Objective-C/Clang-only and are therefore out of scope.
- A suggested Rust module layout consistent with `docs/DESIGN.md`, and an
  implementation order where each step makes some lit tests pass (name them).
Be precise and complete; do not paraphrase loosely. Commit the spec alone.
