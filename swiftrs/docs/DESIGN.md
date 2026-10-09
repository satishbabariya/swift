# swiftrs design

swiftrs is a from-scratch Rust implementation of the **Swift 3.1** compiler,
runtime support and toolchain, restricted to **pure Swift**: no Objective-C
interop, no Clang importer, and no C or C++ anywhere in the shipped product.
It targets every platform LLVM can generate code for, with Linux, macOS and
Windows on x86_64 and arm64 as Tier 1.

The work is done mostly by fleets of Claude Code agents (see
[AUTOMATION.md](AUTOMATION.md)), so this document is written as **decisions
with reasons**. Agents follow it; changing it is a human decision.

## 1. Scope

| | In | Out |
|---|---|---|
| Language | Swift 3.1 exactly (`swift-3.1-RELEASE`), `-swift-version 3` | Swift 4+ features, `-swift-version 4` |
| Interop | none needed: Swift talks to the OS only through the runtime | `@objc`, `dynamic`, bridging, `import` of any C/ObjC module, `-import-objc-header` |
| Tools | `swiftc` driver, `swift -frontend`, `sil-opt`, runtime, stdlib, demangler | SourceKit, swift-ide-test, completion, migrator, REPL, JIT/`-interpret`, LLDB support, api-digester |
| Platforms | everything LLVM targets, tiered (§7) | |

Measured from the 3.1 tag (`corpus/swift-3.1.summary.md` has the current numbers):

- `lib/` is 458K lines of C++. Without ClangImporter, IDE, Migrator,
  PrintAsObjC and the ObjC paths in Sema/SILGen/IRGen, roughly 300K lines
  carry behaviour we must reproduce.
- Runtime and stubs: 19K lines of C++/ObjC++, ~330 exported entry points;
  the compiler calls 173 of them (`include/swift/Runtime/RuntimeFunctions.def`).
- Standard library: 55K lines of Swift + gyb; it imports C only through
  `SwiftShims` (121 symbols) and uses ICU for Unicode on Linux.
- Tests: 8,898 lit tests; 7,236 in scope (1,959 core + 5,277 fixed fuzzer
  crashers that must not crash).

## 2. Decisions

**D1. Source of truth is Swift 3.1 on Linux.** Linux 3.1 never had ObjC
interop, so its behaviour, diagnostics, SIL and ABI are exactly the "pure
Swift" dialect we want. It is our reference compiler (Docker image
`swift:3.1`) and its test suite is our specification. Where 3.1 on Linux has
a bug we keep the bug unless a test of ours documents the deviation in
`docs/deviations.md`.

**D2. Rust, stable toolchain, no native code.** No C/C++ sources, no
`build.rs` that compiles native code, no `-sys` crates that link C libraries
into the product. `unsafe` is denied workspace-wide; only `swiftrs-runtime`
may allow it, module by module, with a `// SAFETY:` comment per block.
Dependencies are allowed when pure Rust, widely used, and listed in §9.

**D3. Backend: emit textual LLVM IR, run external `llc` and `lld`.** The
compiler binary contains no LLVM. It writes `.ll` and invokes `llc` (object
code) and `ld.lld` / `ld64.lld` / `lld-link` (linking) from a configured LLVM
installation (minimum LLVM 17, tested on 18). This gives every LLVM target,
keeps C++ out of our binary, and makes IR trivially diffable.
Consequences:
- We emit opaque-pointer IR (`ptr`), which 3.1 (LLVM 3.9 era, typed
  pointers) never did. IRGen FileCheck tests are adapted mechanically by
  `tools/test/adapt-ir-checks.py` (to be written in M5; typed pointer
  patterns -> `ptr`), never by hand, and adapted tests are tagged in the manifest.
- `swiftcc`, `swiftself`, `swifterror` and Swift's spare-bit enum layout are
  available in upstream LLVM for all targets we care about.
- Optimizations at `-O` come from our SIL optimizer plus `llc -O2`; we do not
  run `opt` passes by default (3.1 ran its own LLVM pass pipeline; ported
  later as an `opt` invocation if benchmarks need it).
- Cranelift (pure Rust) is a possible later fast `-Onone` backend; it cannot
  be the only backend because it lacks most targets and Swift's conventions.

**D4. Runtime in Rust.** `swiftrs-runtime` re-implements the 3.1 runtime ABI
(the symbol names, signatures and data layouts the compiler emits calls to):
allocation and refcounting, metadata instantiation and caches, value witness
tables, dynamic casts, protocol conformance lookup, existentials, errors,
`Mirror` support, the demangler, and the stdlib stubs (number parsing and
printing, hashing, I/O, Unicode). Built as a `staticlib` (default) and a
`cdylib`, with `panic = "abort"`, on Rust `std` (no_std is a later option for
embedded targets). Unicode normalization, grapheme breaking and collation
that 3.1 delegated to ICU are implemented with pure-Rust crates.
It talks to the OS through Rust `std` only, never through C sources.

**D5. One ABI everywhere: the 3.1 non-ObjC ABI.** All platforms use the
Linux 3.1 object model, including macOS and Windows:
- `HeapObject` = metadata pointer + 32-bit strong count + 32-bit weak count.
- Class metadata without the ObjC `isa`/superclass/cache/data fields.
- Swift 3 mangling (`_T` prefix), 3.1 value witness tables and nominal type
  descriptors, metadata records in linker sections (§7 for the per-object-
  format details).
This means we are never ABI-compatible with Apple's toolchain or Apple's
`libswiftCore`, which is fine: the stdlib is ours too.

**D6. The standard library is the 3.1 stdlib minus C.** We take
`stdlib/public/core` from the tag and replace `import SwiftShims` with
`RuntimeShims.swift`, a file of `@_silgen_name` declarations bound to the
Rust runtime. Overlays (Glibc, Darwin, Foundation, Dispatch, XCTest, ...)
do not exist. Programs reach the OS only through the stdlib (`print`,
`readLine`, `CommandLine`, `exit` via `_swift_stdlib_exit`). A later
`SwiftrsSystem` module may expose file and process APIs, written in Swift on
top of runtime entry points.

**D7. Same command-line interface as 3.1.** `swiftrs` behaves as `swiftc`
and `swiftrs-frontend` as `swift -frontend` for every in-scope flag, with the
same diagnostics text and `-verify` semantics, so the 3.1 lit tests run
unmodified (D1). Diagnostics are ported from the 3.1 `.def` files with their
ids and text.

**D8. Textual SIL is a stable, tested boundary.** The SIL printer and parser
are first-class and match 3.1's textual SIL. That lets us replace the
compiler in two halves with a working end-to-end compiler at every step
(milestones M3-M5): our frontend emits SIL the reference backend accepts, and
our backend accepts SIL the reference frontend emits.

**D9. Own module format.** Serialized modules (`.swiftmodule`) use a
swiftrs-specific, versioned binary format, not LLVM bitstream. Nothing needs
to read Apple's format. The 32 tests that run `llvm-bcanalyzer` on modules are
out of scope.

**D10. Test harness is Python, product is Rust.** lit, FileCheck (LLVM's if
present, else the `filecheck` Python port), gyb and line-directive are test
and build tooling and may stay Python. Nothing Python ships.

## 3. Compiler architecture

The pipeline mirrors 3.1 so the C++ is a usable reference, but the data
model is Rust-native:

```
swiftrs (driver) ─► swiftrs-frontend
   Lexer ─► Parser ─► AST ─► Sema (name binding, type checking) ─► SILGen
   ─► SIL mandatory passes (DI, diagnostics, mandatory inlining, ...)
   ─► [SIL performance passes at -O] ─► IRGen ─► .ll ─► llc ─► .o ─► lld
                                     └─► Serialization (.swiftmodule)
```

Crates (`crates/`): `basic` (source manager, diagnostics, interning, arenas),
`lexer`, `parse`, `ast`, `sema`, `silgen`, `sil` (SIL data structures,
printer, parser, verifier), `sil-opt` (passes and the `swiftrs-sil-opt`
binary), `irgen`, `serialization`, `driver`, `frontend` (binaries),
`demangle`, `runtime`.

Data-model rules:
- **Arenas and typed indices, not pointers.** Each compilation has an
  `AstContext` owning arenas for decls, exprs, stmts, patterns and types;
  nodes refer to each other with `Copy` ids (`DeclId(u32)`, `TypeId(u32)`).
  No `Rc<RefCell<..>>` graphs. Side tables (`IndexVec<ExprId, TypeId>`)
  hold results computed later (types, conversions, resolved overloads).
- **Types are interned** (structural uniquing, like 3.1's canonical types),
  so type equality is id equality.
- **Phases are explicit, as in 3.1** (parse all, then bind imports, then
  type check top-level decls, then function bodies), with lazy computation
  of decl signatures through memoized functions on `AstContext` where 3.1
  computes lazily. No general query system until a test needs one.
- **SIL** has its own arenas: functions, blocks, instructions and values as
  ids, with use-lists kept in side tables. The verifier runs after every
  pass in debug builds.
- **Diagnostics** go through one `DiagnosticEngine`; ids and messages come
  from the 3.1 `Diagnostics*.def` files, vendored into the repository and
  turned into Rust by a `build.rs` that only reads text (allowed by D2: no
  native code).
- **Errors don't panic.** User errors are diagnostics. A panic is a compiler
  bug; the fuzzer-crasher tests exist to catch them.

The **constraint solver** (3.1 `CSGen`/`CSSimplify`/`CSSolver`/`CSApply`) is
the hardest part of the port and gets a dedicated human-reviewed spec
(`docs/specs/sema-constraints.md`) before the fleet implements it.

## 4. Runtime and stdlib architecture

- `swiftrs-runtime` exports the 3.1 symbol names. C-convention entry points
  are `#[unsafe(no_mangle)] extern "C"` functions. Rust has no `swiftcc`, so
  the 49 `SWIFT_CC(swift)` entry points get small generated LLVM IR thunks
  (`swiftcc` -> C convention) compiled with `llc` as part of the runtime build.
- Metadata records are found through linker sections (§7); the runtime
  enumerates them at first use (3.1 `ImageInspection*.cpp` behaviour).
- Thread safety follows 3.1: metadata caches are concurrent maps with
  once-initialization; refcounting uses atomics with 3.1's memory orderings.
- Unicode: `unicode-normalization`, `unicode-segmentation` crates replace
  ICU (3.1 `UnicodeNormalization.cpp`, `UnicodeExtendedGraphemeClusters`).
- Assembly helpers (3.1 `swift_sections.S`) become marker objects generated
  as LLVM IR by our toolchain (§7).

## 5. Testing

Three oracles, in order of authority:

1. **The 3.1 lit suite**, unmodified, through `tools/test/lit.cfg.py`
   (`tools/test/run.py`). In-scope tests are listed in
   `corpus/swift-3.1.tsv`, generated by `tools/corpus/extract.py`.
2. **Differential goldens** from the reference compiler
   (`tools/reference/goldens.py`): `-dump-parse`, `-dump-ast`,
   `-emit-silgen`, `-emit-sil`, `-emit-ir` and program output, compared after
   normalization. This catches what tests don't FileCheck.
3. **Generated programs** (milestone M5+): a random Swift 3.1 program
   generator, compiled by both compilers, outputs compared. Also feeds the
   "must not crash" fuzzing.

Plus a held-out set: 10% of in-scope tests (deterministic by path hash, see
`is_held_out` in `tools/test/run.py`) are never selected for agents or turned
into tasks; only the scorekeeper's `--all` run includes them. A pass rate on
held-out tests well below the rest means agents are overfitting.

Rules: the scoreboard only grows (`tools/fleet/ratchet.py`); a test is never
edited to pass; any adapted test is produced by a script.

## 6. Milestones

Each milestone has a measurable exit. Counts are from the manifest.

| | Milestone | Exit criterion |
|---|---|---|
| M0 | Harness | corpus manifest, lit config, runner, ratchet, fleet scripts, reference goldens on Linux |
| M1 | Rust runtime vs reference compiler | 3.1 stdlib built by reference `swiftc`, linked with `swiftrs-runtime`, passes the 401 `exec` tests the C++ runtime passes |
| M2 | C-free stdlib | no SwiftShims, no ICU; M1 still holds. Gives a **pure reference toolchain** used as the oracle from here on |
| M3 | Frontend: lexer, parser, Sema, SILGen | 488 sema + 172 silgen core tests, 5,277 fuzz tests don't crash; our SIL accepted by the reference backend for all exec tests |
| M4 | Mandatory SIL pipeline (`-Onone`) | DI, diagnostics and mandatory-pass `sil` tests; exec tests pass through reference IRGen |
| M5 | IRGen + `llc`/`lld` | all 401 exec tests with **no reference component**; adapted irgen tests |
| M6 | Serialization, driver, multi-module | 89 serialization + 80 driver tests; stdlib built as a module |
| M7 | Self-hosted stdlib, ports | swiftrs builds its own stdlib; Tier 1 platforms green |
| M8 | Performance SIL optimizer (`-O`) | `sil` optimizer tests; benchmark suite within 2x of 3.1 |

M1-M2 (runtime, stdlib) and M3 (frontend) are independent and run as two
parallel workstreams from the start.

## 7. Platforms

Tier 1 (CI on every landing): `x86_64` and `aarch64`/`arm64` on Linux
(ELF), macOS (Mach-O, without ObjC) and Windows (COFF, MSVC ABI).
Tier 2 (nightly): i686, armv7, riscv64, ppc64le, s390x Linux; wasm32-wasi.
Tier 3: anything else LLVM and Rust `std` both support.

Platform-specific work, owned by IRGen + runtime:
- **ELF**: sections `.swift2_protocol_conformances` and
  `.swift2_type_metadata`, bracketed by begin/end marker objects (3.1's
  `swift_sections.S`, which our toolchain generates as LLVM IR) and found at
  runtime with `dl_iterate_phdr`.
- **Mach-O**: `__TEXT,__swift2_proto` and `__TEXT,__swift2_types`, found with
  `_dyld_register_func_for_add_image` + `getsectiondata` (libSystem symbols
  declared in Rust; that's the OS ABI, not C source).
- **COFF**: `.sw2prtc` and `.sw2tymd`, found by walking each loaded module's
  PE section table. No PC-relative relocations across DLLs, so cross-module
  relative references go through indirect symbols; `dllimport`/`dllexport`
  on runtime and stdlib symbols.
- **32-bit**: `Int` is 32 bits, different spare-bit masks for enum layout,
  and 3.1's "two words of header" FIXME on 32-bit heap objects is kept.
- **wasm32**: no spare bits in pointers, and `swiftcc` signatures must match
  exactly at call sites (thunks for mismatched function types).

3.1 had no unwinding (errors return through `swifterror`), so no per-platform
unwinder work is needed.

## 8. What 3.1 behaviour we deliberately drop

- Everything under `#if _runtime(_ObjC)` in the stdlib, all `@objc`/`dynamic`
  codegen, bridging conversions in Sema, `AnyObject` dynamic lookup.
- ClangImporter, module maps, `-import-objc-header`, `-Xcc`.
- REPL and `-interpret` (deferred, not dropped: an LLVM-free interpreter of
  SIL is a possible later milestone).

## 9. Approved dependencies

Compiler: none yet beyond `std`. Candidates when needed: `rustc-hash`,
`indexmap`, `smallvec`, `bumpalo` (arenas), `memchr`.
Runtime: `unicode-normalization`, `unicode-segmentation`.
Adding any other crate needs a line here with the reason.
