# -*- Python -*-
# lit configuration for running the Swift 3.1 test suite against swiftrs.
#
# tools/corpus/extract.py installs a two-line lit.cfg shim into
# .corpus/<name>/{test,validation-test}/ that sets test_source_root and loads
# this file, so the 3.1 tests run unmodified.
#
# Parameters (lit --param k=v), all optional:
#   compiler=swiftrs|reference  which toolchain to test (default swiftrs)
#   swiftrs_bin=DIR             swiftrs binaries (default <swiftrs>/target/debug)
#   reference_bin=DIR           Swift 3.1 toolchain usr/bin for compiler=reference
#                               (default: tools/reference/bin Docker wrappers)
#   target_run=CMD              prefix for running built executables (default:
#                               none; <reference_bin>/run for the Docker reference)
#   triple=TRIPLE               target triple (default: host)
#   llvm_bin=DIR                LLVM tools (llc, lld, llvm-objdump, ...);
#                               default: directory of `llc` on PATH
#   filecheck=PATH              FileCheck binary (default: LLVM FileCheck if on
#                               PATH, else the `filecheck` Python port)

import os
import platform
import shutil

import lit.formats
import lit.util

SWIFTRS = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPO = os.path.dirname(SWIFTRS)
TEST_BIN = os.path.join(SWIFTRS, "tools", "test", "bin")
params = lit_config.params

suite = os.path.basename(config.test_source_root)
corpus_root = os.path.dirname(config.test_source_root)
config.name = suite
# The 3.1 RUN lines were written for bash (lit's external shell).
try:
    config.test_format = lit.formats.ShTest(execute_external=True, force_execute_external=True)
except TypeError:  # lit < 23
    config.test_format = lit.formats.ShTest(execute_external=True)
config.suffixes = [".swift", ".ll", ".sil", ".gyb", ".test-sh"]
config.excludes = ["Inputs", "Unit"]
# run.py points these at the invoking checkout, so fleet worktrees sharing one
# extracted corpus never share build products or test outputs.
lit_root = params.get("lit_root", os.path.join(SWIFTRS, ".lit"))
config.test_exec_root = os.path.join(lit_root, os.path.basename(corpus_root), suite)

# ---------------------------------------------------------------------------
# Host / target

def host_triple():
    machine = platform.machine().lower()
    cpu = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = platform.system()
    if system == "Darwin":
        return ("arm64" if cpu == "aarch64" else cpu) + "-apple-macosx10.9"
    if system == "Windows":
        return cpu + "-unknown-windows-msvc"
    return cpu + "-unknown-linux-gnu"

triple = params.get("triple", host_triple())
cpu = triple.split("-")[0]
if "linux" in triple:
    run_os = "linux-gnu"
    object_format, dylib_ext = "elf", "so"
elif "apple" in triple:
    run_os = "macosx"
    object_format, dylib_ext = "macho", "dylib"
elif "windows" in triple:
    run_os = "windows-msvc"
    object_format, dylib_ext = "coff", "dll"
else:
    run_os = triple.split("-")[2]
    object_format, dylib_ext = "elf", "so"
ptrsize = "32" if cpu in ("i386", "i686", "armv7", "armv7k", "arm", "wasm32") else "64"
lit_config.note(f"swiftrs: testing {suite} for {triple}")

# ---------------------------------------------------------------------------
# Toolchain under test

compiler = params.get("compiler", "swiftrs")
if compiler == "swiftrs":
    bin_dir = params.get("swiftrs_bin", os.path.join(SWIFTRS, "target", "debug"))
    swift = os.path.join(bin_dir, "swiftrs-frontend")      # `swift -frontend`
    swiftc = os.path.join(bin_dir, "swiftrs")               # `swiftc` driver
    sil_opt = os.path.join(bin_dir, "swiftrs-sil-opt")
    frontend = swift  # already the frontend; no -frontend flag needed
    frontend_flag = ""
    config.available_features.add("swiftrs")
elif compiler == "reference":
    bin_dir = params.get("reference_bin")
    if not bin_dir:
        # Default: the Docker wrappers (tools/reference/bin).
        bin_dir = os.path.join(SWIFTRS, "tools", "reference", "bin")
    swift = os.path.join(bin_dir, "swift")
    swiftc = os.path.join(bin_dir, "swiftc")
    sil_opt = os.path.join(bin_dir, "sil-opt")
    frontend = swift
    frontend_flag = "-frontend"
    config.available_features.add("reference_compiler")
else:
    lit_config.fatal(f"unknown compiler={compiler}")

llc = shutil.which("llc")
llvm_bin = params.get("llvm_bin") or (os.path.dirname(os.path.realpath(llc)) if llc else "")

filecheck = (params.get("filecheck") or shutil.which("FileCheck")
             or (llvm_bin and shutil.which("FileCheck", path=llvm_bin))
             or shutil.which("filecheck")
             or shutil.which("filecheck", path=os.path.join(SWIFTRS, ".venv", "bin")))
if not filecheck:
    lit_config.fatal("no FileCheck found; `pip install filecheck` or install LLVM FileCheck")

# Harness tooling (Python; not part of the product). gyb comes from this
# repository's main branch because the 3.1 copy is Python 2.
gyb = os.path.join(REPO, "utils", "gyb")
line_directive = os.path.join(REPO, "utils", "line-directive")

# ---------------------------------------------------------------------------
# Features. Mirrors the 3.1 Linux configuration in -Onone executable mode,
# without objc_interop, swift_repl or swift_interpreter.

for f in (
    "asserts", "swift_stdlib_asserts", "executable_test", "nonexecutable_test",
    "swift_test_mode_optimize_none", "swift_test_mode_optimize_none_" + cpu,
    "CPU=" + cpu, "OS=" + run_os, "PTRSIZE=" + ptrsize, "SWIFT_VERSION=3",
    "crash-recovery",
):
    config.available_features.add(f)
if cpu in ("i386", "x86_64"):
    config.available_features.add("CPU=i386_or_x86_64")
for target in ("X86", "ARM", "AArch64", "PowerPC", "SystemZ"):
    config.available_features.add("CODEGENERATOR=" + target)

# ---------------------------------------------------------------------------
# Substitutions. Order matters: lit applies them in order, so longer names
# precede their prefixes (as in the 3.1 lit.cfg).

target_frontend = f"{frontend} {frontend_flag} -target {triple}".replace("  ", " ")
target_build = f"{swiftc} -target {triple} -Onone"
default_run = os.path.join(bin_dir, "run") if compiler == "reference" else ""
target_run = params.get("target_run", default_run if os.path.exists(default_run) else "")
run_simple = (
    f"rm -rf %t && mkdir -p %t && {target_build} %s -o %t/a.out -module-name main && "
    f"{target_run} %t/a.out"
)
run_stdlib = (
    f"rm -rf %t && mkdir -p %t && {target_build} %s -o %t/a.out -module-name main "
    f"-Xfrontend -disable-access-control && {target_run} %t/a.out"
)
run_simple_gyb = (
    f"rm -rf %t && mkdir -p %t && {gyb} %s -o %t/main.swift && "
    f"{line_directive} %t/main.swift -- {target_build} %t/main.swift -o %t/a.out -module-name main && "
    f"{line_directive} %t/main.swift -- {target_run} %t/a.out"
)
sanitizing_filecheck = (
    f"{os.path.join(REPO, 'utils', 'PathSanitizingFileCheck')} "
    f"--sanitize 'SOURCE_DIR={corpus_root}' --sanitize 'BUILD_DIR={config.test_exec_root}' "
    f"--use-filecheck '{filecheck}'"
)

subs = [
    (r"%target-swift-frontend\(mock-sdk:([^)]+)\)", target_frontend + r" \1"),
    ("%target-swift-frontend", target_frontend),
    ("%target-typecheck-verify-swift",
     f"{target_frontend} -typecheck -verify -disable-objc-attr-requires-foundation-module %s"),
    ("%target-run-simple-swiftgyb", run_simple_gyb),
    ("%target-run-simple-swift", run_simple),
    ("%target-run-stdlib-swift", run_stdlib),
    ("%target-run", target_run),
    ("%target-build-swift", target_build),
    ("%target-swiftc_driver", f"{swiftc} -target {triple}"),
    ("%target-sil-opt", f"{sil_opt} -target {triple}"),
    ("%target-triple", triple),
    ("%target-cpu", cpu),
    ("%target-os", run_os),
    ("%target-ptrsize", ptrsize),
    ("%target-object-format", object_format),
    ("%target-dylib-extension", dylib_ext),
    ("%target-runtime", "native"),
    ("%target-swiftmodule-name", cpu + ".swiftmodule"),
    ("%target-swiftdoc-name", cpu + ".swiftdoc"),
    ("%swiftc_driver_plain", swiftc),
    ("%swift_driver_plain", swift),
    ("%swiftc_driver", f"env SDKROOT= {swiftc}"),
    ("%swift_driver", f"env SDKROOT= {swift}"),
    ("%sil-opt", sil_opt),
    ("%raw-FileCheck", filecheck),
    ("%FileCheck", sanitizing_filecheck),
    (r"%hardlink-or-copy\(from: *(.*), *to: *(.*)\)", r"ln \1 \2 || cp \1 \2"),
    ("%llvm-dwarfdump", os.path.join(llvm_bin, "llvm-dwarfdump")),
    ("%llvm-link", os.path.join(llvm_bin, "llvm-link")),
    ("%line-directive", line_directive),
    ("%gyb", gyb),
    ("%utils", os.path.join(REPO, "utils")),
    # Must come after everything starting with "%swift".
    ("%swift", f"{frontend} {frontend_flag} -target {triple} -disable-objc-attr-requires-foundation-module".replace("  ", " ")),
]
config.substitutions.extend(subs)

config.environment["PATH"] = os.pathsep.join([TEST_BIN, bin_dir, llvm_bin, config.environment.get("PATH", os.environ.get("PATH", ""))])
for var in ("HOME", "TMPDIR", "SWIFTRS_LOG"):
    if var in os.environ:
        config.environment[var] = os.environ[var]
