"""run.py driven the way CI drives it: as a subprocess, with --plain, and
with the exit code as the contract. Everything here that benchmarks anything
trims the language list to C, JavaScript and Python to stay quick.

Run with:  python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ALL_KEYS = ("asm", "c", "cpp", "rust", "swift", "go", "java", "csharp", "js",
            "lua", "perl", "php", "python", "numpy", "ruby", "cobol")
KEEP = {"c", "js", "python"}
SKIP = ",".join(k for k in ALL_KEYS if k not in KEEP)

GCC = shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
NODE = shutil.which("node") or shutil.which("nodejs")
HAVE_TOOLCHAINS = bool(GCC and NODE)


def cli(*argv, cwd=ROOT, timeout=300):
    # run the run.py that lives in `cwd`, so a test working on a copied tree
    # really exercises the copy (its golden.json, its build directory)
    proc = subprocess.run([sys.executable, os.path.join(cwd, "run.py")]
                          + list(argv) + ["--plain"], capture_output=True,
                          text=True, cwd=cwd, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


class TestArgumentErrors(unittest.TestCase):
    # these fail before anything builds, so they need no toolchain

    def test_unknown_skip_bench_lists_the_known_keys(self):
        code, out = cli("--skip-bench", "bogus")
        self.assertEqual(code, 1)
        for key in ("mandelbrot", "sieve", "quicksort", "wordcount",
                    "binarytrees", "matmul"):
            self.assertIn(key, out)

    def test_only_nothing_left(self):
        code, out = cli("--only", "nothing")
        self.assertEqual(code, 1)


@unittest.skipUnless(HAVE_TOOLCHAINS, "needs a C compiler and node")
class TestRealRuns(unittest.TestCase):
    def test_int32_guard_is_a_skip_not_a_failure(self):
        code, out = cli("--scale", "50", "--only", "sieve", "--skip", SKIP)
        self.assertEqual(code, 0, out)
        self.assertIn("overflows the 32-bit languages", out)
        # the guard must fire before the estimate promises the skipped work
        self.assertLess(out.index("overflows the 32-bit languages"),
                        out.index("estimate"))
        self.assertIn("Nothing ran.", out)

    def test_quick_sieve_matches_golden(self):
        code, out = cli("--quick", "--only", "sieve", "--skip", SKIP)
        self.assertEqual(code, 0, out)
        self.assertIn("matches golden", out)

    def test_corrupt_golden_fails_the_run(self):
        # a bug shared by EVERY implementation: only the golden can catch it,
        # and catching it must be a non-zero exit, or CI would smile at it
        tmp = tempfile.mkdtemp(prefix="speedtest-golden-")
        try:
            for name in ("run.py", "bench.c", "bench.js", "bench.py",
                         "golden.json"):
                shutil.copy2(os.path.join(ROOT, name), tmp)
            path = os.path.join(tmp, "golden.json")
            with open(path) as fh:
                golden = json.load(fh)
            golden["sieve"]["4000000"] = 1        # no sieve returns 1
            with open(path, "w") as fh:
                json.dump(golden, fh)
            code, out = cli("--quick", "--only", "sieve", "--skip", SKIP,
                            cwd=tmp)
            self.assertEqual(code, 1, out)
            self.assertIn("EXCLUDED", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_strict_require_missing_language(self):
        code, out = cli("--quick", "--only", "sieve", "--skip", SKIP,
                        "--strict", "--require", "cobol")
        self.assertEqual(code, 1, out)
        self.assertIn("required language(s) missing: cobol", out)

    def test_min_time_grows_reps_and_still_validates(self):
        # C at --quick sieve costs ~ms per rep, so --min-time forces many
        # reps; the checksum must be identical across all of them and the
        # row must still validate against the golden
        code, out = cli("--quick", "--only", "sieve", "--skip", SKIP,
                        "--min-time", "0.3")
        self.assertEqual(code, 0, out)
        self.assertIn("matches golden", out)

    def test_selftest_passes(self):
        code, out = cli("--selftest", "--skip", SKIP)
        self.assertEqual(code, 0, out)
        self.assertIn("Every language reproduces the shared PRNG bit-for-bit",
                      out)
        self.assertNotIn("returned", out)     # the mismatch wording

    def test_shuffle_seed_reproduces_the_order(self):
        orders = []
        for _ in range(2):
            code, out = cli("--quick", "--only", "sieve", "--skip", SKIP,
                            "--shuffle", "--seed", "7")
            self.assertEqual(code, 0, out)
            with open(os.path.join(ROOT, "build", "results.json")) as fh:
                orders.append(json.load(fh)["workload"]["order"])
        self.assertEqual(orders[0], orders[1])
        self.assertEqual(sorted(orders[0]["sieve"]), sorted(KEEP))


if __name__ == "__main__":
    unittest.main()
