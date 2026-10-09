#!/usr/bin/env python3
"""Extract and classify the Swift 3.1 test corpus.

Materializes the reference sources from a git tag of this repository into
`swiftrs/.corpus/<name>/` (gitignored) and writes a committed manifest that
classifies every lit test as in or out of scope for swiftrs:

  * Swift 3.1 language, pure Swift: no Objective-C interop, no Clang importer,
    no C/C++ in the product.
  * Tools in scope: the compiler (frontend + driver), sil-opt, the runtime and
    the standard library. IDE tooling (SourceKit, swift-ide-test, completion,
    api-digester, ...) is out of scope.

Each in-scope test is also tagged with the deepest pipeline stage it
exercises, which is how the fleet orders work (parse < sema < silgen < sil <
irgen < exec).

Usage:
  python3 -I swiftrs/tools/corpus/extract.py [--tag swift-3.1-RELEASE]
"""

import argparse
import collections
import io
import os
import re
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFTRS = os.path.normpath(os.path.join(HERE, "..", ".."))

# Paths pulled out of the tag. Tests and their inputs, the standard library
# (compiled by swiftrs at milestone 2+), and the reference sources that agents
# port from.
ARCHIVE_PATHS = [
    "test",
    "validation-test",
    "stdlib",
    "unittests",
    "lib",
    "include",
    "docs",
    "utils/gyb.py",
]

# lit.cfg: config.suffixes / config.excludes
TEST_SUFFIXES = (".swift", ".ll", ".sil", ".gyb", ".m", ".test-sh")
EXCLUDED_DIRS = {"Inputs"}

# Whole directories that are out of scope regardless of content.
OUT_DIRS = {
    "test/SourceKit": "tool:sourcekit",
    "test/IDE": "tool:ide",
    "test/api-digester": "tool:api-digester",
    "test/PrintAsObjC": "objc",
    "test/ClangModules": "clang-importer",
    "test/ClangImporter": "clang-importer",
    "test/Sanitizers": "tool:sanitizers",
    "test/Migrator": "tool:migrator",
    "test/RemoteAST": "tool:remote-ast",
    "test/Reflection": "tool:reflection",
    "test/sil-extract": "tool:sil-extract",
    "test/Unit": "gtest",
    "validation-test/IDE": "tool:ide",
    "validation-test/SourceKit": "tool:sourcekit",
    "validation-test/Reflection": "tool:reflection",
    "validation-test/compiler_scale": "perf",
    "validation-test/Python": "tool:python",
    # Unfixed fuzzer crashers expect the reference compiler to crash.
    "validation-test/compiler_crashers": "crasher-unfixed",
    "validation-test/compiler_crashers_2": "crasher-unfixed",
}

# RUN-line substitutions/tools that put a test out of scope.
OUT_TOOLS = [
    (re.compile(r"%(target-)?swift-ide-test\b|%complete-test\b|%sourcekitd-test\b"), "tool:ide"),
    (re.compile(r"%swift-format\b"), "tool:ide"),
    (re.compile(r"%target-swift-reflection-(dump|test)\b|swift-remoteast-test"), "tool:reflection"),
    (re.compile(r"%api-digester\b"), "tool:api-digester"),
    (re.compile(r"%(target-)?clang\b|%check-in-clang\b|%clang-importer-sdk|-import-objc-header|-Xcc\b|-enable-objc-interop|%build-clang-importer-objc-overlays"), "clang-importer"),
    (re.compile(r"%build-(irgen|silgen)-test-overlays\b"), "objc"),
    (re.compile(r"%target-jit-run\b|-interpret\b|-i\b"), "deferred:immediate"),
    (re.compile(r"\blldb\b|%lldb"), "tool:lldb"),
    (re.compile(r"swift-autolink-extract|autolink-extract"), "deferred:autolink"),
]

# Imports that require the Clang importer or a C/ObjC platform module.
C_MODULES = {
    "Foundation", "ObjectiveC", "Darwin", "Glibc", "CoreGraphics",
    "CoreFoundation", "Dispatch", "XCTest", "AppKit", "UIKit", "Cocoa",
    "CoreImage", "CoreData", "CoreLocation", "CoreMedia", "AVFoundation",
    "GameplayKit", "SceneKit", "SpriteKit", "MapKit", "Accelerate",
    "SwiftPrivateLibcExtras", "SwiftPrivatePthreadExtras", "SwiftShims",
    "ctypes", "Builtin_float", "IOKit", "Security", "WatchKit", "os",
    "simd", "GLKit", "Metal", "MetalKit", "XPC", "CloudKit", "Contacts",
    "HomeKit", "Intents", "Photos", "AssetsLibrary", "CoreAudio",
    "CallKit", "SafariServices", "StdlibUnittestFoundationExtras",
    "ImportAsMember", "ObjCParseExtras", "objc_generics", "Accessibility",
}
IMPORT_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?([A-Za-z_][A-Za-z_0-9]*)", re.M)

REQUIRES_OUT = [
    (re.compile(r"\bobjc_interop\b"), "objc"),
    (re.compile(r"\bOS=(macosx|ios|tvos|watchos)\b"), "darwin-only"),
    (re.compile(r"\bdisabled\b"), "disabled"),
    (re.compile(r"\bswift_repl\b"), "deferred:repl"),
    (re.compile(r"\bswift_interpreter\b"), "deferred:immediate"),
    (re.compile(r"\b(tsan_runtime|asan|no_asan)\b"), "tool:sanitizers"),
    (re.compile(r"\bcan_import\b"), "clang-importer"),
    (re.compile(r"\bswift-remoteast-test\b"), "tool:reflection"),
    (re.compile(r"\bCODEGENERATOR=(ARM|AArch64|PowerPC|SystemZ)\b"), "deferred:non-x86-codegen-test"),
]

# Deepest-stage detection. Order matters: last match wins by rank.
STAGES = ["parse", "sema", "silgen", "sil", "serialization", "irgen", "driver", "exec"]
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}
STAGE_PATTERNS = [
    ("parse", re.compile(r"(?<![\w-])-(parse|dump-parse)\b")),
    ("sema", re.compile(r"(?<![\w-])-(typecheck|dump-ast|print-ast|dump-type-refinement-contexts)\b|%target-typecheck-verify-swift")),
    ("silgen", re.compile(r"(?<![\w-])-emit-silgen\b")),
    ("sil", re.compile(r"(?<![\w-])-emit-sil\b|(?<![\w-])-emit-sib\b|%target-sil-opt\b|%sil-opt\b|%target-sil-func-extractor\b|%sil-nm\b")),
    ("serialization", re.compile(r"(?<![\w-])-emit-module\b|llvm-bcanalyzer|%target-swift-modulewrap")),
    ("irgen", re.compile(r"(?<![\w-])-(emit-ir|emit-bc|emit-assembly|emit-object|S|c)\b|%target-swift-frontend\b(?!.*-(typecheck|parse|emit-\w+|dump-\w+|print-ast))")),
    ("driver", re.compile(r"%(target-)?swiftc_driver\b|%swift_driver(_plain)?\b|-driver-print-jobs|-driver-print-actions")),
    ("exec", re.compile(r"%target-run\b|%target-run-simple-swift\b|%target-run-stdlib-swift\b|%target-build-swift\b|%target-run-simple-swiftgyb\b|%target-codesign\b")),
]


def git_toplevel():
    return subprocess.check_output(["git", "-C", SWIFTRS, "rev-parse", "--show-toplevel"], text=True).strip()


def extract(repo, tag, out):
    if os.path.isdir(os.path.join(out, "test")):
        print(f"corpus already present at {out}", file=sys.stderr)
        return
    if subprocess.call(["git", "-C", repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], stdout=subprocess.DEVNULL) != 0:
        print(f"fetching tag {tag}", file=sys.stderr)
        subprocess.check_call(["git", "-C", repo, "fetch", "--depth", "1", "origin", "tag", tag])
    print(f"extracting {tag} -> {out}", file=sys.stderr)
    data = subprocess.check_output(["git", "-C", repo, "archive", "--format=tar", tag, "--"] + ARCHIVE_PATHS)
    tmp = out + ".tmp"
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(tmp, filter="data")
    os.replace(tmp, out)


LIT_SHIM = """\
# Installed by swiftrs/tools/corpus/extract.py: run this suite with the
# swiftrs lit configuration. The original 3.1 config is lit.cfg.orig.
import os
config.test_source_root = os.path.dirname(os.path.abspath(__file__))
lit_config.load_config(config, {cfg!r})
"""


def install_lit_shims(out):
    cfg = os.path.join(SWIFTRS, "tools", "test", "lit.cfg.py")
    for suite in ("test", "validation-test"):
        path = os.path.join(out, suite, "lit.cfg")
        orig = path + ".orig"
        if os.path.exists(path) and not os.path.exists(orig):
            os.replace(path, orig)
        for site in ("lit.site.cfg", "lit.site.cfg.in"):
            if os.path.exists(os.path.join(out, suite, site)):
                os.replace(os.path.join(out, suite, site), os.path.join(out, suite, site + ".orig"))
        with open(path, "w") as f:
            f.write(LIT_SHIM.format(cfg=cfg))


def directives(text):
    run, req, xfail, unsup = [], [], [], []
    for line in text.splitlines():
        m = re.search(r"\b(RUN|REQUIRES|XFAIL|UNSUPPORTED):(.*)$", line)
        if not m:
            continue
        kind, body = m.group(1), m.group(2).strip()
        {"RUN": run, "REQUIRES": req, "XFAIL": xfail, "UNSUPPORTED": unsup}[kind].append(body)
    # lit joins RUN lines ending in '\'.
    joined, cur = [], ""
    for r in run:
        if r.endswith("\\"):
            cur += r[:-1] + " "
        else:
            joined.append(cur + r)
            cur = ""
    if cur:
        joined.append(cur)
    return joined, req, xfail, unsup


def classify(rel, text):
    """Returns (status, reason, stage, tags)."""
    for d, reason in OUT_DIRS.items():
        if rel == d or rel.startswith(d + "/"):
            return "out", reason, "", []

    run, req, xfail, unsup = directives(text)
    tags = []
    if not run:
        return "out", "no-run-line", "", tags

    reqs = " ".join(req)
    for pat, reason in REQUIRES_OUT:
        if pat.search(reqs):
            return "out", reason, "", tags

    xf = " ".join(xfail)
    if re.search(r"(^|[\s,])\*($|[\s,])", xf):
        return "out", "xfail-everywhere", "", tags
    if re.search(r"\blinux\b", xf):
        # The Linux reference compiler fails this test, so there is no oracle.
        return "out", "xfail-linux", "", tags
    us = " ".join(unsup)
    if re.search(r"OS=linux-gnu\b", us):
        return "out", "unsupported-linux", "", tags

    runs = "\n".join(run)
    for pat, reason in OUT_TOOLS:
        if pat.search(runs):
            return "out", reason, "", tags

    imports = set(IMPORT_RE.findall(text))
    cmods = sorted(imports & C_MODULES)
    if cmods:
        return "out", "imports-c:" + ",".join(cmods[:3]), "", tags

    if rel.endswith(".m"):
        return "out", "objc", "", tags
    if rel.endswith(".ll"):
        tags.append("llvm-pass")

    stage = ""
    for name, pat in STAGE_PATTERNS:
        if pat.search(runs) and (not stage or STAGE_RANK[name] > STAGE_RANK[stage]):
            stage = name
    if not stage:
        stage = "other"

    if "StdlibUnittest" in imports:
        tags.append("needs:StdlibUnittest")
    if "-parse-stdlib" in runs or "import Builtin" in text:
        tags.append("builtin")
    if "executable_test" in reqs:
        tags.append("executable")
    if re.search(r"\bCPU=|PTRSIZE=|CODEGENERATOR=", reqs):
        tags.append("arch-specific")
    if "-verify" in runs or "%target-typecheck-verify-swift" in runs:
        tags.append("verify")
    if "FileCheck" in runs:
        tags.append("filecheck")
    if "%gyb" in runs or rel.endswith(".gyb"):
        tags.append("gyb")
    if rel.startswith("validation-test/compiler_crashers"):
        # Fixed fuzzer crashers: "must not crash" tests, tracked separately.
        tags.append("fuzz")
    return "in", "", stage, tags


def walk_tests(root):
    for suite in ("test", "validation-test"):
        base = os.path.join(root, suite)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            for fn in sorted(filenames):
                if fn.endswith(TEST_SUFFIXES) and fn != "lit.cfg":
                    path = os.path.join(dirpath, fn)
                    yield os.path.relpath(path, root), path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", default="swift-3.1-RELEASE")
    ap.add_argument("--name", default="swift-3.1")
    ap.add_argument("--repo", default=None, help="git repo holding the tag (default: this checkout)")
    args = ap.parse_args()

    repo = args.repo or git_toplevel()
    out = os.path.join(SWIFTRS, ".corpus", args.name)
    extract(repo, args.tag, out)
    install_lit_shims(out)

    rows = []
    for rel, path in walk_tests(out):
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", errors="replace")
        status, reason, stage, tags = classify(rel, text)
        rows.append((rel, status, reason, stage, ",".join(tags)))

    manifest = os.path.join(SWIFTRS, "corpus", f"{args.name}.tsv")
    with open(manifest, "w") as f:
        f.write("# generated by tools/corpus/extract.py; do not edit by hand\n")
        f.write("path\tstatus\treason\tstage\ttags\n")
        for r in rows:
            f.write("\t".join(r) + "\n")

    ins = [r for r in rows if r[1] == "in"]
    core = [r for r in ins if "fuzz" not in r[4].split(",")]
    outs = collections.Counter(r[2].split(":")[0] if r[2].startswith("imports-c") else r[2] for r in rows if r[1] == "out")
    stages = collections.Counter(r[3] for r in core)
    tagc = collections.Counter(t for r in ins for t in r[4].split(",") if t)

    summary = os.path.join(SWIFTRS, "corpus", f"{args.name}.summary.md")
    with open(summary, "w") as f:
        f.write(f"# {args.tag} test corpus\n\n")
        f.write("Generated by `tools/corpus/extract.py`. Do not edit by hand.\n\n")
        f.write(f"- Total lit tests: **{len(rows)}**\n")
        f.write(f"- In scope (pure Swift, compiler/stdlib/runtime): **{len(ins)}**\n")
        f.write(f"  - core tests: **{len(core)}**\n")
        f.write(f"  - fixed fuzzer crashers (must not crash): **{len(ins) - len(core)}**\n")
        f.write(f"- Out of scope: **{len(rows) - len(ins)}**\n\n")
        f.write("## Core tests, by deepest stage\n\n| stage | tests |\n|---|---|\n")
        for s in STAGES + ["other"]:
            if stages[s]:
                f.write(f"| {s} | {stages[s]} |\n")
        f.write("\n## In scope, by tag\n\n| tag | tests |\n|---|---|\n")
        for t, n in tagc.most_common():
            f.write(f"| {t} | {n} |\n")
        f.write("\n## Out of scope, by reason\n\n| reason | tests |\n|---|---|\n")
        for t, n in outs.most_common():
            f.write(f"| {t} | {n} |\n")
    print(open(summary).read())


if __name__ == "__main__":
    main()
