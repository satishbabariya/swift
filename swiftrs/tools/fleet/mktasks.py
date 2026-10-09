#!/usr/bin/env python3
"""Turn failing tests into fleet work items (fleet/tasks/<id>.md).

Deterministic first pass: groups the failing in-scope tests of one stage by
directory into tasks of at most --size tests. A triage agent then refines
these (merging tasks that share a root cause, splitting ones that don't,
writing hints), so treat the output as a draft.

Task ids sort by pipeline order, so workers take parser work before sema,
sema before SILGen, and so on: `<stage-rank><seq>-<stage>-<dir>`.

  tools/fleet/mktasks.py .lit/results.json --stage sema --size 15
"""

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFTRS = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(SWIFTRS, "tools", "test"))
from run import is_held_out, load_manifest  # noqa: E402

STAGES = ["parse", "sema", "silgen", "sil", "serialization", "irgen", "driver", "exec", "other"]

TEMPLATE = """# {title}

Stage: {stage}
Subsystem: {subsystem}

Make these Swift 3.1 tests pass. They currently fail. Read each test and its
RUN lines first; run one with `tools/test/run.py <path> -v`. The reference
implementation is the C++ under `.corpus/swift-3.1/lib/{subsystem}/` (and its
headers in `.corpus/swift-3.1/include/swift/`); the subsystem spec is
`docs/specs/{spec}.md` if it exists.

## Tests
{tests}
"""

SUBSYSTEM = {
    "parse": "Parse", "sema": "Sema", "silgen": "SILGen", "sil": "SILOptimizer",
    "serialization": "Serialization", "irgen": "IRGen", "driver": "Driver",
    "exec": "IRGen", "other": "Basic",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--corpus", default="swift-3.1")
    ap.add_argument("--stage", action="append")
    ap.add_argument("--size", type=int, default=15)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = {r["path"]: r for r in load_manifest(args.corpus) if r["status"] == "in"}
    with open(args.results) as f:
        results = json.load(f).get("tests", [])
    failing = []
    for t in results:
        suite, _, rel = t["name"].partition(" :: ")
        path = f"{suite}/{rel}"
        r = manifest.get(path)
        if r and not is_held_out(path) and t["code"] in ("FAIL", "UNRESOLVED", "TIMEOUT", "XPASS"):
            if not args.stage or r["stage"] in args.stage:
                failing.append(r)

    tasks_dir = os.path.join(SWIFTRS, "fleet", "tasks")
    existing = set()
    if os.path.isdir(tasks_dir):
        for fn in os.listdir(tasks_dir):
            with open(os.path.join(tasks_dir, fn)) as f:
                existing |= {l[2:].split()[0] for l in f if l.startswith("- ")}

    groups = collections.defaultdict(list)
    for r in failing:
        if r["path"] in existing:
            continue
        stage = "fuzz" if "fuzz" in r["tags"] else r["stage"]
        groups[(stage, os.path.dirname(r["path"]))].append(r["path"])

    seq = collections.Counter()
    made = 0
    for (stage, d), paths in sorted(groups.items(), key=lambda kv: (STAGES.index(kv[0][0]) if kv[0][0] in STAGES else 99, kv[0][1])):
        rank = STAGES.index(stage) if stage in STAGES else 9
        for i in range(0, len(paths), args.size):
            chunk = sorted(paths)[i:i + args.size]
            seq[stage] += 1
            slug = d.replace("validation-test/", "v-").replace("test/", "").replace("/", "-").lower()
            tid = f"{rank}{seq[stage]:04d}-{stage}-{slug}"
            sub = SUBSYSTEM.get(stage, "Sema")
            body = TEMPLATE.format(
                title=f"{stage}: {len(chunk)} failing tests in {d}", stage=stage, subsystem=sub,
                spec=sub.lower(), tests="\n".join(f"- {p}" for p in chunk))
            made += 1
            if args.dry_run:
                print(tid)
                continue
            os.makedirs(tasks_dir, exist_ok=True)
            with open(os.path.join(tasks_dir, tid + ".md"), "w") as f:
                f.write(body)
    print(f"{made} tasks from {len(failing)} failing tests ({len(existing)} already assigned)", file=sys.stderr)


if __name__ == "__main__":
    main()
