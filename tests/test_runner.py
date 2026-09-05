"""Unit tests for run.py's pure logic -- the parts a benchmark run itself
never exercises deliberately: the averaging, the sizing math, the overflow
guard's premise, the estimator's bounds, and run_one's protocol parsing.

Run with:  python3 -m unittest discover tests
No toolchains needed; nothing here compiles or benchmarks anything.
"""
import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run


class TestGmean(unittest.TestCase):
    def test_basics(self):
        self.assertAlmostEqual(run.gmean([2.0, 8.0]), 4.0)
        self.assertAlmostEqual(run.gmean([1.0, 1.0, 1.0]), 1.0)
        self.assertAlmostEqual(run.gmean([3.0]), 3.0)

    def test_baseline_independence(self):
        # The Fleming & Wallace point: rescaling every ratio by a constant
        # rescales the mean by exactly that constant, so the *ranking* of
        # languages cannot depend on which baseline was chosen.
        rs = [1.4, 2.0, 37.5, 0.9]
        self.assertAlmostEqual(run.gmean([r * 2.5 for r in rs]),
                               run.gmean(rs) * 2.5)

    def test_less_swayed_by_outlier_than_arithmetic(self):
        rs = [1.0, 1.0, 1.0, 100.0]
        self.assertLess(run.gmean(rs), sum(rs) / len(rs))


class TestSized(unittest.TestCase):
    def by_key(self, key):
        return next(b for b in run.BENCHMARKS if b["key"] == key)

    def test_stock_scales(self):
        # the exact sizes golden.json was recorded at
        self.assertEqual(run.sized(self.by_key("mandelbrot"), 0.08), 226)
        self.assertEqual(run.sized(self.by_key("mandelbrot"), 1.0), 800)
        self.assertEqual(run.sized(self.by_key("mandelbrot"), 6.0), 1959)
        self.assertEqual(run.sized(self.by_key("sieve"), 6.0), 300000000)
        self.assertEqual(run.sized(self.by_key("matmul"), 6.0), 908)

    def test_floor(self):
        self.assertEqual(run.sized(self.by_key("matmul"), 1e-9), 8)

    def test_scale_50_overflows_32_bits(self):
        # the premise of main()'s INT32 guard: --scale 50 really does push
        # the sieve past what C's atoi / Java's int can hold
        self.assertGreater(run.sized(self.by_key("sieve"), 50), 2**31 - 1)


class TestGolden(unittest.TestCase):
    def test_golden_file_is_sane(self):
        with open(os.path.join(os.path.dirname(HERE), "golden.json")) as fh:
            golden = json.load(fh)
        keys = set(b["key"] for b in run.BENCHMARKS)
        self.assertTrue(set(golden) - {"prng"} <= keys)
        for bench, entries in golden.items():
            for size, val in entries.items():
                int(size)
                self.assertIsInstance(val, int)
                self.assertGreaterEqual(val, 0)
                self.assertLess(val, 1 << 64)
        # the reference PRNG checksum, recomputed from scratch
        state, h = 12345, 0
        for _ in range(1000):
            state = (state * 6364136223846793005 + 1442695040888963407) % 2**64
            h = (h * 31 + (state >> 33)) & 0xFFFFFFFF
        self.assertEqual(golden["prng"]["1000"], h)


class TestEstimator(unittest.TestCase):
    def test_units_growth(self):
        b = next(b for b in run.BENCHMARKS if b["growth"] == "cubic")
        self.assertAlmostEqual(run.Estimator.units(b, b["base"] * 2), 8.0)
        b = next(b for b in run.BENCHMARKS if b["growth"] == "quadratic")
        self.assertAlmostEqual(run.Estimator.units(b, b["base"] * 3), 9.0)

    def test_bucket_is_octaves(self):
        self.assertEqual(run.Estimator.bucket(1.0), "0")
        self.assertEqual(run.Estimator.bucket(8.0), "3")
        self.assertEqual(run.Estimator.bucket(1e-9), run.Estimator.bucket(1e-12))


@unittest.skipUnless(os.path.exists("/bin/sh"), "needs a POSIX shell")
class TestRunOne(unittest.TestCase):
    def fake_lang(self, argv):
        lang = run.Language("fake", "Fake", "37")
        lang.cmd = argv
        return lang

    def test_parses_protocol(self):
        lang = self.fake_lang(["/bin/sh", "-c", 'echo "OK sieve 42 1.500"'])
        secs, checksum, wall, rss, err = run.run_one(lang, "sieve", 8, 1)
        self.assertIsNone(err)
        self.assertEqual(checksum, 42)
        self.assertAlmostEqual(secs, 0.0015)
        self.assertGreater(wall, 0)

    def test_nonzero_exit_is_an_error(self):
        lang = self.fake_lang(["/bin/sh", "-c", "echo boom >&2; exit 3"])
        secs, checksum, wall, rss, err = run.run_one(lang, "sieve", 8, 1)
        self.assertIsNone(secs)
        self.assertIn("exited 3", err)
        self.assertIn("boom", err)

    def test_garbage_output_is_an_error(self):
        lang = self.fake_lang(["/bin/sh", "-c", "echo not a benchmark"])
        _, _, _, _, err = run.run_one(lang, "sieve", 8, 1)
        self.assertIn("unexpected", err)

    def test_timeout_kills_the_child(self):
        lang = self.fake_lang(["/bin/sh", "-c", "sleep 30"])
        _, _, _, _, err = run.run_one(lang, "sieve", 8, 1, timeout=0.3)
        self.assertIn("timed out", err)

    def test_zero_time_is_clamped(self):
        lang = self.fake_lang(["/bin/sh", "-c", 'echo "OK sieve 4 0.000"'])
        secs, _, _, _, err = run.run_one(lang, "sieve", 8, 1)
        self.assertIsNone(err)
        self.assertGreater(secs, 0)


if __name__ == "__main__":
    unittest.main()
