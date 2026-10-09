#!/usr/bin/env python3
"""Record reference Swift 3.1 outputs for differential testing.

lit tests check what their authors chose to FileCheck. Goldens check
everything else: for each selected in-scope source file this runs the
reference frontend once per stage and stores stdout, stderr and the exit code
under .goldens/<corpus>/<path>/<mode>.{out,err,rc}. tools/test/diff.py (later
milestones) compares swiftrs output against these after normalization.

Needs the reference toolchain: Docker with the swift:3.1 image (default, via
tools/reference/bin) or --reference-bin pointing at a Swift 3.1 usr/bin.

  tools/reference/goldens.py --stage sema --stage silgen
  tools/reference/goldens.py --tag executable --modes emit-ir,run
"""

import argparse
import concurrent.futures
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFTRS = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(SWIFTRS, "tools", "test"))
from run import load_manifest  # noqa: E402

MODES = ["dump-parse", "dump-ast", "emit-silgen", "emit-sil", "emit-ir", "run"]

# Flags copied from a test's own frontend RUN line so the golden compiles the
# file the way the test does. Value-taking flags list their arity.
PASSTHROUGH = {
    "-parse-stdlib": 0, "-parse-as-library": 0, "-enable-source-import": 0,
    "-disable-access-control": 0, "-enable-testing": 0, "-module-name": 1,
    "-swift-version": 1, "-D": 1, "-I": 1, "-enable-resilience": 0,
    "-assume-single-threaded": 0, "-disable-availability-checking": 0,
}


def frontend_flags(text, corpus_dir, test_path):
    """Pass-through flags from the first frontend RUN line, %S expanded."""
    for line in text.splitlines():
        m = re.search(r"RUN:.*%target-(?:swift-frontend(?:\(mock-sdk:[^)]*\))?|typecheck-verify-swift)\s(.*)", line)
        if not m:
            continue
        try:
            toks = shlex.split(m.group(1).split("|")[0])
        except ValueError:
            return []
        out, i = [], 0
        while i < len(toks):
            arity = PASSTHROUGH.get(toks[i])
            if arity is not None:
                vals = toks[i + 1:i + 1 + arity]
                out += [toks[i]] + [v.replace("%S", os.path.dirname(test_path)) for v in vals]
                i += 1 + arity
            else:
                i += 1
        return out
    return []


def run_one(ref_bin, triple, root, out_root, row, modes, timeout):
    path = os.path.join(root, row["path"])
    if not path.endswith(".swift"):
        return row["path"], "skipped"
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    flags = frontend_flags(text, root, path)
    if "-module-name" not in flags:
        flags += ["-module-name", "main"]
    dest = os.path.join(out_root, row["path"])
    os.makedirs(dest, exist_ok=True)
    for mode in modes:
        if mode == "run":
            exe = os.path.join(dest, "a.out")
            cmds = [[os.path.join(ref_bin, "swiftc"), "-target", triple, "-Onone", path, "-o", exe] + flags,
                    [os.path.join(ref_bin, "run"), exe]]
        else:
            cmds = [[os.path.join(ref_bin, "swift"), "-frontend", "-target", triple, "-" + mode, path] + flags]
        rc, out, err = 0, b"", b""
        for cmd in cmds:
            try:
                p = subprocess.run(cmd, capture_output=True, timeout=timeout)
                rc, out, err = p.returncode, p.stdout, p.stderr
            except subprocess.TimeoutExpired:
                rc, out, err = -999, b"", b"timeout"
            if rc != 0:
                break
        for ext, data in (("out", out), ("err", err), ("rc", str(rc).encode() + b"\n")):
            with open(os.path.join(dest, f"{mode}.{ext}"), "wb") as f:
                f.write(data.replace(root.encode(), b"<CORPUS>"))
    return row["path"], "ok"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="swift-3.1")
    ap.add_argument("--stage", action="append")
    ap.add_argument("--tag", action="append")
    ap.add_argument("--path", action="append")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--reference-bin", default=os.path.join(HERE, "bin"))
    ap.add_argument("--triple", default="x86_64-unknown-linux-gnu")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args()

    modes = [m for m in args.modes.split(",") if m]
    bad = set(modes) - set(MODES)
    if bad:
        ap.error(f"unknown modes: {', '.join(sorted(bad))}")
    rows = [r for r in load_manifest(args.corpus) if r["status"] == "in" and "fuzz" not in r["tags"]]
    if args.stage:
        rows = [r for r in rows if r["stage"] in args.stage]
    if args.tag:
        rows = [r for r in rows if r["tags"] & set(args.tag)]
    if args.path:
        rows = [r for r in rows if any(r["path"].startswith(p) for p in args.path)]
    root = os.path.join(SWIFTRS, ".corpus", args.corpus)
    out_root = os.path.join(SWIFTRS, ".goldens", args.corpus)
    print(f"recording {len(rows)} tests x {len(modes)} modes -> {os.path.relpath(out_root)}", file=sys.stderr)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(run_one, args.reference_bin, args.triple, root, out_root, r, modes, args.timeout) for r in rows]
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(rows)}", file=sys.stderr)
            fut.result()


if __name__ == "__main__":
    main()
