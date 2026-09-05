/*
 * Bench.java -- the Java entry in the language speed comparison.
 *
 * Usage: java Bench <benchmark> <size>
 * Prints: OK <benchmark> <checksum> <compute_milliseconds>
 *
 * Same algorithm, same deterministic input, same checksum as every other
 * bench.* in this suite.
 */
import java.util.HashMap;

public class Bench {

    // ---------- shared deterministic PRNG (identical in every language here) ----------
    static long rngState;

    static void rngSeed(long s) { rngState = s; }

    static int rngNext() {
        rngState = rngState * 6364136223846793005L + 1442695040888963407L;
        return (int) (rngState >>> 33);   // top 31 bits
    }

    // ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
    static long benchMandelbrot(int n) {
        long total = 0;
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
    static long benchSieve(int n) {
        byte[] comp = new byte[n + 1];
        long count = 0;
        for (int i = 2; i <= n; i++) {
            if (comp[i] == 0) {
                count++;
                // i*i is computed in 64-bit (it overflows int near the top),
                // but the marking loop runs on int so C2 keeps its range
                // checks out of the loop body
                long start = (long) i * i;
                if (start <= n)
                    for (int j = (int) start; j <= n; j += i) comp[j] = 1;
            }
        }
        return count;
    }

    // ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
    static void insertionSort(int[] a, int lo, int hi) {
        for (int i = lo + 1; i <= hi; i++) {
            int v = a[i];
            int j = i - 1;
            while (j >= lo && a[j] > v) { a[j + 1] = a[j]; j--; }
            a[j + 1] = v;
        }
    }

    /* Java has no unsigned int, but it doesn't need one here: rngNext()
       returns the state's top 31 bits, so every value is non-negative and
       signed compare orders them exactly like C's unsigned compare. */

    static void quicksort(int[] a, int lo, int hi) {
        while (hi - lo > 16) {
            int mid = lo + (hi - lo) / 2;
            int t;
            // median-of-three: order a[lo] <= a[mid] <= a[hi]
            if (a[mid] < a[lo])  { t = a[mid]; a[mid] = a[lo];  a[lo]  = t; }
            if (a[hi]  < a[lo])  { t = a[hi];  a[hi]  = a[lo];  a[lo]  = t; }
            if (a[hi]  < a[mid]) { t = a[hi];  a[hi]  = a[mid]; a[mid] = t; }
            int pivot = a[mid];

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
        insertionSort(a, lo, hi);
    }

    static long benchQuicksort(int n) {
        int[] a = new int[n];
        rngSeed(12345);
        for (int i = 0; i < n; i++) a[i] = rngNext();
        quicksort(a, 0, n - 1);
        int h = 0;
        for (int i = 0; i < n; i++) h = h * 31 + a[i];   // order-sensitive checksum
        return Integer.toUnsignedLong(h);
    }

    // ---------- 4. word count: string hashing + hash map ----------
    static final int VOCAB = 5000;

    static long benchWordcount(int n) {
        String[] words = new String[VOCAB];
        rngSeed(12345);
        for (int i = 0; i < VOCAB; i++) {
            int len = 3 + rngNext() % 6;
            char[] buf = new char[len];
            for (int c = 0; c < len; c++) buf[c] = (char) ('a' + rngNext() % 26);
            words[i] = new String(buf);
        }

        // int[] slots instead of Integer values: merge() would autobox a
        // fresh Integer on nearly every increment (the cache stops at 127);
        // a mutable slot is one lookup and zero allocation on the hot path
        HashMap<String, int[]> counts = new HashMap<>(8192);
        long maxc = 0;
        for (int k = 0; k < n; k++) {
            int ra = rngNext() % VOCAB;
            int rb = rngNext() % VOCAB;
            String w = words[ra * rb / VOCAB];  // triangular, so counts vary; fits int
            int[] slot = counts.get(w);
            if (slot == null) counts.put(w, slot = new int[1]);
            int c = ++slot[0];
            if (c > maxc) maxc = c;
        }
        return (long) counts.size() * 1000003L + maxc;
    }

    // ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
    // Nothing here computes; the entire cost is allocating nodes and letting
    // the GC take them back. Object allocation is the thing the JVM is most
    // aggressively engineered for, so this row is Java's home game.
    static final class Node {
        Node l, r;
        Node(Node l, Node r) { this.l = l; this.r = r; }
    }

    static Node makeTree(int d) {
        if (d == 0) return new Node(null, null);
        return new Node(makeTree(d - 1), makeTree(d - 1));
    }

    static long checkTree(Node t) {
        if (t.l == null) return 1;
        return 1 + checkTree(t.l) + checkTree(t.r);
    }

    static long benchBinarytrees(int n) {
        int h = 0;
        for (int k = 0; k < n; k++) {
            Node t = makeTree(11);             // 4,095 nodes, built and thrown away
            h = h * 31 + (int) checkTree(t) + k;
        }
        return Integer.toUnsignedLong(h);
    }

    // ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
    static long benchMatmul(int n) {
        int[] a = new int[n * n];
        int[] b = new int[n * n];
        int[] c = new int[n * n];
        rngSeed(12345);
        for (int i = 0; i < n * n; i++) a[i] = rngNext() % 100;
        for (int i = 0; i < n * n; i++) b[i] = rngNext() % 100;
        for (int i = 0; i < n; i++) {
            int ib = i * n;
            for (int j = 0; j < n; j++) {
                int s = 0;
                int bi = j;   // walks b down the column; saves a multiply per step
                for (int k = 0; k < n; k++) { s += a[ib + k] * b[bi]; bi += n; }
                c[ib + j] = s;
            }
        }
        int h = 0;
        for (int i = 0; i < n * n; i++) h = h * 31 + c[i];
        return Integer.toUnsignedLong(h);
    }

    // ---------- hidden: prng conformance check, not part of the scored suite ----------
    // run.py --selftest uses it to validate each language's PRNG directly; several
    // languages implement the 64-bit multiply in 32-bit halves and this checks every bit.
    static long benchPrng(int n) {
        rngSeed(12345);
        int h = 0;
        for (int i = 0; i < n; i++) h = h * 31 + rngNext();
        return Integer.toUnsignedLong(h);
    }

    public static void main(String[] args) {
        if (args.length < 2) {
            System.err.println("usage: java Bench <benchmark> <size> [reps]");
            System.exit(2);
        }
        String name = args[0];
        int size = Integer.parseInt(args[1]);
        int reps = (args.length > 2) ? Integer.parseInt(args[2]) : 1;
        if (reps < 1) reps = 1;
        int warmup = (args.length > 3) ? Integer.parseInt(args[3]) : 0;

        // Untimed warm-up (run.py --warmup): lets C2 reach steady state
        // before the clock starts, the JMH treatment. Deterministic work,
        // so the results are simply discarded.
        for (int w = 0; w < warmup; w++) run(name, size);

        // Best-of-N: the fastest run is the one least polluted by scheduler noise,
        // cold caches and (for the JIT languages) not-yet-compiled code.
        double best = Double.POSITIVE_INFINITY;
        long result = 0;
        for (int r = 0; r < reps; r++) {
            long t0 = System.nanoTime();
            long v = run(name, size);
            double elapsed = (System.nanoTime() - t0) / 1e6;
            if (elapsed < best) best = elapsed;
            if (r == 0) result = v;
            else if (v != result) {
                System.err.println("nondeterministic result!");
                System.exit(3);
            }
        }

        System.out.printf("OK %s %d %.3f%n", name, result, best);
    }

    static long run(String name, int size) {
        switch (name) {
            case "mandelbrot": return benchMandelbrot(size);
            case "sieve":      return benchSieve(size);
            case "quicksort":  return benchQuicksort(size);
            case "wordcount":  return benchWordcount(size);
            case "binarytrees": return benchBinarytrees(size);
            case "matmul":     return benchMatmul(size);
            case "prng":       return benchPrng(size);
            default:
                System.err.println("unknown benchmark: " + name);
                System.exit(2);
                return 0;
        }
    }
}
