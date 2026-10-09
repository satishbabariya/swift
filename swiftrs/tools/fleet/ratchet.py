#!/usr/bin/env python3
"""Pass-count ratchet for swiftrs.

The scoreboard (scoreboard/<corpus>.json) records every test that has ever
passed. A change may add passes but must not lose one: that is the single
rule that keeps a fleet of parallel agents from undoing each other's work.

  ratchet.py check  .lit/results.json   exit 1 if a scoreboard test regressed
  ratchet.py update .lit/results.json   check, then record new passes
  ratchet.py status                     per-stage progress table

Only tests present in the results file are checked, so a sampled run checks
its sample. Deliberate regressions go in scoreboard/allowed-regressions.txt
(one path per line, with a reason comment), reviewed by a human.
"""

import argparse
import collections
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFTRS = os.path.normpath(os.path.join(HERE, "..", ".."))
GOOD = {"PASS", "XFAIL"}


def board_path(corpus):
    return os.path.join(SWIFTRS, "scoreboard", f"{corpus}.json")


def load_board(corpus):
    try:
        with open(board_path(corpus)) as f:
            return set(json.load(f)["passing"])
    except FileNotFoundError:
        return set()


def save_board(corpus, passing):
    os.makedirs(os.path.dirname(board_path(corpus)), exist_ok=True)
    with open(board_path(corpus), "w") as f:
        json.dump({"corpus": corpus, "passing": sorted(passing)}, f, indent=0)
        f.write("\n")


def load_results(path):
    with open(path) as f:
        data = json.load(f)
    out = {}
    for t in data.get("tests", []):
        suite, _, rel = t["name"].partition(" :: ")
        out[f"{suite}/{rel}"] = t["code"]
    return out


def allowed_regressions():
    path = os.path.join(SWIFTRS, "scoreboard", "allowed-regressions.txt")
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {l.split("#")[0].strip() for l in f if l.split("#")[0].strip()}


def manifest(corpus):
    rows = {}
    with open(os.path.join(SWIFTRS, "corpus", f"{corpus}.tsv")) as f:
        for line in f:
            if line.startswith(("#", "path\t")):
                continue
            path, status, _, stage, tags = line.rstrip("\n").split("\t")
            if status == "in":
                if zlib.crc32(path.encode()) % 10 == 0:
                    rows[path] = "held-out"
                else:
                    rows[path] = "fuzz" if "fuzz" in tags.split(",") else stage
    return rows


def check(corpus, results_path):
    board = load_board(corpus)
    results = load_results(results_path)
    allowed = allowed_regressions()
    regressed = sorted(p for p, code in results.items()
                       if p in board and code not in GOOD and p not in allowed)
    new = sorted(p for p, code in results.items() if code in GOOD and p not in board)
    print(f"checked {len(results)} tests: {len(regressed)} regressed, {len(new)} newly passing")
    for p in regressed[:50]:
        print(f"  REGRESSED {p} ({results[p]})")
    if len(regressed) > 50:
        print(f"  ... and {len(regressed) - 50} more")
    return board, results, regressed, new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["check", "update", "status"])
    ap.add_argument("results", nargs="?")
    ap.add_argument("--corpus", default="swift-3.1")
    args = ap.parse_args()

    if args.command == "status":
        board = load_board(args.corpus)
        m = manifest(args.corpus)
        total, passing = collections.Counter(m.values()), collections.Counter(m[p] for p in board if p in m)
        print(f"{'stage':<14} {'passing':>8} {'total':>8}")
        for stage in sorted(total):
            print(f"{stage:<14} {passing[stage]:>8} {total[stage]:>8}  {100 * passing[stage] // total[stage]:>3}%")
        print(f"{'all':<14} {sum(passing.values()):>8} {sum(total.values()):>8}")
        return

    if not args.results:
        ap.error("results file required")
    board, results, regressed, new = check(args.corpus, args.results)
    if regressed:
        sys.exit(1)
    if args.command == "update" and new:
        save_board(args.corpus, board | set(new))
        print(f"scoreboard updated: {len(board) + len(new)} passing")


if __name__ == "__main__":
    main()
