/*
 * bench.c -- the C entry in the language speed comparison.
 *
 * Usage: bench <benchmark> <size>
 * Prints: OK <benchmark> <checksum> <compute_milliseconds>
 *
 * Every language in this suite runs the *same* algorithm on the *same*
 * deterministic input and prints the same checksum. If the checksums ever
 * disagree, the comparison is invalid and the runner will say so.
 */
#define _POSIX_C_SOURCE 199309L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

/* ---------- shared deterministic PRNG (identical in every language here) ---------- */
static uint64_t rng_state;
static void rng_seed(uint64_t s) { rng_state = s; }
static uint32_t rng_next(void) {
    rng_state = rng_state * 6364136223846793005ULL + 1442695040888963407ULL;
    return (uint32_t)(rng_state >> 33);          /* top 31 bits */
}

/* ---------- 1. mandelbrot: tight floating-point loop, zero allocation ---------- */
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

/* ---------- 2. sieve of eratosthenes: integer math over a large flat array ---------- */
static long long bench_sieve(int n) {
    unsigned char *comp = calloc((size_t)n + 1, 1);
    if (!comp) { fprintf(stderr, "out of memory\n"); exit(1); }
    long long count = 0;
    for (long long i = 2; i <= n; i++) {
        if (!comp[i]) {
            count++;
            for (long long j = i * i; j <= n; j += i) comp[j] = 1;
        }
    }
    free(comp);
    return count;
}

/* ---------- 3. quicksort: branches, swaps, recursion, random memory access ---------- */
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
        /* median-of-three: order a[lo] <= a[mid] <= a[hi] */
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
        /* recurse into the smaller half, loop on the larger: depth stays O(log n) */
        if (j - lo < hi - i) { quicksort(a, lo, j); lo = i; }
        else                 { quicksort(a, i, hi); hi = j; }
    }
    insertion_sort(a, lo, hi);
}

static long long bench_quicksort(int n) {
    uint32_t *a = malloc((size_t)n * sizeof(uint32_t));
    if (!a) { fprintf(stderr, "out of memory\n"); exit(1); }
    rng_seed(12345);
    for (int i = 0; i < n; i++) a[i] = rng_next();
    quicksort(a, 0, n - 1);
    uint32_t h = 0;
    for (int i = 0; i < n; i++) h = h * 31u + a[i];   /* order-sensitive checksum */
    free(a);
    return (long long)h;
}

/* ---------- 4. word count: string hashing + hash map, the "library work" case ----------
 * C has no built-in map, so here is the price of admission: an open-addressing
 * table written by hand. Go/Java/Python each get one line of code for this.
 */
#define VOCAB 5000
#define TBL_BITS 14
#define TBL_SIZE (1 << TBL_BITS)

typedef struct { const char *key; int count; } Slot;

static uint32_t fnv1a(const char *s) {
    uint32_t h = 2166136261u;
    while (*s) { h ^= (unsigned char)*s++; h *= 16777619u; }
    return h;
}

static long long bench_wordcount(int n) {
    static char words[VOCAB][16];
    rng_seed(12345);
    for (int i = 0; i < VOCAB; i++) {
        int len = 3 + (int)(rng_next() % 6);
        for (int c = 0; c < len; c++) words[i][c] = (char)('a' + rng_next() % 26);
        words[i][len] = '\0';
    }

    Slot *tbl = calloc(TBL_SIZE, sizeof(Slot));
    if (!tbl) { fprintf(stderr, "out of memory\n"); exit(1); }
    long long distinct = 0, maxc = 0;

    for (int k = 0; k < n; k++) {
        uint32_t ra = rng_next() % VOCAB;
        uint32_t rb = rng_next() % VOCAB;
        const char *w = words[(ra * rb) / VOCAB];      /* triangular, so counts vary */

        uint32_t idx = fnv1a(w) & (TBL_SIZE - 1);
        for (;;) {
            if (tbl[idx].key == NULL) {
                tbl[idx].key = w; tbl[idx].count = 1; distinct++;
                if (maxc < 1) maxc = 1;
                break;
            }
            if (strcmp(tbl[idx].key, w) == 0) {
                tbl[idx].count++;
                if (tbl[idx].count > maxc) maxc = tbl[idx].count;
                break;
            }
            idx = (idx + 1) & (TBL_SIZE - 1);
        }
    }
    free(tbl);
    return distinct * 1000003LL + maxc;
}

/* ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
 * Nothing here computes; the entire cost is malloc, free and following
 * pointers. The GC languages pay in bursts, C pays retail at every node.
 */
typedef struct Node { struct Node *l, *r; } Node;

static Node *make_tree(int d) {
    Node *t = malloc(sizeof(Node));
    if (!t) { fprintf(stderr, "out of memory\n"); exit(1); }
    if (d == 0) { t->l = t->r = NULL; }
    else { t->l = make_tree(d - 1); t->r = make_tree(d - 1); }
    return t;
}

static long long check_tree(const Node *t) {
    if (!t->l) return 1;
    return 1 + check_tree(t->l) + check_tree(t->r);
}

static void free_tree(Node *t) {
    if (t->l) { free_tree(t->l); free_tree(t->r); }
    free(t);
}

static long long bench_binarytrees(int n) {
    uint32_t h = 0;
    for (int k = 0; k < n; k++) {
        Node *t = make_tree(11);               /* 4,095 nodes, built and thrown away */
        h = h * 31u + (uint32_t)check_tree(t) + (uint32_t)k;
        free_tree(t);
    }
    return (long long)h;
}

/* ---------- 6. matmul: triple-nested loops over flat 2D arrays ---------- */
static long long bench_matmul(int n) {
    uint32_t *a = malloc((size_t)n * n * sizeof(uint32_t));
    uint32_t *b = malloc((size_t)n * n * sizeof(uint32_t));
    uint32_t *c = malloc((size_t)n * n * sizeof(uint32_t));
    if (!a || !b || !c) { fprintf(stderr, "out of memory\n"); exit(1); }
    rng_seed(12345);
    for (int i = 0; i < n * n; i++) a[i] = rng_next() % 100;
    for (int i = 0; i < n * n; i++) b[i] = rng_next() % 100;
    for (int i = 0; i < n; i++) {
        int ib = i * n;
        for (int j = 0; j < n; j++) {
            uint32_t s = 0;
            int bi = j;   /* walks b down the column; saves a multiply per step */
            for (int k = 0; k < n; k++) { s += a[ib + k] * b[bi]; bi += n; }
            c[ib + j] = s;
        }
    }
    uint32_t h = 0;
    for (int i = 0; i < n * n; i++) h = h * 31u + c[i];
    free(a); free(b); free(c);
    return (long long)h;
}

/* ---------- hidden: prng conformance check, not part of the scored suite ----------
 * run.py --selftest uses it to validate each language's PRNG directly; several
 * languages implement the 64-bit multiply in 32-bit halves and this checks every bit. */
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

    /* Best-of-N: the fastest run is the one least polluted by scheduler noise,
       cold caches and (for the JIT languages) not-yet-compiled code. */
    double best = 1e18;
    long long result = 0;
    for (int r = 0; r < reps; r++) {
        double t0 = now_ms();
        long long v = dispatch(name, size);
        double elapsed = now_ms() - t0;
        if (elapsed < best) best = elapsed;
        if (r == 0) result = v;
        else if (v != result) { fprintf(stderr, "nondeterministic result!\n"); return 3; }
    }

    printf("OK %s %lld %.3f\n", name, result, best);
    return 0;
}
