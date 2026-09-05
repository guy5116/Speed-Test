"""write_report: results.json is the machine-readable record of a run, so
its shape, its numbers and its reproducibility metadata are all contract.

Run with:  python3 -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import run


def fake_lang(key, name):
    lang = run.Language(key, name, "37")
    lang.available = True
    lang.version = name + " 1.0"
    lang.note = "made of cardboard"
    return lang


class TestWriteReport(unittest.TestCase):
    def setUp(self):
        self._saved = run.BUILD
        run.BUILD = tempfile.mkdtemp(prefix="report-test-")
        self.active = [fake_lang("fastlang", "Fastlang"),
                       fake_lang("slowlang", "Slowlang")]
        self.benches = [b for b in run.BENCHMARKS
                        if b["key"] in ("mandelbrot", "sieve", "quicksort")]
        # quicksort deliberately absent from results: an excluded row
        self.results = {"mandelbrot": {"fastlang": 0.10, "slowlang": 0.15},
                        "sieve": {"fastlang": 0.20, "slowlang": 0.22}}
        self.startup = {"fastlang": 0.001, "slowlang": 0.02}
        self.run_meta = {
            "scale": 1.0, "reps": 3,
            "sizes": {"mandelbrot": 800, "sieve": 50_000_000, "quicksort": 4_000_000},
            "checksums": {"mandelbrot": 111, "sieve": 222},
            "rss_mb": {"mandelbrot": {"fastlang": 3.0, "slowlang": 40.0},
                       "sieve": {"fastlang": 55.0, "slowlang": 60.0}},
            "argv": ["--quick", "--shuffle"], "seed": 7,
            "order": {"mandelbrot": ["slowlang", "fastlang"],
                      "sieve": ["fastlang", "slowlang"]},
            "commit": "abc1234", "pinned": None, "serial_gc": False,
        }
        self.path = run.write_report(self.active, self.benches, self.results,
                                     self.startup, self.run_meta, 12.3)
        with open(os.path.join(run.BUILD, "results.json")) as fh:
            self.payload = json.load(fh)
        with open(self.path) as fh:
            self.html = fh.read()

    def tearDown(self):
        shutil.rmtree(run.BUILD, ignore_errors=True)
        run.BUILD = self._saved

    def test_top_level_shape(self):
        for key in ("geomean_slowdown", "benchmarks", "languages",
                    "startup_seconds", "machine", "workload"):
            self.assertIn(key, self.payload)

    def test_benchmark_entries(self):
        entry = self.payload["benchmarks"]["mandelbrot"]
        self.assertEqual(entry["checksum"], 111)
        self.assertEqual(entry["size"], 800)
        self.assertEqual(entry["seconds"], self.results["mandelbrot"])
        self.assertEqual(entry["peak_rss_mb"]["slowlang"], 40.0)

    def test_excluded_benchmark_is_absent_everywhere(self):
        self.assertNotIn("quicksort", self.payload["benchmarks"])
        self.assertNotIn("quicksort", self.html.lower())

    def test_geomean_recomputed(self):
        # slowlang: 1.5x on mandelbrot, 1.1x on sieve
        want = run.gmean([0.15 / 0.10, 0.22 / 0.20])
        self.assertAlmostEqual(self.payload["geomean_slowdown"]["slowlang"], want)
        self.assertAlmostEqual(self.payload["geomean_slowdown"]["fastlang"], 1.0)

    def test_html_self_contained(self):
        self.assertTrue(os.path.exists(self.path))
        low = self.html.lower()
        self.assertNotIn('src="http://', low)
        self.assertNotIn('src="https://', low)
        self.assertNotIn("src='http", low)
        self.assertNotIn("<link rel", low)          # no external stylesheets
        for lang in self.active:
            self.assertIn(lang.name, self.html)

    def test_workload_metadata_round_trips(self):
        wl = self.payload["workload"]
        self.assertEqual(wl["argv"], ["--quick", "--shuffle"])
        self.assertEqual(wl["seed"], 7)
        self.assertEqual(wl["order"]["mandelbrot"], ["slowlang", "fastlang"])
        self.assertEqual(wl["commit"], "abc1234")


if __name__ == "__main__":
    unittest.main()
