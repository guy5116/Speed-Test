#!/usr/bin/env perl
# bench.pl -- the Perl entry in the language speed comparison.
#
# Usage: perl bench.pl <benchmark> <size> [reps]
# Prints: OK <benchmark> <checksum> <best_ms> <median_ms> <worst_ms>
#
# Same algorithm, same deterministic input, same checksum as every other
# bench.* in this suite.
#
# Plain Perl 5, core modules only. Two judgement calls worth knowing about:
# the shared PRNG needs 64-bit arithmetic that wraps, which is what
# `use integer` provides (native IVs on a 64-bit perl), scoped to that one
# sub so the floating-point benchmarks are untouched; and the sieve's flat
# byte array is a string poked with vec(), because a 50-million-element Perl
# array costs ~50 bytes per entry where C spends 1 -- vec() is Perl's honest
# equivalent of a byte array, the way typed arrays are JavaScript's.
use strict;
use warnings;
use Time::HiRes qw(clock_gettime CLOCK_MONOTONIC);

# ---------- shared deterministic PRNG (identical in every language here) ----------
my $rng_state = 0;

sub rng_seed { $rng_state = $_[0]; }

sub rng_next {
    # Native 64-bit integer arithmetic, which wraps on overflow exactly like
    # the uint64 in the other languages. Under `use integer` the >> is an
    # arithmetic shift, so mask back down to the 31 bits everyone returns.
    use integer;
    $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
    return ($rng_state >> 33) & 0x7FFFFFFF;    # top 31 bits
}

# ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
sub bench_mandelbrot {
    my ($n) = @_;
    my $total = 0;
    # every lexical hoisted out of the loops: perl allocates a fresh pad
    # entry per `my` per iteration scope, ~11% of this benchmark
    my ($ci, $cr, $zr, $zi, $zr2, $zi2, $i);
    for my $py (0 .. $n - 1) {
        $ci = 2.0 * $py / $n - 1.0;
        for my $px (0 .. $n - 1) {
            $cr = 2.0 * $px / $n - 1.5;
            $zr = 0.0;
            $zi = 0.0;
            $i  = 0;
            while ($i < 255) {
                $zr2 = $zr * $zr;
                $zi2 = $zi * $zi;
                last if $zr2 + $zi2 > 4.0;
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
sub bench_sieve {
    my ($n) = @_;
    my $comp = "\0" x ($n + 1);    # one byte per candidate, like everyone else
    my $count = 0;
    for my $i (2 .. $n) {
        if (!vec($comp, $i, 8)) {
            $count++;
            for (my $j = $i * $i; $j <= $n; $j += $i) {
                # 4-arg substr, not lvalue vec: assigning through vec()
                # builds a magic SV and dispatches through magic_setvec on
                # every store -- measured at ~2x the whole benchmark. The
                # *read* above stays vec(), which measures faster than
                # substr eq; stores outnumber reads here, reads win above.
                substr($comp, $j, 1, "\1");
            }
        }
    }
    return $count;
}

# ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
# Hand-written, not sort() -- the point is to measure the language, not the
# quality of its sort library.
sub insertion_sort {
    use integer;
    my ($arr, $lo, $hi) = @_;
    for my $i ($lo + 1 .. $hi) {
        my $v = $arr->[$i];
        my $j = $i - 1;
        while ($j >= $lo && $arr->[$j] > $v) {
            $arr->[$j + 1] = $arr->[$j];
            $j--;
        }
        $arr->[$j + 1] = $v;
    }
}

sub quicksort {
    use integer;
    my ($arr, $lo, $hi) = @_;
    my $t;
    while ($hi - $lo > 16) {
        my $mid = $lo + (($hi - $lo) >> 1);
        # median-of-three: order a[lo] <= a[mid] <= a[hi]. Plain temp swaps:
        # the slice-swap idiom builds two 2-element lists per swap.
        if ($arr->[$mid] < $arr->[$lo]) { $t = $arr->[$mid]; $arr->[$mid] = $arr->[$lo]; $arr->[$lo] = $t; }
        if ($arr->[$hi] < $arr->[$lo])  { $t = $arr->[$hi];  $arr->[$hi]  = $arr->[$lo]; $arr->[$lo] = $t; }
        if ($arr->[$hi] < $arr->[$mid]) { $t = $arr->[$hi];  $arr->[$hi]  = $arr->[$mid]; $arr->[$mid] = $t; }
        my $pivot = $arr->[$mid];

        my ($i, $j) = ($lo, $hi);
        while ($i <= $j) {
            $i++ while $arr->[$i] < $pivot;
            $j-- while $arr->[$j] > $pivot;
            if ($i <= $j) {
                $t = $arr->[$i];
                $arr->[$i] = $arr->[$j];
                $arr->[$j] = $t;
                $i++;
                $j--;
            }
        }
        # recurse into the smaller half, loop on the larger: depth stays O(log n)
        if ($j - $lo < $hi - $i) { quicksort($arr, $lo, $j); $lo = $i; }
        else                     { quicksort($arr, $i, $hi); $hi = $j; }
    }
    insertion_sort($arr, $lo, $hi);
}

sub bench_quicksort {
    my ($n) = @_;
    rng_seed(12345);
    use integer;
    # the PRNG body is pasted into the map block: perl has no inliner, and a
    # sub call around two arithmetic ops measured ~25% of the whole fill
    my @a = map {
        $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
        ($rng_state >> 33) & 0x7FFFFFFF;
    } 1 .. $n;
    quicksort(\@a, 0, $n - 1);
    my $h = 0;
    for my $v (@a) {
        $h = ($h * 31 + $v) & 0xFFFFFFFF;    # order-sensitive checksum
    }
    return $h;
}

# ---------- 4. word count: string hashing + hash map ----------
# The hash is the data structure Perl was built around, and it is written in
# C -- so this is the benchmark where Perl gives up the least.
use constant VOCAB => 5000;

sub bench_wordcount {
    my ($n) = @_;
    rng_seed(12345);
    my @words;
    for my $i (0 .. VOCAB - 1) {
        my $len = 3 + rng_next() % 6;
        my $w = '';
        $w .= chr(97 + rng_next() % 26) for 1 .. $len;
        push @words, $w;
    }

    my %counts;
    my $maxc = 0;
    {
        # `use integer` makes / truncating division (no int() op, no FP
        # round-trip), and the PRNG is pasted inline -- see bench_quicksort
        use integer;
        my ($ra, $rb, $c);
        for (1 .. $n) {
            $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
            $ra = (($rng_state >> 33) & 0x7FFFFFFF) % VOCAB;
            $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
            $rb = (($rng_state >> 33) & 0x7FFFFFFF) % VOCAB;
            my $w = $words[$ra * $rb / VOCAB];    # triangular, so counts vary
            $c = ++$counts{$w};
            $maxc = $c if $c > $maxc;
        }
    }
    return (scalar keys %counts) * 1000003 + $maxc;
}

# ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
# Perl frees by reference counting, so the teardown cost lands right here in
# the loop when $t is reassigned -- not in a later GC pause.
sub make_tree {
    my ($d) = @_;
    return [undef, undef] if $d == 0;
    return [make_tree($d - 1), make_tree($d - 1)];
}

sub check_tree {
    my ($t) = @_;
    return 1 unless defined $t->[0];
    return 1 + check_tree($t->[0]) + check_tree($t->[1]);
}

sub bench_binarytrees {
    my ($n) = @_;
    my $h = 0;
    for my $k (0 .. $n - 1) {
        my $t = make_tree(11);    # 4,095 nodes, built and thrown away
        $h = ($h * 31 + check_tree($t) + $k) & 0xFFFFFFFF;
    }
    return $h;
}

# ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
sub bench_matmul {
    my ($n) = @_;
    rng_seed(12345);
    use integer;    # everything here is integer math; skip the FP round-trips
    my (@a, @b);
    for my $t (0 .. $n * $n - 1) {
        $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
        $a[$t] = (($rng_state >> 33) & 0x7FFFFFFF) % 100;
    }
    for my $t (0 .. $n * $n - 1) {
        $rng_state = $rng_state * 6364136223846793005 + 1442695040888963407;
        $b[$t] = (($rng_state >> 33) & 0x7FFFFFFF) % 100;
    }
    my @c;
    for my $i (0 .. $n - 1) {
        my $ib = $i * $n;
        for my $j (0 .. $n - 1) {
            my $s  = 0;
            my $bi = $j;    # walks b down the column; saves a multiply per step
            for my $k (0 .. $n - 1) {
                $s += $a[$ib + $k] * $b[$bi];
                $bi += $n;
            }
            $c[$ib + $j] = $s;
        }
    }
    my $h = 0;
    for my $v (@c) {
        $h = ($h * 31 + $v) & 0xFFFFFFFF;
    }
    return $h;
}

# ---------- prng: hidden conformance check, not part of the scored suite ----------
# run.py --selftest uses it to validate the PRNG bit-for-bit. A plain call
# per step is the point: correctness, not speed.
sub bench_prng {
    my ($n) = @_;
    rng_seed(12345);
    my $h = 0;
    {
        use integer;
        for (1 .. $n) {
            $h = ($h * 31 + rng_next()) & 0xFFFFFFFF;
        }
    }
    return $h;
}

# ---------- main ----------
my %BENCHMARKS = (
    mandelbrot  => \&bench_mandelbrot,
    sieve       => \&bench_sieve,
    quicksort   => \&bench_quicksort,
    wordcount   => \&bench_wordcount,
    binarytrees => \&bench_binarytrees,
    matmul      => \&bench_matmul,
    prng        => \&bench_prng,    # hidden: PRNG conformance, see above
);

sub main {
    if (@ARGV < 2) {
        print STDERR "usage: perl bench.pl <benchmark> <size> [reps]\n";
        return 2;
    }
    my ($name, $size, $reps) = @ARGV;
    $size = int($size);
    $reps = int($reps // 1);
    $reps = 1 if $reps < 1;

    my $fn = $BENCHMARKS{$name};
    if (!$fn) {
        print STDERR "unknown benchmark: $name\n";
        return 2;
    }

    # Best-of-N: the fastest run is the one least polluted by scheduler noise,
    # cold caches and (for the JIT languages) not-yet-compiled code. Median
    # and worst ride along so the runner can report the spread.
    my @times;
    my $result;
    for my $r (0 .. $reps - 1) {
        # CLOCK_MONOTONIC, not time(): wall-clock jumps if NTP steps the clock
        my $t0 = clock_gettime(CLOCK_MONOTONIC);
        my $v = $fn->($size);
        push @times, (clock_gettime(CLOCK_MONOTONIC) - $t0) * 1000.0;
        if ($r == 0) {
            $result = $v;
        }
        elsif ($v != $result) {
            print STDERR "nondeterministic result!\n";
            return 3;
        }
    }

    @times = sort { $a <=> $b } @times;
    printf "OK %s %d %.3f %.3f %.3f\n", $name, $result,
        $times[0], $times[int($reps / 2)], $times[-1];
    return 0;
}

exit main();
