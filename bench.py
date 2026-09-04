#!/usr/bin/env python3
"""
bench.py -- the Python entry in the language speed comparison.

Usage: python3 bench.py <benchmark> <size> [reps]
Prints: OK <benchmark> <checksum> <compute_milliseconds>

Same algorithm, same deterministic input, same checksum as every other
bench.* in this suite.

Deliberately written in plain, idiomatic Python. No numpy, no slice tricks,
no C extensions -- the point is to show what the *interpreter* costs when you
write the loop yourself. See the README for what changes when you don't.
"""
import sys
import time

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

# ---------- shared deterministic PRNG (identical in every language here) ----------
_rng_state = 0


def rng_seed(s):
    global _rng_state
    _rng_state = s


def rng_next():
    global _rng_state
    _rng_state = (_rng_state * 6364136223846793005 + 1442695040888963407) & MASK64
    return _rng_state >> 33  # top 31 bits


# ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
def bench_mandelbrot(n):
    total = 0
    for py in range(n):
        ci = 2.0 * py / n - 1.0
        for px in range(n):
            cr = 2.0 * px / n - 1.5
            zr = 0.0
            zi = 0.0
            i = 0
            while i < 255:
                zr2 = zr * zr
                zi2 = zi * zi
                if zr2 + zi2 > 4.0:
                    break
                zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
                i += 1
            total += i
    return total


# ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
def bench_sieve(n):
    comp = bytearray(n + 1)
    count = 0
    for i in range(2, n + 1):
        if not comp[i]:
            count += 1
            for j in range(i * i, n + 1, i):
                comp[j] = 1
    return count


# ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
def insertion_sort(a, lo, hi):
    for i in range(lo + 1, hi + 1):
        v = a[i]
        j = i - 1
        while j >= lo and a[j] > v:
            a[j + 1] = a[j]
            j -= 1
        a[j + 1] = v


def quicksort(a, lo, hi):
    while hi - lo > 16:
        mid = lo + (hi - lo) // 2
        # median-of-three: order a[lo] <= a[mid] <= a[hi]
        if a[mid] < a[lo]:
            a[mid], a[lo] = a[lo], a[mid]
        if a[hi] < a[lo]:
            a[hi], a[lo] = a[lo], a[hi]
        if a[hi] < a[mid]:
            a[hi], a[mid] = a[mid], a[hi]
        pivot = a[mid]

        i, j = lo, hi
        while i <= j:
            while a[i] < pivot:
                i += 1
            while a[j] > pivot:
                j -= 1
            if i <= j:
                a[i], a[j] = a[j], a[i]
                i += 1
                j -= 1
        # recurse into the smaller half, loop on the larger: depth stays O(log n)
        if j - lo < hi - i:
            quicksort(a, lo, j)
            lo = i
        else:
            quicksort(a, i, hi)
            hi = j
    insertion_sort(a, lo, hi)


def bench_quicksort(n):
    rng_seed(12345)
    a = [rng_next() for _ in range(n)]
    quicksort(a, 0, n - 1)
    h = 0
    for i in range(n):
        h = (h * 31 + a[i]) & MASK32  # order-sensitive checksum
    return h


# ---------- 4. word count: string hashing + hash map ----------
# Python's dict is written in C, so this benchmark is the one where the
# interpreter tax is smallest -- most of the real work happens below Python.
VOCAB = 5000


def bench_wordcount(n):
    rng_seed(12345)
    words = []
    for _ in range(VOCAB):
        length = 3 + rng_next() % 6
        words.append("".join(chr(97 + rng_next() % 26) for _ in range(length)))

    counts = {}
    maxc = 0
    for _ in range(n):
        ra = rng_next() % VOCAB
        rb = rng_next() % VOCAB
        w = words[(ra * rb) // VOCAB]  # triangular, so counts vary
        c = counts.get(w, 0) + 1
        counts[w] = c
        if c > maxc:
            maxc = c
    return len(counts) * 1000003 + maxc


# ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
# Nothing here computes; the entire cost is allocating little objects and
# reclaiming them. CPython pays at every release via refcounting, plus the
# cyclic GC sweeping through periodically.
def make_tree(d):
    if d == 0:
        return (None, None)
    return (make_tree(d - 1), make_tree(d - 1))


def check_tree(t):
    l, r = t
    if l is None:
        return 1
    return 1 + check_tree(l) + check_tree(r)


def bench_binarytrees(n):
    h = 0
    for k in range(n):
        t = make_tree(11)  # 4,095 nodes, built and thrown away
        h = (h * 31 + check_tree(t) + k) & MASK32
    return h


# ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
def bench_matmul(n):
    rng_seed(12345)
    a = [rng_next() % 100 for _ in range(n * n)]
    b = [rng_next() % 100 for _ in range(n * n)]
    c = [0] * (n * n)
    for i in range(n):
        ib = i * n
        for j in range(n):
            s = 0
            for k in range(n):
                s += a[ib + k] * b[k * n + j]
            c[ib + j] = s
    h = 0
    for v in c:
        h = (h * 31 + v) & MASK32
    return h


BENCHMARKS = {
    "mandelbrot": bench_mandelbrot,
    "sieve": bench_sieve,
    "quicksort": bench_quicksort,
    "wordcount": bench_wordcount,
    "binarytrees": bench_binarytrees,
    "matmul": bench_matmul,
}


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: bench.py <benchmark> <size> [reps]\n")
        return 2
    name = sys.argv[1]
    size = int(sys.argv[2])
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    reps = max(1, reps)

    fn = BENCHMARKS.get(name)
    if fn is None:
        sys.stderr.write("unknown benchmark: %s\n" % name)
        return 2

    # Best-of-N: the fastest run is the one least polluted by scheduler noise,
    # cold caches and (for the JIT languages) not-yet-compiled code.
    best = float("inf")
    result = None
    for r in range(reps):
        t0 = time.perf_counter()
        v = fn(size)
        elapsed = (time.perf_counter() - t0) * 1000.0
        best = min(best, elapsed)
        if r == 0:
            result = v
        elif v != result:
            sys.stderr.write("nondeterministic result!\n")
            return 3

    print("OK %s %d %.3f" % (name, result, best))
    return 0


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    sys.exit(main())
