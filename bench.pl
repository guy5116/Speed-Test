#!/usr/bin/env perl
# bench.pl -- the Perl entry in the language speed comparison.
#
# Usage: perl bench.pl <benchmark> <size> [reps]
# Prints: OK <benchmark> <checksum> <compute_milliseconds>
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
use Time::HiRes qw(time);

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
    for my $py (0 .. $n - 1) {
        my $ci = 2.0 * $py / $n - 1.0;
        for my $px (0 .. $n - 1) {
            my $cr = 2.0 * $px / $n - 1.5;
            my ($zr, $zi) = (0.0, 0.0);
            my $i = 0;
            while ($i < 255) {
                my $zr2 = $zr * $zr;
                my $zi2 = $zi * $zi;
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
                vec($comp, $j, 8) = 1;
            }
        }
    }
    return $count;
}

# ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
# Hand-written, not sort() -- the point is to measure the language, not the
# quality of its sort library.
sub insertion_sort {
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
    my ($arr, $lo, $hi) = @_;
    while ($hi - $lo > 16) {
        my $mid = $lo + (($hi - $lo) >> 1);
        # median-of-three: order a[lo] <= a[mid] <= a[hi]
        @$arr[$mid, $lo] = @$arr[$lo, $mid] if $arr->[$mid] < $arr->[$lo];
        @$arr[$hi,  $lo] = @$arr[$lo, $hi]  if $arr->[$hi]  < $arr->[$lo];
        @$arr[$hi, $mid] = @$arr[$mid, $hi] if $arr->[$hi]  < $arr->[$mid];
        my $pivot = $arr->[$mid];

        my ($i, $j) = ($lo, $hi);
        while ($i <= $j) {
            $i++ while $arr->[$i] < $pivot;
            $j-- while $arr->[$j] > $pivot;
            if ($i <= $j) {
                @$arr[$i, $j] = @$arr[$j, $i];
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
    my @a = map { rng_next() } 1 .. $n;
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
    for (1 .. $n) {
        my $ra = rng_next() % VOCAB;
        my $rb = rng_next() % VOCAB;
        my $w = $words[int($ra * $rb / VOCAB)];    # triangular, so counts vary
        my $c = ++$counts{$w};
        $maxc = $c if $c > $maxc;
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
    my (@a, @b);
    $a[$_] = rng_next() % 100 for 0 .. $n * $n - 1;
    $b[$_] = rng_next() % 100 for 0 .. $n * $n - 1;
    my @c;
    for my $i (0 .. $n - 1) {
        my $ib = $i * $n;
        for my $j (0 .. $n - 1) {
            my $s = 0;
            for my $k (0 .. $n - 1) {
                $s += $a[$ib + $k] * $b[$k * $n + $j];
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

# ---------- main ----------
my %BENCHMARKS = (
    mandelbrot  => \&bench_mandelbrot,
    sieve       => \&bench_sieve,
    quicksort   => \&bench_quicksort,
    wordcount   => \&bench_wordcount,
    binarytrees => \&bench_binarytrees,
    matmul      => \&bench_matmul,
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
    # cold caches and (for the JIT languages) not-yet-compiled code.
    my $best = 9**99;
    my $result;
    for my $r (0 .. $reps - 1) {
        my $t0 = time();
        my $v = $fn->($size);
        my $elapsed = (time() - $t0) * 1000.0;
        $best = $elapsed if $elapsed < $best;
        if ($r == 0) {
            $result = $v;
        }
        elsif ($v != $result) {
            print STDERR "nondeterministic result!\n";
            return 3;
        }
    }

    printf "OK %s %d %.3f\n", $name, $result, $best;
    return 0;
}

exit main();
