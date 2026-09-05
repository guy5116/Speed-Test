#!/usr/bin/env python3
"""
bench_numpy.py -- Python again, but written the way Python is actually used
for numeric work: the loops pushed down into NumPy, where they run as C.

Usage: python3 bench_numpy.py <benchmark> <size> [reps]
Prints: OK <benchmark> <checksum> <best_ms> <median_ms> <worst_ms>

Same deterministic input, same checksum as every other bench.* in the suite,
so the runner will happily race it against everyone else. It only enters the
three benchmarks that have an honest vectorised form (mandelbrot, sieve,
matmul); the other three are declared sit-outs in run.py, because a NumPy
"quicksort" would be C's introsort, a NumPy "wordcount" would skip the
strings entirely, and binary trees are made of exactly the small heap
objects NumPy exists to avoid.

Everything here is bit-exact with the scalar implementations:
- mandelbrot performs the identical IEEE double operations per pixel, just
  for a whole grid of pixels at a time, retiring points as they escape;
- the shared LCG is vectorised by lane decomposition (k parallel streams,
  each stepping k states at a time -- an LCG composed with itself is still
  an LCG), so the stream of outputs is unchanged;
- matmul runs in float64 through BLAS, which is exact here: entries are
  < 100, so every dot product is an integer far below 2^53.
"""
import math
import os
import sys
import time

# The suite is single-threaded by charter. BLAS libraries fan out across
# cores by default, and these knobs only work if set before the import.
for _var in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[_var] = "1"

import numpy as np

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

# ---------- shared deterministic PRNG (identical stream, vectorised) ----------
LCG_A = 6364136223846793005
LCG_C = 1442695040888963407

_rng_state = 0


def rng_seed(s):
    global _rng_state
    _rng_state = s


def _affine_pow(k):
    """The LCG step is x -> A*x + C (mod 2^64); k steps compose to another
    affine map. Returns its (a, c) by binary exponentiation."""
    ra, rc = 1, 0                    # identity
    ba, bc = LCG_A, LCG_C            # one step
    while k:
        if k & 1:
            ra, rc = (ba * ra) & MASK64, (ba * rc + bc) & MASK64
        ba, bc = (ba * ba) & MASK64, (ba * bc + bc) & MASK64
        k >>= 1
    return ra, rc


def rng_block(count):
    """The next `count` outputs of the shared PRNG, in order, as a uint64
    array. Runs k=1024 interleaved lanes, each jumping k states per step, so
    the sequential recurrence becomes a handful of whole-array multiplies."""
    global _rng_state
    lanes = min(1024, count)
    steps = (count + lanes - 1) // lanes

    s = _rng_state
    first = np.empty(lanes, dtype=np.uint64)
    for i in range(lanes):           # states s_1 .. s_lanes, the slow way once
        s = (s * LCG_A + LCG_C) & MASK64
        first[i] = s

    ja, jc = _affine_pow(lanes)      # the k-step jump
    ja, jc = np.uint64(ja), np.uint64(jc)
    states = np.empty((steps, lanes), dtype=np.uint64)
    states[0] = first
    v = first
    for j in range(1, steps):        # uint64 arithmetic wraps, like the scalar
        v = v * ja + jc
        states[j] = v

    # leave the scalar state exactly where `count` sequential calls would
    a, c = _affine_pow(count)
    _rng_state = (a * _rng_state + c) & MASK64
    return states.ravel()[:count] >> np.uint64(33)   # top 31 bits, in order


# ---------- 1. mandelbrot: the whole grid iterates at once ----------
def bench_mandelbrot(n):
    # identical expressions to the scalar version, so identical doubles:
    # ci = 2.0*py/n - 1.0 and cr = 2.0*px/n - 1.5, one IEEE divide each
    ci = 2.0 * np.arange(n, dtype=np.float64) / n - 1.0
    cr = 2.0 * np.arange(n, dtype=np.float64) / n - 1.5
    ci = np.repeat(ci, n)
    cr = np.tile(cr, n)
    zr = np.zeros(n * n, dtype=np.float64)
    zi = np.zeros(n * n, dtype=np.float64)

    total = 0
    for _ in range(255):
        zr2 = zr * zr
        zi2 = zi * zi
        alive = zr2 + zi2 <= 4.0     # scalar loop breaks on > 4.0
        total += int(np.count_nonzero(alive))
        if not alive.all():          # retire escaped points, keep arrays dense
            zr, zi, cr, ci = zr[alive], zi[alive], cr[alive], ci[alive]
            zr2, zi2 = zr2[alive], zi2[alive]
            if zr.size == 0:
                break
        zi = 2.0 * zr * zi + ci      # old zr, exactly like the scalar body
        zr = zr2 - zi2 + cr
    return total


# ---------- 2. sieve of eratosthenes: marking becomes a strided store ----------
def bench_sieve(n):
    comp = np.zeros(n + 1, dtype=bool)
    for i in range(2, math.isqrt(n) + 1):
        if not comp[i]:
            comp[i * i:: i] = True
    return (n - 1) - int(np.count_nonzero(comp[2:]))


# ---------- 6. matmul: BLAS, exact in float64 because entries are < 100 ----------
def bench_matmul(n):
    rng_seed(12345)
    a = (rng_block(n * n) % np.uint64(100)).astype(np.float64).reshape(n, n)
    b = (rng_block(n * n) % np.uint64(100)).astype(np.float64).reshape(n, n)
    c = a @ b                        # every entry an integer < n*99*99 << 2^53

    # h = (h*31 + v) & MASK32 folded over c is a polynomial in 31 mod 2^32,
    # so it vectorises as a dot product against descending powers of 31
    v = c.ravel().astype(np.uint32)
    base = np.full(v.size, 31, dtype=np.uint32)
    base[0] = 1
    pows = np.cumprod(base, dtype=np.uint32)         # 31^0 .. 31^(N-1), mod 2^32
    return int((v * pows[::-1]).sum(dtype=np.uint32))


# ---------- hidden: prng conformance check, not part of the scored suite ----------
# run.py --selftest uses it; for this file it is the direct test of the lane
# decomposition in rng_block -- every one of the n outputs, in order.
def bench_prng(n):
    rng_seed(12345)
    v = rng_block(n).astype(np.uint32)
    base = np.full(v.size, 31, dtype=np.uint32)
    base[0] = 1
    pows = np.cumprod(base, dtype=np.uint32)       # same fold as bench_matmul
    return int((v * pows[::-1]).sum(dtype=np.uint32))


BENCHMARKS = {
    "mandelbrot": bench_mandelbrot,
    "sieve": bench_sieve,
    "matmul": bench_matmul,
    "prng": bench_prng,
}


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: bench_numpy.py <benchmark> <size> [reps]\n")
        return 2
    name = sys.argv[1]
    size = int(sys.argv[2])
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    reps = max(1, reps)

    fn = BENCHMARKS.get(name)
    if fn is None:
        sys.stderr.write("numpy sits out %s (see run.py); it runs: %s\n"
                         % (name, ", ".join(sorted(BENCHMARKS))))
        return 2

    times = []
    result = None
    for r in range(reps):
        t0 = time.perf_counter()
        v = fn(size)
        times.append((time.perf_counter() - t0) * 1000.0)
        if r == 0:
            result = v
        elif v != result:
            sys.stderr.write("nondeterministic result!\n")
            return 3

    times.sort()
    print("OK %s %d %.3f %.3f %.3f"
          % (name, result, times[0], times[reps // 2], times[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
