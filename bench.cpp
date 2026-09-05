/*
 * bench.cpp -- the C++ entry in the language speed comparison.
 *
 * Usage: bench <benchmark> <size> [reps]
 * Prints: OK <benchmark> <checksum> <best_ms> <median_ms> <worst_ms>
 *
 * Every language in this suite runs the *same* algorithm on the *same*
 * deterministic input and prints the same checksum. If the checksums ever
 * disagree, the comparison is invalid and the runner will say so.
 *
 * The interesting difference from bench.c is benchmark 4: C++ has
 * std::unordered_map, so it does not pay C's forty lines of hand-rolled
 * hash table. Whether it pays for that in speed instead is the question.
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <algorithm>
#include <chrono>
#include <memory>
#include <string>
#include <vector>
#include <unordered_map>

static double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

// ---------- shared deterministic PRNG (identical in every language here) ----------
static uint64_t rng_state;
static void rng_seed(uint64_t s) { rng_state = s; }
static uint32_t rng_next() {
    rng_state = rng_state * 6364136223846793005ULL + 1442695040888963407ULL;
    return (uint32_t)(rng_state >> 33);          // top 31 bits
}

// ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
static long long bench_mandelbrot(int n) {
    long long total = 0;
    for (int py = 0; py < n; py++) {
        double ci = 2.0 * py / n - 1.0;
        for (int px = 0; px < n; px++) {
            double cr = 2.0 * px / n - 1.5;
            double zr = 0.0, zi = 0.0;
            int i = 0;
            while (i < 255) {
                double zr2 = zr * zr, zi2 = zi * zi;
                if (zr2 + zi2 > 4.0) break;
                zi = 2.0 * zr * zi + ci;
                zr = zr2 - zi2 + cr;
                i++;
            }
            total += i;
        }
    }
    return total;
}

// ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
static long long bench_sieve(int n) {
    std::vector<unsigned char> comp((size_t)n + 1, 0);
    long long count = 0;
    for (long long i = 2; i <= n; i++) {
        if (!comp[i]) {
            count++;
            for (long long j = i * i; j <= n; j += i) comp[j] = 1;
        }
    }
    return count;
}

// ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
// Hand-written, not std::sort -- the point is to measure the language, not the
// quality of its sort library. (std::sort would win; that is a different test.)
static void insertion_sort(uint32_t *a, int lo, int hi) {
    for (int i = lo + 1; i <= hi; i++) {
        uint32_t v = a[i];
        int j = i - 1;
        while (j >= lo && a[j] > v) { a[j + 1] = a[j]; j--; }
        a[j + 1] = v;
    }
}

static void quicksort(uint32_t *a, int lo, int hi) {
    while (hi - lo > 16) {
        int mid = lo + (hi - lo) / 2;
        uint32_t t;
        // median-of-three: order a[lo] <= a[mid] <= a[hi]
        if (a[mid] < a[lo])  { t = a[mid]; a[mid] = a[lo];  a[lo]  = t; }
        if (a[hi]  < a[lo])  { t = a[hi];  a[hi]  = a[lo];  a[lo]  = t; }
        if (a[hi]  < a[mid]) { t = a[hi];  a[hi]  = a[mid]; a[mid] = t; }
        uint32_t pivot = a[mid];

        int i = lo, j = hi;
        while (i <= j) {
            while (a[i] < pivot) i++;
            while (a[j] > pivot) j--;
            if (i <= j) { t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
        }
        // recurse into the smaller half, loop on the larger: depth stays O(log n)
        if (j - lo < hi - i) { quicksort(a, lo, j); lo = i; }
        else                 { quicksort(a, i, hi); hi = j; }
    }
    insertion_sort(a, lo, hi);
}

static long long bench_quicksort(int n) {
    std::vector<uint32_t> a((size_t)n);
    rng_seed(12345);
    for (int i = 0; i < n; i++) a[i] = rng_next();
    quicksort(a.data(), 0, n - 1);
    uint32_t h = 0;
    for (int i = 0; i < n; i++) h = h * 31u + a[i];   // order-sensitive checksum
    return (long long)h;
}

// ---------- 4. word count: string hashing + hash map ----------
// One line of std::unordered_map does what bench.c spends forty lines on.
static const int VOCAB = 5000;

static long long bench_wordcount(int n) {
    std::vector<std::string> words(VOCAB);
    rng_seed(12345);
    for (int i = 0; i < VOCAB; i++) {
        int len = 3 + (int)(rng_next() % 6);
        std::string w(len, ' ');
        for (int c = 0; c < len; c++) w[c] = (char)('a' + rng_next() % 26);
        words[i] = std::move(w);
    }

    std::unordered_map<std::string, int> counts;
    counts.reserve(8192);   // sized like every other language's map here
    long long maxc = 0;
    for (int k = 0; k < n; k++) {
        uint32_t ra = rng_next() % VOCAB;
        uint32_t rb = rng_next() % VOCAB;
        const std::string &w = words[(ra * rb) / VOCAB];   // triangular, so counts vary
        int c = ++counts[w];
        if (c > maxc) maxc = c;
    }
    return (long long)counts.size() * 1000003LL + maxc;
}

// ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
// unique_ptr instead of bench.c's malloc/free: the destructor tears the tree
// down recursively, so the freeing cost is still paid inside the loop --
// C++'s ownership machinery, priced against C's by-hand version.
struct Node {
    std::unique_ptr<Node> l, r;
};

static std::unique_ptr<Node> make_tree(int d) {
    auto t = std::make_unique<Node>();
    if (d > 0) {
        t->l = make_tree(d - 1);
        t->r = make_tree(d - 1);
    }
    return t;
}

static long long check_tree(const Node *t) {
    if (!t->l) return 1;
    return 1 + check_tree(t->l.get()) + check_tree(t->r.get());
}

static long long bench_binarytrees(int n) {
    uint32_t h = 0;
    for (int k = 0; k < n; k++) {
        auto t = make_tree(11);                // 4,095 nodes, built and thrown away
        h = h * 31u + (uint32_t)check_tree(t.get()) + (uint32_t)k;
    }                                          // ~t frees the whole tree here
    return (long long)h;
}

// ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
static long long bench_matmul(int n) {
    std::vector<uint32_t> a((size_t)n * n), b((size_t)n * n), c((size_t)n * n);
    rng_seed(12345);
    for (int i = 0; i < n * n; i++) a[i] = rng_next() % 100;
    for (int i = 0; i < n * n; i++) b[i] = rng_next() % 100;
    for (int i = 0; i < n; i++) {
        int ib = i * n;
        for (int j = 0; j < n; j++) {
            uint32_t s = 0;
            int bi = j;   // walks b down the column; saves a multiply per step
            for (int k = 0; k < n; k++) { s += a[ib + k] * b[bi]; bi += n; }
            c[ib + j] = s;
        }
    }
    uint32_t h = 0;
    for (int i = 0; i < n * n; i++) h = h * 31u + c[i];
    return (long long)h;
}

// ---------- hidden: prng conformance check, not part of the scored suite ----------
// run.py --selftest uses it to validate each language's PRNG directly; several
// languages implement the 64-bit multiply in 32-bit halves and this checks every bit.
static long long bench_prng(int n) {
    rng_seed(12345);
    uint32_t h = 0;
    for (int i = 0; i < n; i++) h = h * 31u + rng_next();
    return (long long)h;
}

static long long dispatch(const char *name, int size) {
    if (!strcmp(name, "mandelbrot"))  return bench_mandelbrot(size);
    if (!strcmp(name, "sieve"))       return bench_sieve(size);
    if (!strcmp(name, "quicksort"))   return bench_quicksort(size);
    if (!strcmp(name, "wordcount"))   return bench_wordcount(size);
    if (!strcmp(name, "binarytrees")) return bench_binarytrees(size);
    if (!strcmp(name, "matmul"))      return bench_matmul(size);
    if (!strcmp(name, "prng"))        return bench_prng(size);
    fprintf(stderr, "unknown benchmark: %s\n", name);
    exit(2);
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <benchmark> <size> [reps]\n", argv[0]); return 2; }
    const char *name = argv[1];
    int size = atoi(argv[2]);
    int reps = (argc > 3) ? atoi(argv[3]) : 1;
    if (reps < 1) reps = 1;

    // Best-of-N: the fastest run is the one least polluted by scheduler noise,
    // cold caches and (for the JIT languages) not-yet-compiled code. Median
    // and worst ride along so the runner can report the spread.
    std::vector<double> times;
    times.reserve(reps);
    long long result = 0;
    for (int r = 0; r < reps; r++) {
        double t0 = now_ms();
        long long v = dispatch(name, size);
        times.push_back(now_ms() - t0);
        if (r == 0) result = v;
        else if (v != result) { fprintf(stderr, "nondeterministic result!\n"); return 3; }
    }

    std::sort(times.begin(), times.end());
    printf("OK %s %lld %.3f %.3f %.3f\n", name, result,
           times[0], times[reps / 2], times[reps - 1]);
    return 0;
}
