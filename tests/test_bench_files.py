"""Every language entry against the Python oracle, at the tiny sizes where
hand translations diverge: odd grids, empty composite ranges, the insertion-
sort threshold, the k=0 checksum term, 1x1 matrices. bench.py is importable,
so it IS the oracle -- goldens for tiny sizes are computed on the fly.

Whatever toolchains are missing simply are not in LANGS; on a machine with
only Python this degenerates to Python-vs-itself and still passes.

Run with:  python3 -m unittest discover tests   (~5 s per available language)
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bench
import run

TMP = None
LANGS = []

# sizes chosen to hit the edges where translations diverge
CASES = {
    "mandelbrot": (1, 2, 16, 33),          # odd n, single pixel
    "sieve": (2, 3, 4, 100, 1000),         # n=2/3 have no composites
    "quicksort": (1, 2, 16, 17, 18, 1000),  # around the insertion-sort threshold
    "wordcount": (1, 2, 1000),             # maxc=1 path, distinct-count path
    "binarytrees": (1, 2, 3),              # the k=0 term in the checksum
    "matmul": (1, 2, 3, 8),                # n=1 exercises ib=0 index math
    "prng": (1, 2, 1000),
}


def setUpModule():
    global TMP, LANGS
    TMP = tempfile.mkdtemp(prefix="speedtest-oracle-")
    run.BUILD = TMP
    run.BUILD_NOTES = os.path.join(TMP, "buildinfo.json")
    run.TIMING_CACHE = os.path.join(TMP, "timings.json")
    LANGS = [l for l in run.build_all(set()) if l.available]


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class TestCrossLanguageOracle(unittest.TestCase):
    def test_tiny_sizes_match_the_oracle(self):
        for lang in LANGS:
            for key, sizes in CASES.items():
                if key in lang.sits_out:
                    continue
                for size in sizes:
                    with self.subTest(lang=lang.key, bench=key, size=size):
                        expected = getattr(bench, "bench_" + key)(size)
                        secs, checksum, wall, rss, err = run.run_one(
                            lang, key, size, 1, timeout=120.0)
                        self.assertIsNone(err, err)
                        self.assertEqual(checksum, expected)

    def test_reps_do_not_change_the_answer(self):
        expected = bench.bench_sieve(1000)
        for lang in LANGS:
            with self.subTest(lang=lang.key):
                secs, checksum, wall, rss, err = run.run_one(
                    lang, "sieve", 1000, 2, timeout=120.0)
                self.assertIsNone(err, err)
                self.assertEqual(checksum, expected)


class TestEntryProtocol(unittest.TestCase):
    def test_warmup_argument_accepted_by_every_entry(self):
        # run.py only sends the 4th argument to the JIT runtimes, but the
        # protocol says everyone must tolerate it
        expected = bench.bench_sieve(1000)
        for lang in LANGS:
            with self.subTest(lang=lang.key):
                code, out, errout, wall, rss, err = run.launch(
                    lang.cmd + ["sieve", "1000", "1", "2"], timeout=120.0)
                self.assertIsNone(err, err)
                self.assertEqual(code, 0, errout)
                secs, checksum, perr = run.parse_output(out)
                self.assertIsNone(perr, perr)
                self.assertEqual(checksum, expected)

    def test_unknown_benchmark_exits_2(self):
        for lang in LANGS:
            with self.subTest(lang=lang.key):
                code, out, errout, wall, rss, err = run.launch(
                    lang.cmd + ["unknown_bench", "8", "1"], timeout=120.0)
                self.assertIsNone(err, err)
                self.assertEqual(code, 2, (out, errout))

    def test_no_arguments_exits_2(self):
        for lang in LANGS:
            with self.subTest(lang=lang.key):
                code, out, errout, wall, rss, err = run.launch(
                    list(lang.cmd), timeout=120.0)
                self.assertIsNone(err, err)
                self.assertEqual(code, 2, (out, errout))


if __name__ == "__main__":
    unittest.main()
