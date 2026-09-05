"""Row validation, the 32-bit scale cap, and the protocol parser -- the
pure functions Tier 2 pulled out of main()/run_one so they could be tested
without running a single benchmark.

Run with:  python3 -m unittest discover tests
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run


class TestValidateRow(unittest.TestCase):
    def test_agree_and_match_golden(self):
        ok, value, note = run.validate_row({"c": 42, "js": 42}, 42)
        self.assertTrue(ok)
        self.assertEqual(value, 42)
        self.assertEqual(note, "matches golden")

    def test_agree_no_golden(self):
        ok, value, note = run.validate_row({"c": 42, "js": 42}, None)
        self.assertTrue(ok)
        self.assertEqual(value, 42)

    def test_agree_but_golden_mismatch(self):
        # every language agrees, and every language is wrong: the one bug
        # within-run agreement can never catch
        ok, value, note = run.validate_row({"c": 41, "js": 41}, 42)
        self.assertFalse(ok)
        self.assertEqual(value, 41)
        self.assertIn("golden", note)

    def test_disagreement(self):
        ok, value, note = run.validate_row({"c": 1, "js": 2}, None)
        self.assertFalse(ok)
        self.assertIsNone(value)
        self.assertIn("DISAGREE", note)

    def test_empty_row(self):
        ok, value, note = run.validate_row({}, None)
        self.assertFalse(ok)
        self.assertIsNone(value)

    def test_single_language_self_validates(self):
        # a one-language run must be able to validate itself (against the
        # golden when there is one, trivially when there is not)
        ok, value, note = run.validate_row({"c": 7}, None)
        self.assertTrue(ok)
        self.assertEqual(value, 7)
        ok, value, note = run.validate_row({"c": 7}, 7)
        self.assertTrue(ok)
        self.assertEqual(note, "matches golden")


class TestInt32Cap(unittest.TestCase):
    def by_key(self, key):
        return next(b for b in run.BENCHMARKS if b["key"] == key)

    def test_known_caps(self):
        self.assertAlmostEqual(run.int32_cap(self.by_key("sieve")),
                               42.94967294, places=4)
        self.assertAlmostEqual(run.int32_cap(self.by_key("mandelbrot")) / 1e12,
                               7.2057594, places=3)
        self.assertAlmostEqual(run.int32_cap(self.by_key("matmul")) / 1e19,
                               7.9228162, places=3)

    def test_cap_is_tight_for_every_benchmark(self):
        for b in run.BENCHMARKS:
            cap = run.int32_cap(b)
            self.assertLessEqual(run.sized(b, cap), run.INT32_MAX, b["key"])
            self.assertGreater(run.sized(b, cap * 1.01), run.INT32_MAX, b["key"])


class TestParseOutput(unittest.TestCase):
    def test_good_line(self):
        secs, checksum, err, med, worst = run.parse_output("OK sieve 42 1.500")
        self.assertIsNone(err)
        self.assertEqual(checksum, 42)
        self.assertAlmostEqual(secs, 0.0015)
        self.assertIsNone(med)        # the old 4-field form carries no spread
        self.assertIsNone(worst)

    def test_spread_protocol_six_fields(self):
        secs, checksum, err, med, worst = run.parse_output(
            "OK sieve 42 1.500 1.600 1.900")
        self.assertIsNone(err)
        self.assertEqual(checksum, 42)
        self.assertAlmostEqual(secs, 0.0015)
        self.assertAlmostEqual(med, 0.0016)
        self.assertAlmostEqual(worst, 0.0019)

    def test_five_fields_is_an_error(self):
        secs, checksum, err, med, worst = run.parse_output("OK sieve 42 1.5 1.6")
        self.assertIsNotNone(err)

    def test_zero_time_clamped_above_zero(self):
        secs, checksum, err, med, worst = run.parse_output("OK sieve 42 0.000")
        self.assertIsNone(err)
        self.assertEqual(checksum, 42)
        self.assertGreater(secs, 0)

    def test_missing_field_is_an_error(self):
        secs, checksum, err, med, worst = run.parse_output("OK sieve 42")
        self.assertIsNotNone(err)
        self.assertIsNone(secs)

    def test_bad_checksum_is_an_error(self):
        secs, checksum, err, med, worst = run.parse_output("OK sieve x 1.0")
        self.assertIsNotNone(err)

    def test_whitespace_tolerated(self):
        secs, checksum, err, med, worst = run.parse_output("  OK sieve 42 1.500 \n")
        self.assertIsNone(err)
        self.assertEqual(checksum, 42)

    def test_empty_output_is_an_error(self):
        secs, checksum, err, med, worst = run.parse_output("")
        self.assertIsNotNone(err)


class TestGoldenStockTies(unittest.TestCase):
    STOCK = (0.08, 1.0, 6.0)          # --quick, standard, --heavy

    def test_golden_and_stock_scales_cover_each_other(self):
        # both directions: every recorded size is a stock size, and every
        # stock (benchmark, scale) pair has a recorded golden -- this ties
        # golden.json and the BENCHMARKS table together
        with open(os.path.join(os.path.dirname(HERE), "golden.json")) as fh:
            golden = json.load(fh)
        for b in run.BENCHMARKS:
            stock_sizes = set(str(run.sized(b, s)) for s in self.STOCK)
            self.assertEqual(set(golden.get(b["key"], {})), stock_sizes,
                             b["key"])


if __name__ == "__main__":
    unittest.main()
