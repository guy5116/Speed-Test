#!/usr/bin/env python3
"""Regenerate or verify golden.json, reproducibly.

golden.json used to be hand-recorded from cross-verified runs. This script
makes it mechanical: for every benchmark and every stock scale (--quick,
standard, --heavy) it runs at least two independent implementations -- the C
build (with the runner's exact flags), bench.js, and bench_numpy.py where it
enters -- and only accepts a value every present source agrees on. The prng
entries come from the arbitrary-precision Python reference, which has no
64-bit tricks to get wrong.

    python3 tools/golden.py --check              # verify golden.json, exit 1 on any diff
    python3 tools/golden.py --check --quick-only # just the --quick sizes (for CI)
    python3 tools/golden.py --write              # rewrite golden.json (sorted, one per line)

A checksum-moving change (e.g. a new binarytrees checksum) is applied safely
as: change the implementations, `--write`, and commit both together.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import bench      # noqa: E402  (the bigint PRNG reference)
import run        # noqa: E402

GOLDEN_PATH = os.path.join(HERE, "golden.json")
STOCK_SCALES = (0.08, 1.0, 6.0)     # --quick, standard, --heavy
QUICK_SCALES = (0.08,)
PRNG_COUNTS = (1000, 1000000)       # what --selftest and the unit tests use
SOURCE_KEYS = ("c", "js", "numpy")  # independent implementations to cross-check


def sources():
    langs = {l.key: l for l in run.build_all(set()) if l.available}
    return [langs[k] for k in SOURCE_KEYS if k in langs]


def compute(scales, langs):
    """Run every (benchmark, scale) through every source that enters it.
    Returns ({bench: {str(size): value}}, [problem strings])."""
    entries, problems = {}, []
    for b in run.BENCHMARKS:
        for scale in scales:
            size = run.sized(b, scale)
            if size > run.INT32_MAX:
                continue
            votes = {}
            for lang in langs:
                if b["key"] in lang.sits_out:
                    continue
                secs, checksum, wall, rss, err, med, worst = run.run_one(
                    lang, b["key"], size, 1, timeout=1800.0)
                if err:
                    problems.append("%s %d: %s" % (b["key"], size, err))
                else:
                    votes[lang.key] = checksum
            distinct = set(votes.values())
            if len(votes) < 2:
                problems.append("%s %d: only %d independent source(s) -- "
                                "need two to agree" % (b["key"], size, len(votes)))
            elif len(distinct) > 1:
                problems.append("%s %d: sources disagree: %r" % (b["key"], size, votes))
            else:
                value = distinct.pop()
                entries.setdefault(b["key"], {})[str(size)] = value
                print("  %-12s %11d  %12d   (%s)"
                      % (b["key"], size, value, ", ".join(sorted(votes))))
    for count in PRNG_COUNTS:
        entries.setdefault("prng", {})[str(count)] = bench.bench_prng(count)
        print("  %-12s %11d  %12d   (python bigint reference)"
              % ("prng", count, entries["prng"][str(count)]))
    return entries, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="recompute and compare against golden.json")
    ap.add_argument("--write", action="store_true",
                    help="recompute and rewrite golden.json")
    ap.add_argument("--quick-only", action="store_true",
                    help="with --check: only the --quick-scale sizes")
    args = ap.parse_args()
    if args.check == args.write:
        ap.error("pass exactly one of --check / --write")
    if args.quick_only and args.write:
        ap.error("--quick-only would write a partial file; it only checks")

    scales = QUICK_SCALES if args.quick_only else STOCK_SCALES
    langs = sources()
    print("sources: %s" % ", ".join(l.key for l in langs))
    entries, problems = compute(scales, langs)
    for p in problems:
        print("PROBLEM: %s" % p)

    if args.write:
        if problems:
            print("refusing to write golden.json with unverified entries")
            return 1
        with open(GOLDEN_PATH, "w") as fh:
            json.dump(entries, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote %s" % os.path.relpath(GOLDEN_PATH))
        return 0

    with open(GOLDEN_PATH) as fh:
        recorded = json.load(fh)
    bad = len(problems)
    for bench_key, by_size in sorted(entries.items()):
        for size, value in sorted(by_size.items()):
            want = recorded.get(bench_key, {}).get(size)
            if want is None:
                print("MISSING: golden.json has no %s/%s (computed %d)"
                      % (bench_key, size, value))
                bad += 1
            elif want != value:
                print("DIFFERS: %s/%s computed %d, golden.json says %d"
                      % (bench_key, size, value, want))
                bad += 1
    if not args.quick_only:
        # full check: golden.json must not carry entries nothing reproduces
        for bench_key, by_size in sorted(recorded.items()):
            for size in by_size:
                if size not in entries.get(bench_key, {}):
                    print("STALE: golden.json carries %s/%s, which no stock "
                          "scale produces" % (bench_key, size))
                    bad += 1
    if bad:
        print("%d problem(s)." % bad)
        return 1
    print("golden.json reproduces at %s."
          % ("the --quick scale" if args.quick_only else "every stock scale"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
