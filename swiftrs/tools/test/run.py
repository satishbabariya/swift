#!/usr/bin/env python3
"""Run a selection of the in-scope Swift 3.1 lit tests against swiftrs.

Selects tests from corpus/<corpus>.tsv, runs them with lit, writes the raw lit
results to .lit/results.json and prints a short per-stage summary. Output is
kept terse on purpose: agents read it.

Examples:
  tools/test/run.py --stage sema                 # all core sema tests
  tools/test/run.py --stage sema --sample 50     # deterministic 50-test sample
  tools/test/run.py --path test/Parse            # by path prefix
  tools/test/run.py --tag fuzz --sample 200 --seed 7
  tools/test/run.py test/Parse/matching_patterns.swift -v
  tools/test/run.py --all                        # everything in scope (slow)

Then: tools/fleet/ratchet.py check .lit/results.json
"""

import argparse
import collections
import json
import os
import random
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFTRS = os.path.normpath(os.path.join(HERE, "..", ".."))


def load_manifest(corpus):
    rows = []
    with open(os.path.join(SWIFTRS, "corpus", f"{corpus}.tsv")) as f:
        for line in f:
            if line.startswith("#") or line.startswith("path\t"):
                continue
            path, status, reason, stage, tags = line.rstrip("\n").split("\t")
            rows.append({"path": path, "status": status, "reason": reason,
                         "stage": stage, "tags": set(filter(None, tags.split(",")))})
    return rows


def is_held_out(path):
    """10% of tests, fixed by path, that agents never see (docs/DESIGN.md §5)."""
    return zlib.crc32(path.encode()) % 10 == 0


def lit_binary():
    venv = os.path.join(SWIFTRS, ".venv", "bin", "lit")
    if os.path.exists(venv):
        return [venv]
    return [sys.executable, "-m", "lit"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tests", nargs="*", help="explicit test paths (relative to the corpus root)")
    ap.add_argument("--corpus", default="swift-3.1")
    ap.add_argument("--stage", action="append", help="select by deepest stage (repeatable)")
    ap.add_argument("--tag", action="append", help="select tests carrying this tag (repeatable)")
    ap.add_argument("--path", action="append", help="select by path prefix (repeatable)")
    ap.add_argument("--all", action="store_true", help="every in-scope test, including fuzz")
    ap.add_argument("--scoreboard", action="store_true", help="only tests already on the scoreboard")
    ap.add_argument("--held-out", action="store_true", help="include the held-out set (scorekeeper only)")
    ap.add_argument("--no-fuzz", action="store_true", help="exclude fixed fuzzer crashers")
    ap.add_argument("--sample", type=int, help="deterministic random sample of N tests")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--shard", help="I/N: run shard I of N (0-based)")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    ap.add_argument("-v", "--verbose", action="store_true", help="show output of failing tests")
    ap.add_argument("--param", action="append", default=[], help="extra lit --param k=v")
    ap.add_argument("--output", default=os.path.join(SWIFTRS, ".lit", "results.json"))
    ap.add_argument("--list", action="store_true", help="print the selection and exit")
    args = ap.parse_args()

    rows = [r for r in load_manifest(args.corpus) if r["status"] == "in"]
    if not (args.all or args.held_out):
        rows = [r for r in rows if not is_held_out(r["path"])]
    if args.tests:
        wanted = set(args.tests)
        rows = [r for r in rows if r["path"] in wanted]
        missing = wanted - {r["path"] for r in rows}
        if missing:
            print(f"not in scope or held out (see corpus/{args.corpus}.tsv): {' '.join(sorted(missing))}", file=sys.stderr)
    elif not (args.all or args.stage or args.tag or args.path or args.scoreboard):
        ap.error("select tests: give paths, --stage, --tag, --path, --scoreboard or --all")
    if args.scoreboard:
        board = os.path.join(SWIFTRS, "scoreboard", f"{args.corpus}.json")
        passing = set(json.load(open(board))["passing"]) if os.path.exists(board) else set()
        rows = [r for r in rows if r["path"] in passing]
    if args.stage:
        rows = [r for r in rows if r["stage"] in args.stage]
    if args.tag:
        rows = [r for r in rows if r["tags"] & set(args.tag)]
    if args.path:
        rows = [r for r in rows if any(r["path"].startswith(p) for p in args.path)]
    if args.no_fuzz or (args.stage and not (args.tag and "fuzz" in args.tag)):
        rows = [r for r in rows if "fuzz" not in r["tags"]]
    if args.sample and args.sample < len(rows):
        rows = sorted(random.Random(args.seed).sample(rows, args.sample), key=lambda r: r["path"])
    if args.shard:
        i, n = map(int, args.shard.split("/"))
        rows = rows[i::n]
    if not rows:
        sys.exit("no tests selected")
    if args.list:
        for r in rows:
            print(r["path"])
        return

    root = os.path.join(SWIFTRS, ".corpus", args.corpus)
    if not os.path.isdir(root):
        sys.exit(f"missing corpus {root}; run tools/corpus/extract.py")
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    cmd = lit_binary() + ["-q", "-j", str(args.jobs), "--output", args.output]
    if args.verbose:
        cmd.append("-v")
    cmd += ["--param", "swiftrs_bin=" + os.path.join(SWIFTRS, "target", "debug"),
            "--param", "lit_root=" + os.path.join(SWIFTRS, ".lit")]
    for p in args.param:
        cmd += ["--param", p]
    cmd += [os.path.join(root, r["path"]) for r in rows]
    print(f"running {len(rows)} tests", file=sys.stderr)
    rc = subprocess.call(cmd, stdout=None if args.verbose else subprocess.DEVNULL)

    with open(args.output) as f:
        results = json.load(f)
    by_path = {r["path"]: r for r in rows}
    stats = collections.defaultdict(collections.Counter)
    for t in results.get("tests", []):
        suite, _, rel = t["name"].partition(" :: ")
        r = by_path.get(f"{suite}/{rel}")
        stats[r["stage"] if r else "?"][t["code"]] += 1
    codes = ["PASS", "FAIL", "UNSUPPORTED", "XFAIL", "XPASS", "UNRESOLVED", "TIMEOUT"]
    print("stage          " + " ".join(f"{c[:6]:>7}" for c in codes))
    total = collections.Counter()
    for stage in sorted(stats):
        total.update(stats[stage])
        print(f"{stage:<14} " + " ".join(f"{stats[stage][c]:>7}" for c in codes))
    print(f"{'total':<14} " + " ".join(f"{total[c]:>7}" for c in codes))
    print(f"results: {os.path.relpath(args.output)}")
    sys.exit(0 if rc == 0 else 1)


if __name__ == "__main__":
    main()
