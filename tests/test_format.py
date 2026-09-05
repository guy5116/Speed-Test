"""The formatting helpers: readable, honest, and inert when colour is off.

Run with:  python3 -m unittest discover tests
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run

_UNIT = {"microsecond": 1e-6, "millisecond": 1e-3, "second": 1.0, "sec": 1.0,
         "min": 60.0, "hr": 3600.0, "day": 86400.0}


def parse_human(text):
    """'1 min 30 sec' -> 90.0: fold every number-unit pair back to seconds."""
    total = 0.0
    for num, unit in re.findall(r"([0-9.]+) ([a-z]+)", text):
        if unit.endswith("s"):
            unit = unit[:-1]
        total += float(num) * _UNIT[unit]
    return total


class TestHumanTime(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(run.human_time(0.0005), "500 microseconds")
        self.assertEqual(run.human_time(0.0123), "12 milliseconds")
        self.assertEqual(run.human_time(5), "5.0 seconds")
        self.assertEqual(run.human_time(90), "1 min 30 sec")
        self.assertEqual(run.human_time(5400), "1 hr 30 min")
        self.assertEqual(run.human_time(108000), "1 day 6 hr")

    def test_monotone_across_unit_boundaries(self):
        # A longer duration must never *read* as a shorter one: sweep six
        # decades and check the value a reader would parse never decreases.
        prev = -1.0
        x = 1e-6
        while x < 3e6:
            got = parse_human(run.human_time(x))
            self.assertGreaterEqual(got, prev, run.human_time(x))
            prev = got
            x *= 1.07


class TestSmallNumbers(unittest.TestCase):
    def test_commas(self):
        self.assertEqual(run.commas(1234567), "1,234,567")

    def test_fmt_secs_never_shows_zero(self):
        self.assertNotEqual(run.fmt_secs(0.0004), "0.000 s")
        self.assertEqual(run.fmt_secs(0.0004), "400 us")


class TestHeat(unittest.TestCase):
    def test_heat_ratio_always_a_hue(self):
        for ratio in (0, 1, 100, 1e9):
            col = run.heat_ratio(ratio)
            self.assertEqual(len(col), 4)          # (r, g, b, ansi fallback)
            for channel in col[:3]:
                self.assertIsInstance(channel, int)


class TestColourOff(unittest.TestCase):
    def test_gradient_and_hue_are_inert_without_colour(self):
        saved = run.USE_COLOR
        run.USE_COLOR = False
        try:
            self.assertEqual(run.hue("abc", run.PINK), "abc")
            self.assertEqual(run.hue("abc", run.PINK, bold=True), "abc")
            self.assertEqual(run.gradient("abc", run.PINK, run.AQUA), "abc")
        finally:
            run.USE_COLOR = saved


if __name__ == "__main__":
    unittest.main()
