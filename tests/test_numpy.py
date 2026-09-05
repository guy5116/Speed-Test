"""bench_numpy against bench.py, output for output.

rng_block is non-trivial math (lane decomposition, an affine jump computed by
binary exponentiation) and before this file it was only validated indirectly,
at n=1,000,000, through the selftest. Here every lane boundary is checked
against the scalar bigint reference, and the three vectorised benchmarks are
checked value-for-value at tiny sizes.

Run with:  python3 -m unittest discover tests
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    import bench_numpy
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

import bench


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class TestRngBlock(unittest.TestCase):
    # 1024 is the lane count, so the boundaries around it are where the
    # decomposition could tear
    COUNTS = (1, 2, 1023, 1024, 1025, 2048, 5000)

    def test_blocks_match_the_scalar_stream(self):
        for count in self.COUNTS:
            with self.subTest(count=count):
                bench.rng_seed(12345)
                bench_numpy.rng_seed(12345)
                block = bench_numpy.rng_block(count)
                expected = [bench.rng_next() for _ in range(count)]
                self.assertEqual(block.tolist(), expected)
                # the scalar state must land exactly where `count` sequential
                # calls left the reference generator
                self.assertEqual(bench_numpy._rng_state, bench._rng_state)

    def test_two_blocks_equal_one(self):
        bench_numpy.rng_seed(9)
        first = bench_numpy.rng_block(600).tolist()
        second = bench_numpy.rng_block(700).tolist()
        bench_numpy.rng_seed(9)
        combined = bench_numpy.rng_block(1300).tolist()
        self.assertEqual(first + second, combined)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class TestVectorisedBenchmarks(unittest.TestCase):
    def test_mandelbrot(self):
        for n in (1, 7, 16, 33):
            with self.subTest(n=n):
                self.assertEqual(bench_numpy.bench_mandelbrot(n),
                                 bench.bench_mandelbrot(n))

    def test_sieve(self):
        for n in (2, 3, 100):
            with self.subTest(n=n):
                self.assertEqual(bench_numpy.bench_sieve(n), bench.bench_sieve(n))

    def test_matmul(self):
        for n in (1, 2, 8):
            with self.subTest(n=n):
                self.assertEqual(bench_numpy.bench_matmul(n), bench.bench_matmul(n))

    def test_prng_checksum(self):
        self.assertEqual(bench_numpy.bench_prng(1000), bench.bench_prng(1000))


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class TestSingleThreadCharter(unittest.TestCase):
    def test_blas_thread_env_set_before_numpy_import(self):
        # bench_numpy must pin the BLAS pools before `import numpy`; by the
        # time this test runs the import happened, so the vars must be there
        for var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            self.assertEqual(os.environ.get(var), "1", var)


if __name__ == "__main__":
    unittest.main()
