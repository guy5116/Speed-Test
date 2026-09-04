<?php
# bench.php -- the PHP entry in the language speed comparison.
#
# Usage: php bench.php <benchmark> <size> [reps]
# Prints: OK <benchmark> <checksum> <compute_milliseconds>
#
# Same algorithm, same deterministic input, same checksum as every other
# bench.* in this suite.
#
# Plain PHP 7.3+, no extensions required -- though the runner switches on
# opcache's tracing JIT when it is present. Two judgement calls worth
# knowing about: PHP integers are signed 64-bit and *overflow to float*
# instead of wrapping, so the shared PRNG's 64-bit multiply is done in
# 32-bit halves whose products stay safely inside 63 bits; and the sieve's
# flat byte array is a string poked one byte at a time, because a PHP array
# of 50 million ints costs ~16 bytes per entry where C spends 1 -- the
# mutable string is PHP's honest equivalent of a byte array, the way vec()
# is Perl's and typed arrays are JavaScript's.

# The CLI SAPI documents memory_limit as -1, but distro php.ini files (Nix,
# Debian) often cap it at 128M, which kills the sieve and quicksort at
# --heavy scale before they can print. Restore the documented CLI default.
ini_set('memory_limit', '-1');

# ---------- shared deterministic PRNG (identical in every language here) ----------
# State is one u64 held as two 32-bit halves. The multiplier splits as
# 0x5851F42D_4C957F2D and the increment as 0x14057B7E_F767814F; every partial
# product below is < 2^63, so nothing ever silently becomes a float.
$rng_lo = 0;
$rng_hi = 0;

function rng_seed($s) {
    global $rng_lo, $rng_hi;
    $rng_lo = $s & 0xFFFFFFFF;
    $rng_hi = ($s >> 32) & 0xFFFFFFFF;
}

function rng_next() {
    global $rng_lo, $rng_hi;
    $p  = $rng_lo * 0x4C957F2D;
    $lo = ($p & 0xFFFFFFFF) + 0xF767814F;
    $hi = ($p >> 32)
        + (($rng_lo * 0x5851F42D) & 0xFFFFFFFF)
        + (($rng_hi * 0x4C957F2D) & 0xFFFFFFFF)
        + 0x14057B7E + ($lo >> 32);
    $rng_lo = $lo & 0xFFFFFFFF;
    $rng_hi = $hi & 0xFFFFFFFF;
    return $rng_hi >> 1;    # top 31 bits of the 64-bit state
}

# ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
function bench_mandelbrot($n) {
    $total = 0;
    for ($py = 0; $py < $n; $py++) {
        $ci = 2.0 * $py / $n - 1.0;
        for ($px = 0; $px < $n; $px++) {
            $cr = 2.0 * $px / $n - 1.5;
            $zr = 0.0;
            $zi = 0.0;
            $i = 0;
            while ($i < 255) {
                $zr2 = $zr * $zr;
                $zi2 = $zi * $zi;
                if ($zr2 + $zi2 > 4.0) break;
                $zi = 2.0 * $zr * $zi + $ci;
                $zr = $zr2 - $zi2 + $cr;
                $i++;
            }
            $total += $i;
        }
    }
    return $total;
}

# ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
function bench_sieve($n) {
    $comp = str_repeat("\0", $n + 1);    # one byte per candidate, like everyone else
    $count = 0;
    for ($i = 2; $i <= $n; $i++) {
        if ($comp[$i] === "\0") {
            $count++;
            for ($j = $i * $i; $j <= $n; $j += $i) {
                $comp[$j] = "\1";
            }
        }
    }
    return $count;
}

# ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
# Hand-written, not sort() -- the point is to measure the language, not the
# quality of its sort library.
function insertion_sort(&$a, $lo, $hi) {
    for ($i = $lo + 1; $i <= $hi; $i++) {
        $v = $a[$i];
        $j = $i - 1;
        while ($j >= $lo && $a[$j] > $v) {
            $a[$j + 1] = $a[$j];
            $j--;
        }
        $a[$j + 1] = $v;
    }
}

function quicksort(&$a, $lo, $hi) {
    while ($hi - $lo > 16) {
        $mid = $lo + (($hi - $lo) >> 1);
        # median-of-three: order a[lo] <= a[mid] <= a[hi]
        if ($a[$mid] < $a[$lo]) { $t = $a[$mid]; $a[$mid] = $a[$lo];  $a[$lo]  = $t; }
        if ($a[$hi]  < $a[$lo]) { $t = $a[$hi];  $a[$hi]  = $a[$lo];  $a[$lo]  = $t; }
        if ($a[$hi] < $a[$mid]) { $t = $a[$hi];  $a[$hi]  = $a[$mid]; $a[$mid] = $t; }
        $pivot = $a[$mid];

        $i = $lo;
        $j = $hi;
        while ($i <= $j) {
            while ($a[$i] < $pivot) $i++;
            while ($a[$j] > $pivot) $j--;
            if ($i <= $j) {
                $t = $a[$i]; $a[$i] = $a[$j]; $a[$j] = $t;
                $i++;
                $j--;
            }
        }
        # recurse into the smaller half, loop on the larger: depth stays O(log n)
        if ($j - $lo < $hi - $i) { quicksort($a, $lo, $j); $lo = $i; }
        else                     { quicksort($a, $i, $hi); $hi = $j; }
    }
    insertion_sort($a, $lo, $hi);
}

function bench_quicksort($n) {
    global $rng_lo, $rng_hi;
    rng_seed(12345);
    # The PRNG body is pasted inline with the state in plain locals: `global`
    # binds by reference, and every read through a reference zval blunts the
    # JIT's type inference. Same expansion in wordcount and matmul.
    $slo = $rng_lo;
    $shi = $rng_hi;
    $a = array_fill(0, $n, 0);    # preallocated: no rehash mid-fill
    for ($i = 0; $i < $n; $i++) {
        $p   = $slo * 0x4C957F2D;
        $t   = ($p & 0xFFFFFFFF) + 0xF767814F;
        $shi = (($p >> 32)
             + (($slo * 0x5851F42D) & 0xFFFFFFFF)
             + (($shi * 0x4C957F2D) & 0xFFFFFFFF)
             + 0x14057B7E + ($t >> 32)) & 0xFFFFFFFF;
        $slo = $t & 0xFFFFFFFF;
        $a[$i] = $shi >> 1;
    }
    $rng_lo = $slo;
    $rng_hi = $shi;
    quicksort($a, 0, $n - 1);
    $h = 0;
    foreach ($a as $v) {
        $h = ($h * 31 + $v) & 0xFFFFFFFF;    # order-sensitive checksum
    }
    return $h;
}

# ---------- 4. word count: string hashing + hash map ----------
# The associative array is PHP's native data structure and it is written in
# C, so this is the benchmark where PHP gives up the least.
const VOCAB = 5000;

function bench_wordcount($n) {
    rng_seed(12345);
    $words = [];
    for ($i = 0; $i < VOCAB; $i++) {
        $len = 3 + rng_next() % 6;
        $w = '';
        for ($c = 0; $c < $len; $c++) $w .= chr(97 + rng_next() % 26);
        $words[] = $w;
    }

    global $rng_lo, $rng_hi;
    $slo = $rng_lo;    # PRNG inlined; see bench_quicksort
    $shi = $rng_hi;
    $counts = [];
    $maxc = 0;
    for ($k = 0; $k < $n; $k++) {
        $p   = $slo * 0x4C957F2D;
        $t   = ($p & 0xFFFFFFFF) + 0xF767814F;
        $shi = (($p >> 32)
             + (($slo * 0x5851F42D) & 0xFFFFFFFF)
             + (($shi * 0x4C957F2D) & 0xFFFFFFFF)
             + 0x14057B7E + ($t >> 32)) & 0xFFFFFFFF;
        $slo = $t & 0xFFFFFFFF;
        $ra = ($shi >> 1) % VOCAB;
        $p   = $slo * 0x4C957F2D;
        $t   = ($p & 0xFFFFFFFF) + 0xF767814F;
        $shi = (($p >> 32)
             + (($slo * 0x5851F42D) & 0xFFFFFFFF)
             + (($shi * 0x4C957F2D) & 0xFFFFFFFF)
             + 0x14057B7E + ($t >> 32)) & 0xFFFFFFFF;
        $slo = $t & 0xFFFFFFFF;
        $rb = ($shi >> 1) % VOCAB;
        # (int) of the float division equals intdiv here -- $ra*$rb < 2.5e7,
        # doubles are exact that low -- and it skips a real function call
        $w = $words[(int)($ra * $rb / VOCAB)];    # triangular, so counts vary
        $c = ($counts[$w] ?? 0) + 1;
        $counts[$w] = $c;
        if ($c > $maxc) $maxc = $c;
    }
    $rng_lo = $slo;
    $rng_hi = $shi;
    return count($counts) * 1000003 + $maxc;
}

# ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
# Nothing here computes; the entire cost is allocating nodes and letting the
# refcounter take them back. Like Perl, PHP frees by reference counting, so
# the teardown cost lands right here in the loop when $t is reassigned.
class Node {
    public $l;
    public $r;
    function __construct($l, $r) { $this->l = $l; $this->r = $r; }
}

function make_tree($d) {
    if ($d === 0) return new Node(null, null);
    return new Node(make_tree($d - 1), make_tree($d - 1));
}

function check_tree($t) {
    if ($t->l === null) return 1;
    return 1 + check_tree($t->l) + check_tree($t->r);
}

function bench_binarytrees($n) {
    $h = 0;
    for ($k = 0; $k < $n; $k++) {
        $t = make_tree(11);    # 4,095 nodes, built and thrown away
        $h = ($h * 31 + check_tree($t) + $k) & 0xFFFFFFFF;
    }
    return $h;
}

# ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
function bench_matmul($n) {
    global $rng_lo, $rng_hi;
    rng_seed(12345);
    $nn = $n * $n;
    $slo = $rng_lo;    # PRNG inlined; see bench_quicksort
    $shi = $rng_hi;
    $a = array_fill(0, $nn, 0);
    $b = array_fill(0, $nn, 0);
    for ($i = 0; $i < $nn; $i++) {
        $p   = $slo * 0x4C957F2D;
        $t   = ($p & 0xFFFFFFFF) + 0xF767814F;
        $shi = (($p >> 32)
             + (($slo * 0x5851F42D) & 0xFFFFFFFF)
             + (($shi * 0x4C957F2D) & 0xFFFFFFFF)
             + 0x14057B7E + ($t >> 32)) & 0xFFFFFFFF;
        $slo = $t & 0xFFFFFFFF;
        $a[$i] = ($shi >> 1) % 100;
    }
    for ($i = 0; $i < $nn; $i++) {
        $p   = $slo * 0x4C957F2D;
        $t   = ($p & 0xFFFFFFFF) + 0xF767814F;
        $shi = (($p >> 32)
             + (($slo * 0x5851F42D) & 0xFFFFFFFF)
             + (($shi * 0x4C957F2D) & 0xFFFFFFFF)
             + 0x14057B7E + ($t >> 32)) & 0xFFFFFFFF;
        $slo = $t & 0xFFFFFFFF;
        $b[$i] = ($shi >> 1) % 100;
    }
    $rng_lo = $slo;
    $rng_hi = $shi;
    $c = array_fill(0, $nn, 0);
    for ($i = 0; $i < $n; $i++) {
        $ib = $i * $n;
        for ($j = 0; $j < $n; $j++) {
            $s = 0;
            $bi = $j;    # walks b down the column; saves a multiply per step
            for ($k = 0; $k < $n; $k++) {
                $s += $a[$ib + $k] * $b[$bi];
                $bi += $n;
            }
            $c[$ib + $j] = $s;
        }
    }
    $h = 0;
    for ($i = 0; $i < $nn; $i++) {
        $h = ($h * 31 + $c[$i]) & 0xFFFFFFFF;
    }
    return $h;
}

# ---------- main ----------
const BENCHMARKS = [
    'mandelbrot'  => 'bench_mandelbrot',
    'sieve'       => 'bench_sieve',
    'quicksort'   => 'bench_quicksort',
    'wordcount'   => 'bench_wordcount',
    'binarytrees' => 'bench_binarytrees',
    'matmul'      => 'bench_matmul',
];

function main($argv) {
    if (count($argv) < 3) {
        fwrite(STDERR, "usage: php bench.php <benchmark> <size> [reps]\n");
        return 2;
    }
    $name = $argv[1];
    $size = (int)$argv[2];
    $reps = isset($argv[3]) ? (int)$argv[3] : 1;
    if ($reps < 1) $reps = 1;

    if (!isset(BENCHMARKS[$name])) {
        fwrite(STDERR, "unknown benchmark: $name\n");
        return 2;
    }
    $fn = BENCHMARKS[$name];

    # Best-of-N: the fastest run is the one least polluted by scheduler noise,
    # cold caches and (for the JIT languages) not-yet-compiled code.
    $best = INF;
    $result = null;
    for ($r = 0; $r < $reps; $r++) {
        $t0 = hrtime(true);
        $v = $fn($size);
        $elapsed = (hrtime(true) - $t0) / 1e6;
        if ($elapsed < $best) $best = $elapsed;
        if ($r === 0) {
            $result = $v;
        } elseif ($v !== $result) {
            fwrite(STDERR, "nondeterministic result!\n");
            return 3;
        }
    }

    printf("OK %s %d %.3f\n", $name, $result, $best);
    return 0;
}

exit(main($argv));
