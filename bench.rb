#!/usr/bin/env ruby
# bench.rb -- the Ruby entry in the language speed comparison.
#
# Usage: ruby bench.rb <benchmark> <size> [reps]
# Prints: OK <benchmark> <checksum> <compute_milliseconds>
#
# Same algorithm, same deterministic input, same checksum as every other
# bench.* in this suite.
#
# Plain CRuby, no gems, no YJIT flag -- whatever `ruby` does by default is
# what gets measured, the same deal Python and Lua get. Like Python, Ruby
# has arbitrary-precision integers, so the shared 64-bit PRNG is a multiply
# and a mask rather than native wraparound. The sieve uses a String as its
# byte array (getbyte/setbyte): a Ruby Array would work, but at 8+ bytes per
# entry it is a different data structure, not a slower one -- the same
# reasoning as JavaScript's typed arrays and Perl's vec().

MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1

# ---------- shared deterministic PRNG (identical in every language here) ----------
$rng_state = 0

def rng_seed(s)
  $rng_state = s
end

def rng_next
  $rng_state = ($rng_state * 6364136223846793005 + 1442695040888963407) & MASK64
  $rng_state >> 33 # top 31 bits
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
  a = Array.new(n) { rng_next }
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
  n.times do
    ra = rng_next % VOCAB
    rb = rng_next % VOCAB
    w = words[(ra * rb) / VOCAB] # triangular, so counts vary
    c = (counts[w] += 1)
    maxc = c if c > maxc
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
  a = Array.new(n * n) { rng_next % 100 }
  b = Array.new(n * n) { rng_next % 100 }
  c = Array.new(n * n, 0)
  i = 0
  while i < n
    ib = i * n
    j = 0
    while j < n
      s = 0
      k = 0
      while k < n
        s += a[ib + k] * b[k * n + j]
        k += 1
      end
      c[ib + j] = s
      j += 1
    end
    i += 1
  end
  h = 0
  c.each { |v| h = (h * 31 + v) & MASK32 }
  h
end

BENCHMARKS = {
  "mandelbrot" => method(:bench_mandelbrot),
  "sieve" => method(:bench_sieve),
  "quicksort" => method(:bench_quicksort),
  "wordcount" => method(:bench_wordcount),
  "binarytrees" => method(:bench_binarytrees),
  "matmul" => method(:bench_matmul),
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

  fn = BENCHMARKS[name]
  if fn.nil?
    warn "unknown benchmark: #{name}"
    return 2
  end

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
