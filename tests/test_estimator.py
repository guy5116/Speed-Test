"""The Estimator: it may only ever be wrong about the progress bar, so these
tests pin down the ways it must not be wrong -- unbounded bias, a poisoned
cache, a foreign host's numbers, or a typo in the reference tables.

Run with:  python3 -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run


def by_key(key):
    return next(b for b in run.BENCHMARKS if b["key"] == key)


class TestEstimatorLearning(unittest.TestCase):
    def setUp(self):
        # isolate every test from build/timings.json and from each other
        self._saved = run.TIMING_CACHE
        self._dir = tempfile.TemporaryDirectory()
        run.TIMING_CACHE = os.path.join(self._dir.name, "timings.json")

    def tearDown(self):
        run.TIMING_CACHE = self._saved
        self._dir.cleanup()

    def test_estimate_tracks_a_recorded_wall(self):
        est = run.Estimator()
        b = by_key("sieve")
        est.record("c", b, b["base"], 3, 1.2)
        guess = est.estimate("c", b, b["base"], 3)
        self.assertLess(abs(guess - 1.2) / 1.2, 0.05)

    def test_bias_bounded_after_pathological_walls(self):
        est = run.Estimator()
        b = by_key("sieve")
        est.record("c", b, b["base"], 1, 1000.0)      # wall 1000x any sane guess
        self.assertTrue(0.1 <= est.bias <= 10.0, est.bias)
        est.record("c", b, b["base"] * 8, 1, 0.06)    # then absurdly fast
        self.assertTrue(0.1 <= est.bias <= 10.0, est.bias)

    def test_machine_sane_after_zero_wall(self):
        est = run.Estimator()
        est.record("c", by_key("sieve"), 50_000_000, 1, 0.0)
        self.assertGreater(est.machine, 0)
        self.assertTrue(est.machine == est.machine and est.machine != float("inf"))

    def test_save_load_roundtrip(self):
        est = run.Estimator()
        est.record("c", by_key("sieve"), 50_000_000, 3, 1.2)
        est.save()
        again = run.Estimator()
        self.assertEqual(again.rates, est.rates)

    def test_foreign_host_cache_is_ignored(self):
        with open(run.TIMING_CACHE, "w") as fh:
            json.dump({"host": "someone|else|4 cores", "machine": 9.0,
                       "build": 5.0, "startup": {"c": 1.0},
                       "rates": {"c|sieve": {"0": 123.0}}}, fh)
        est = run.Estimator()
        self.assertEqual(est.rates, {})
        self.assertEqual(est.machine, 1.0)

    def test_cold_estimate_comes_from_the_reference_tables(self):
        # no cache, no prior run: the guess must be REF_SECONDS x REF_RATIO
        # plus the startup constant -- this catches a typo in the tables
        est = run.Estimator()
        b = by_key("mandelbrot")
        want = (run.REF_SECONDS["mandelbrot"] * run.REF_RATIO["c"]["mandelbrot"]
                + run.REF_STARTUP["c"])
        self.assertAlmostEqual(est.estimate("c", b, b["base"], 1), want, places=9)

    def test_total_skips_sits_out(self):
        est = run.Estimator()
        plays = run.Language("c", "C", "36")
        sits = run.Language("numpy", "NumPy", "36")
        sits.sits_out["quicksort"] = "sits this one out"
        benches = [by_key("quicksort")]
        self.assertGreater(est.total([plays], benches, 1.0, 1), 0)
        self.assertEqual(est.total([sits], benches, 1.0, 1), 0)


if __name__ == "__main__":
    unittest.main()
