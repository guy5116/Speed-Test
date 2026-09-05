#!/usr/bin/env ruby
# bench.rb -- the Ruby entry in the language speed comparison.
#
# Usage: ruby bench.rb <benchmark> <size> [reps]
# Prints: OK <benchmark> <checksum> <compute_milliseconds>
#
# Same algorithm, same deterministic input, same checksum as every other
# bench.* in this suite.
#
# Plain CRuby, no gems; the runner turns on YJIT when the build has it.
# Ruby's integers go arbitrary-precision past 2^62, so the shared PRNG's
# full-width 64-bit multiply would run in Bignum -- three heap allocations
# per call, and a YJIT side-exit on every one. The state therefore lives in
# two 32-bit halves and the multiply runs in 16-bit limbs, every intermediate
# comfortably Fixnum -- the same trick bench.js and bench.php use, produced
# by the same constraint. The sieve uses a String as its byte array
# (getbyte/setbyte): a Ruby Array would work, but at 8+ bytes per entry it is
# a different data structure, not a slower one -- the same reasoning as
# JavaScript's typed arrays and Perl's vec().

MASK32 = (1 << 32) - 1

# ---------- shared deterministic PRNG (identical in every language here) ----------
# A = 0x5851F42D_4C957F2D, C = 0x14057B7E_F767814F, state = hi:lo.

$rng_lo = 0
$rng_hi = 0

def rng_seed(s)
  $rng_lo = s & 0xFFFFFFFF
  $rng_hi = (s >> 32) & 0xFFFFFFFF
end

def rng_next
  lo = $rng_lo
  hi = $rng_hi
  p0 = lo * 0x7F2D                       # lo * A_lo, 16 bits at a time
  p1 = lo * 0x4C95
  t = p0 + ((p1 & 0xFFFF) << 16)
  carry = (t >> 32) + (p1 >> 16)
  new_lo = t & 0xFFFFFFFF
  new_hi = (carry +
            lo * 0xF42D + (((lo * 0x5851) & 0xFFFF) << 16) +  # + lo * A_hi
            hi * 0x7F2D + (((hi * 0x4C95) & 0xFFFF) << 16))   # + hi * A_lo
  t = new_lo + 0xF767814F                # + C, with carry into the high word
  $rng_lo = t & 0xFFFFFFFF
  $rng_hi = (new_hi + 0x14057B7E + (t >> 32)) & 0xFFFFFFFF
  $rng_hi >> 1                           # the state's top 31 bits
end

# ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
def bench_mandelbrot(n)
  total = 0
  py = 0
  while py < n
    ci = 2.0 * py / n - 1.0
    px = 0
    while px < n
      cr = 2.0 * px / n - 1.5
      zr = 0.0
      zi = 0.0
      i = 0
      while i < 255
        zr2 = zr * zr
        zi2 = zi * zi
        break if zr2 + zi2 > 4.0
        zi = 2.0 * zr * zi + ci
        zr = zr2 - zi2 + cr
        i += 1
      end
      total += i
      px += 1
    end
    py += 1
  end
  total
end

# ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
def bench_sieve(n)
  comp = "\0".b * (n + 1) # one byte per candidate, like everyone else
  count = 0
  i = 2
  while i <= n
    if comp.getbyte(i) == 0
      count += 1
      j = i * i
      while j <= n
        comp.setbyte(j, 1)
        j += i
      end
    end
    i += 1
  end
  count
end

# ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
# Hand-written, not Array#sort! -- the point is to measure the language, not
# the quality of its sort library.
def insertion_sort(a, lo, hi)
  i = lo + 1
  while i <= hi
    v = a[i]
    j = i - 1
    while j >= lo && a[j] > v
      a[j + 1] = a[j]
      j -= 1
    end
    a[j + 1] = v
    i += 1
  end
end

def quicksort(a, lo, hi)
  while hi - lo > 16
    mid = lo + ((hi - lo) >> 1)
    # median-of-three: order a[lo] <= a[mid] <= a[hi]
    a[mid], a[lo] = a[lo], a[mid] if a[mid] < a[lo]
    a[hi], a[lo] = a[lo], a[hi] if a[hi] < a[lo]
    a[hi], a[mid] = a[mid], a[hi] if a[hi] < a[mid]
    pivot = a[mid]

    i = lo
    j = hi
    while i <= j
      i += 1 while a[i] < pivot
      j -= 1 while a[j] > pivot
      if i <= j
        a[i], a[j] = a[j], a[i]
        i += 1
        j -= 1
      end
    end
    # recurse into the smaller half, loop on the larger: depth stays O(log n)
    if j - lo < hi - i
      quicksort(a, lo, j)
      lo = i
    else
      quicksort(a, i, hi)
      hi = j
    end
  end
  insertion_sort(a, lo, hi)
end

def bench_quicksort(n)
  rng_seed(12345)
  a = Array.new(n, 0)
  k = 0
  while k < n # while, not a block: no 4M yields, no captured locals
    a[k] = rng_next
    k += 1
  end
  quicksort(a, 0, n - 1)
  h = 0
  k = 0
  while k < n
    h = (h * 31 + a[k]) & MASK32 # order-sensitive checksum
    k += 1
  end
  h
end

# ---------- 4. word count: string hashing + hash map ----------
# Ruby's Hash is written in C, so this is the benchmark where the
# interpreter tax is smallest.
VOCAB = 5000

def bench_wordcount(n)
  rng_seed(12345)
  words = Array.new(VOCAB) do
    len = 3 + rng_next % 6
    w = +""
    len.times { w << (97 + rng_next % 26) }
    w
  end

  counts = Hash.new(0)
  maxc = 0
  k = 0
  while k < n # while, not n.times: the block would heap-allocate the env
    ra = rng_next % VOCAB
    rb = rng_next % VOCAB
    w = words[(ra * rb) / VOCAB] # triangular, so counts vary
    c = (counts[w] += 1)
    maxc = c if c > maxc
    k += 1
  end
  counts.size * 1000003 + maxc
end

# ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
# Nothing here computes; the entire cost is allocating nodes and letting the
# GC take them back.
def make_tree(d)
  return [nil, nil] if d == 0
  [make_tree(d - 1), make_tree(d - 1)]
end

def check_tree(t)
  l = t[0]
  return 1 if l.nil?
  1 + check_tree(l) + check_tree(t[1])
end

def bench_binarytrees(n)
  h = 0
  k = 0
  while k < n
    t = make_tree(11) # 4,095 nodes, built and thrown away
    h = (h * 31 + check_tree(t) + k) & MASK32
    k += 1
  end
  h
end

# ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
def bench_matmul(n)
  rng_seed(12345)
  nn = n * n
  a = Array.new(nn, 0)
  b = Array.new(nn, 0)
  c = Array.new(nn, 0)
  k = 0
  while k < nn
    a[k] = rng_next % 100
    k += 1
  end
  k = 0
  while k < nn
    b[k] = rng_next % 100
    k += 1
  end
  i = 0
  while i < n
    ib = i * n
    j = 0
    while j < n
      s = 0
      k = 0
      bi = j # walks b down the column; saves a multiply per inner step
      while k < n
        s += a[ib + k] * b[bi]
        bi += n
        k += 1
      end
      c[ib + j] = s
      j += 1
    end
    i += 1
  end
  h = 0
  k = 0
  while k < nn
    h = (h * 31 + c[k]) & MASK32
    k += 1
  end
  h
end

# ---------- prng: hidden conformance check, not part of the scored suite ----------
# run.py --selftest uses it to validate the PRNG bit-for-bit -- this is the
# direct test of the 16-bit-limb multiply in rng_next. A plain call per step
# is the point: correctness, not speed.
def bench_prng(n)
  rng_seed(12345)
  h = 0
  i = 0
  while i < n
    h = (h * 31 + rng_next) & MASK32
    i += 1
  end
  h
end

BENCHMARKS = {
  "mandelbrot" => method(:bench_mandelbrot),
  "sieve" => method(:bench_sieve),
  "quicksort" => method(:bench_quicksort),
  "wordcount" => method(:bench_wordcount),
  "binarytrees" => method(:bench_binarytrees),
  "matmul" => method(:bench_matmul),
  "prng" => method(:bench_prng), # hidden: PRNG conformance, see above
}.freeze

def main
  if ARGV.length < 2
    warn "usage: ruby bench.rb <benchmark> <size> [reps]"
    return 2
  end
  name = ARGV[0]
  size = ARGV[1].to_i
  reps = ARGV.length > 2 ? ARGV[2].to_i : 1
  reps = 1 if reps < 1
  warmup = ARGV.length > 3 ? ARGV[3].to_i : 0
  warmup = 0 if warmup < 0

  fn = BENCHMARKS[name]
  if fn.nil?
    warn "unknown benchmark: #{name}"
    return 2
  end

  # Untimed warm-up (run.py --warmup): lets YJIT reach steady state before
  # the clock starts. Results discarded.
  warmup.times { fn.call(size) }

  # Best-of-N: the fastest run is the one least polluted by scheduler noise,
  # cold caches and (for the JIT languages) not-yet-compiled code.
  best = Float::INFINITY
  result = nil
  reps.times do |r|
    t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    v = fn.call(size)
    elapsed = (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0) * 1000.0
    best = elapsed if elapsed < best
    if r == 0
      result = v
    elsif v != result
      warn "nondeterministic result!"
      return 3
    end
  end

  printf("OK %s %d %.3f\n", name, result, best)
  0
end

exit main
