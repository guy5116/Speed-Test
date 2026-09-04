/*
 * bench.cs -- the C# entry in the language speed comparison.
 *
 * Usage: dotnet bench_cs.dll <benchmark> <size> [reps]
 * Prints: OK <benchmark> <checksum> <compute_milliseconds>
 *
 * Same algorithm, same deterministic input, same checksum as every other
 * bench.* in this suite.
 *
 * Plain C#, no NuGet packages, no unsafe. Unlike Java, C# has real unsigned
 * integers, so the shared PRNG and the quicksort's 32-bit values need no
 * sign gymnastics at all -- ulong and uint just do what bench.c's uint64_t
 * and uint32_t do. The runner compiles this with a generated csproj in
 * build/csharp and runs the resulting IL through the .NET JIT.
 */
using System;
using System.Collections.Generic;
using System.Diagnostics;

class Bench {

    // ---------- shared deterministic PRNG (identical in every language here) ----------
    static ulong rngState;

    static void RngSeed(ulong s) { rngState = s; }

    static uint RngNext() {
        rngState = rngState * 6364136223846793005UL + 1442695040888963407UL;
        return (uint)(rngState >> 33);   // top 31 bits
    }

    // ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
    static long BenchMandelbrot(int n) {
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
    static long BenchSieve(int n) {
        byte[] comp = new byte[n + 1];
        long count = 0;
        for (int i = 2; i <= n; i++) {
            if (comp[i] == 0) {
                count++;
                for (long j = (long)i * i; j <= n; j += i) comp[j] = 1;
            }
        }
        return count;
    }

    // ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
    // Hand-written, not Array.Sort -- the point is to measure the language,
    // not the quality of its sort library.
    static void InsertionSort(uint[] a, int lo, int hi) {
        for (int i = lo + 1; i <= hi; i++) {
            uint v = a[i];
            int j = i - 1;
            while (j >= lo && a[j] > v) { a[j + 1] = a[j]; j--; }
            a[j + 1] = v;
        }
    }

    static void Quicksort(uint[] a, int lo, int hi) {
        while (hi - lo > 16) {
            int mid = lo + (hi - lo) / 2;
            uint t;
            // median-of-three: order a[lo] <= a[mid] <= a[hi]
            if (a[mid] < a[lo])  { t = a[mid]; a[mid] = a[lo];  a[lo]  = t; }
            if (a[hi]  < a[lo])  { t = a[hi];  a[hi]  = a[lo];  a[lo]  = t; }
            if (a[hi]  < a[mid]) { t = a[hi];  a[hi]  = a[mid]; a[mid] = t; }
            uint pivot = a[mid];

            int i = lo, j = hi;
            while (i <= j) {
                while (a[i] < pivot) i++;
                while (a[j] > pivot) j--;
                if (i <= j) { t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
            }
            // recurse into the smaller half, loop on the larger: depth stays O(log n)
            if (j - lo < hi - i) { Quicksort(a, lo, j); lo = i; }
            else                 { Quicksort(a, i, hi); hi = j; }
        }
        InsertionSort(a, lo, hi);
    }

    static long BenchQuicksort(int n) {
        uint[] a = new uint[n];
        RngSeed(12345);
        for (int i = 0; i < n; i++) a[i] = RngNext();
        Quicksort(a, 0, n - 1);
        uint h = 0;
        for (int i = 0; i < n; i++) h = h * 31u + a[i];   // order-sensitive checksum
        return (long)h;
    }

    // ---------- 4. word count: string hashing + hash map ----------
    // The Dictionary is written in C# but JIT-compiled like everything else
    // in the runtime, so unlike the scripting languages there is no "drop
    // into C" discount here -- and no penalty either.
    const int VOCAB = 5000;

    static long BenchWordcount(int n) {
        string[] words = new string[VOCAB];
        RngSeed(12345);
        for (int i = 0; i < VOCAB; i++) {
            int len = 3 + (int)(RngNext() % 6);
            char[] buf = new char[len];
            for (int c = 0; c < len; c++) buf[c] = (char)('a' + RngNext() % 26);
            words[i] = new string(buf);
        }

        var counts = new Dictionary<string, int>();
        long maxc = 0;
        for (int k = 0; k < n; k++) {
            int ra = (int)(RngNext() % VOCAB);
            int rb = (int)(RngNext() % VOCAB);
            string w = words[(int)((long)ra * rb / VOCAB)];  // triangular, so counts vary
            counts.TryGetValue(w, out int c);
            c++;
            counts[w] = c;
            if (c > maxc) maxc = c;
        }
        return (long)counts.Count * 1000003L + maxc;
    }

    // ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
    // Nothing here computes; the entire cost is allocating nodes and letting
    // the GC take them back. Like the JVM, the .NET runtime has a bump
    // allocator and a generational collector, so this row is a home game.
    sealed class Node {
        public Node l, r;
        public Node(Node l, Node r) { this.l = l; this.r = r; }
    }

    static Node MakeTree(int d) {
        if (d == 0) return new Node(null, null);
        return new Node(MakeTree(d - 1), MakeTree(d - 1));
    }

    static long CheckTree(Node t) {
        if (t.l == null) return 1;
        return 1 + CheckTree(t.l) + CheckTree(t.r);
    }

    static long BenchBinarytrees(int n) {
        uint h = 0;
        for (int k = 0; k < n; k++) {
            Node t = MakeTree(11);             // 4,095 nodes, built and thrown away
            h = h * 31u + (uint)CheckTree(t) + (uint)k;
        }
        return (long)h;
    }

    // ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
    static long BenchMatmul(int n) {
        uint[] a = new uint[n * n];
        uint[] b = new uint[n * n];
        uint[] c = new uint[n * n];
        RngSeed(12345);
        for (int i = 0; i < n * n; i++) a[i] = RngNext() % 100;
        for (int i = 0; i < n * n; i++) b[i] = RngNext() % 100;
        for (int i = 0; i < n; i++) {
            int ib = i * n;
            for (int j = 0; j < n; j++) {
                uint s = 0;
                for (int k = 0; k < n; k++) s += a[ib + k] * b[k * n + j];
                c[ib + j] = s;
            }
        }
        uint h = 0;
        for (int i = 0; i < n * n; i++) h = h * 31u + c[i];
        return (long)h;
    }

    static long Run(string name, int size) {
        switch (name) {
            case "mandelbrot":  return BenchMandelbrot(size);
            case "sieve":       return BenchSieve(size);
            case "quicksort":   return BenchQuicksort(size);
            case "wordcount":   return BenchWordcount(size);
            case "binarytrees": return BenchBinarytrees(size);
            case "matmul":      return BenchMatmul(size);
            default:
                Console.Error.WriteLine("unknown benchmark: " + name);
                Environment.Exit(2);
                return 0;
        }
    }

    static int Main(string[] args) {
        if (args.Length < 2) {
            Console.Error.WriteLine("usage: bench_cs <benchmark> <size> [reps]");
            return 2;
        }
        string name = args[0];
        int size = int.Parse(args[1]);
        int reps = (args.Length > 2) ? int.Parse(args[2]) : 1;
        if (reps < 1) reps = 1;

        // Best-of-N: the fastest run is the one least polluted by scheduler noise,
        // cold caches and (for the JIT languages) not-yet-compiled code.
        double best = double.PositiveInfinity;
        long result = 0;
        for (int r = 0; r < reps; r++) {
            long t0 = Stopwatch.GetTimestamp();
            long v = Run(name, size);
            double elapsed = (Stopwatch.GetTimestamp() - t0) * 1000.0 / Stopwatch.Frequency;
            if (elapsed < best) best = elapsed;
            if (r == 0) result = v;
            else if (v != result) {
                Console.Error.WriteLine("nondeterministic result!");
                return 3;
            }
        }

        // InvariantCulture so the milliseconds always print with a '.', whatever
        // the machine's locale thinks a decimal separator looks like.
        Console.WriteLine(string.Format(System.Globalization.CultureInfo.InvariantCulture,
                                        "OK {0} {1} {2:F3}", name, result, best));
        return 0;
    }
}
