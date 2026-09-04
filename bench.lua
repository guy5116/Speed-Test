#!/usr/bin/env lua
--[[
bench.lua -- the Lua entry in the language speed comparison.

Usage: lua bench.lua <benchmark> <size> [reps]
Prints: OK <benchmark> <checksum> <compute_milliseconds>

Same algorithm, same deterministic input, same checksum as every other
bench.* in this suite.

Requires Lua 5.3 or newer: the shared PRNG needs a real 64-bit integer that
wraps on overflow, and Lua only grew one in 5.3. On 5.1/5.2/LuaJIT every
number is a double and the generator would silently produce different bits --
which the runner would catch as a checksum mismatch, but better to say so.

Plain reference Lua, no LuaJIT, no C modules. LuaJIT would change these
numbers enormously and is a different measurement; see the README.
--]]

-- ---------- shared deterministic PRNG (identical in every language here) ----------
local rng_state = 0

local function rng_seed(s)
  rng_state = s
end

local function rng_next()
  -- Lua 5.3+ integer arithmetic wraps on overflow, exactly like the uint64
  -- in the other languages. `>>` is a logical shift, so the sign bit does
  -- not leak in.
  rng_state = rng_state * 6364136223846793005 + 1442695040888963407
  return rng_state >> 33   -- top 31 bits
end

-- ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
local function bench_mandelbrot(n)
  local total = 0
  for py = 0, n - 1 do
    local ci = 2.0 * py / n - 1.0
    for px = 0, n - 1 do
      local cr = 2.0 * px / n - 1.5
      local zr, zi = 0.0, 0.0
      local i = 0
      while i < 255 do
        local zr2, zi2 = zr * zr, zi * zi
        if zr2 + zi2 > 4.0 then break end
        zi = 2.0 * zr * zi + ci
        zr = zr2 - zi2 + cr
        i = i + 1
      end
      total = total + i
    end
  end
  return total
end

-- ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
-- Lua has no byte array, so this is a table with one integer per index: about
-- 16 bytes per entry where C spends 1. That is a real property of the
-- language and it is why this benchmark wants ~1 GB of RAM at the standard
-- scale. A bitset would be cheaper, but it would also be doing eight times
-- less memory traffic than everyone else on the one benchmark that exists to
-- measure memory traffic, so it would not be the same test.
local function bench_sieve(n)
  local comp = {}
  for i = 2, n do comp[i] = 0 end
  local count = 0
  for i = 2, n do
    if comp[i] == 0 then
      count = count + 1
      for j = i * i, n, i do comp[j] = 1 end
    end
  end
  return count
end

-- ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
-- Hand-written, not table.sort -- the point is to measure the language, not
-- the quality of its sort library. Indices are 1-based here; the algorithm is
-- identical and a fully sorted array checksums the same either way.
local function insertion_sort(a, lo, hi)
  for i = lo + 1, hi do
    local v = a[i]
    local j = i - 1
    while j >= lo and a[j] > v do
      a[j + 1] = a[j]
      j = j - 1
    end
    a[j + 1] = v
  end
end

local function quicksort(a, lo, hi)
  while hi - lo > 16 do
    local mid = lo + (hi - lo) // 2
    -- median-of-three: order a[lo] <= a[mid] <= a[hi]
    if a[mid] < a[lo] then a[mid], a[lo] = a[lo], a[mid] end
    if a[hi] < a[lo] then a[hi], a[lo] = a[lo], a[hi] end
    if a[hi] < a[mid] then a[hi], a[mid] = a[mid], a[hi] end
    local pivot = a[mid]

    local i, j = lo, hi
    while i <= j do
      while a[i] < pivot do i = i + 1 end
      while a[j] > pivot do j = j - 1 end
      if i <= j then
        a[i], a[j] = a[j], a[i]
        i = i + 1
        j = j - 1
      end
    end
    -- recurse into the smaller half, loop on the larger: depth stays O(log n)
    if j - lo < hi - i then
      quicksort(a, lo, j)
      lo = i
    else
      quicksort(a, i, hi)
      hi = j
    end
  end
  insertion_sort(a, lo, hi)
end

local function bench_quicksort(n)
  rng_seed(12345)
  local a = {}
  for i = 1, n do a[i] = rng_next() end
  quicksort(a, 1, n)
  local h = 0
  for i = 1, n do
    h = (h * 31 + a[i]) & 0xFFFFFFFF   -- order-sensitive checksum
  end
  return h
end

-- ---------- 4. word count: string hashing + hash map ----------
-- The table is Lua's only data structure, and it is a hash map written in C,
-- so this is the benchmark where Lua gives up the least.
local VOCAB = 5000

local function bench_wordcount(n)
  rng_seed(12345)
  local words = {}
  local buf = {}
  for i = 1, VOCAB do
    local len = 3 + rng_next() % 6
    for c = 1, len do buf[c] = string.char(97 + rng_next() % 26) end
    words[i] = table.concat(buf, "", 1, len)
  end

  local counts = {}
  local distinct, maxc = 0, 0
  for _ = 1, n do
    local ra = rng_next() % VOCAB
    local rb = rng_next() % VOCAB
    local w = words[(ra * rb) // VOCAB + 1]   -- triangular, so counts vary
    local c = counts[w]
    if c == nil then
      c = 1
      distinct = distinct + 1
    else
      c = c + 1
    end
    counts[w] = c
    if c > maxc then maxc = c end
  end
  return distinct * 1000003 + maxc
end

-- ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
-- Nothing here computes; the entire cost is allocating little tables and
-- letting the GC take them back.
local function make_tree(d)
  if d == 0 then return { l = false, r = false } end
  return { l = make_tree(d - 1), r = make_tree(d - 1) }
end

local function check_tree(t)
  if not t.l then return 1 end
  return 1 + check_tree(t.l) + check_tree(t.r)
end

local function bench_binarytrees(n)
  local h = 0
  for k = 0, n - 1 do
    local t = make_tree(11)   -- 4,095 nodes, built and thrown away
    h = (h * 31 + check_tree(t) + k) & 0xFFFFFFFF
  end
  return h
end

-- ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
local function bench_matmul(n)
  rng_seed(12345)
  local a, b, c = {}, {}, {}
  for i = 1, n * n do a[i] = rng_next() % 100 end
  for i = 1, n * n do b[i] = rng_next() % 100 end
  for i = 0, n - 1 do
    local ib = i * n
    for j = 1, n do
      local s = 0
      for k = 1, n do
        s = s + a[ib + k] * b[(k - 1) * n + j]
      end
      c[ib + j] = s
    end
  end
  local h = 0
  for i = 1, n * n do
    h = (h * 31 + c[i]) & 0xFFFFFFFF
  end
  return h
end

local BENCHMARKS = {
  mandelbrot = bench_mandelbrot,
  sieve = bench_sieve,
  quicksort = bench_quicksort,
  wordcount = bench_wordcount,
  binarytrees = bench_binarytrees,
  matmul = bench_matmul,
}

local function main()
  local name = arg[1]
  local size = tonumber(arg[2])
  if name == nil or size == nil then
    io.stderr:write("usage: lua bench.lua <benchmark> <size> [reps]\n")
    return 2
  end
  size = math.tointeger(size) or 0
  local reps = math.tointeger(tonumber(arg[3]) or 1) or 1
  if reps < 1 then reps = 1 end

  local fn = BENCHMARKS[name]
  if fn == nil then
    io.stderr:write("unknown benchmark: " .. name .. "\n")
    return 2
  end

  -- Best-of-N: the fastest run is the one least polluted by scheduler noise,
  -- cold caches and (for the JIT languages) not-yet-compiled code.
  -- os.clock() is Lua's only sub-second timer; it reports CPU rather than wall
  -- time, which for a single-threaded compute loop is the same number.
  local best = math.huge
  local result = nil
  for r = 1, reps do
    local t0 = os.clock()
    local v = fn(size)
    local elapsed = (os.clock() - t0) * 1000.0
    if elapsed < best then best = elapsed end
    if r == 1 then
      result = v
    elseif v ~= result then
      io.stderr:write("nondeterministic result!\n")
      return 3
    end
  end

  io.write(string.format("OK %s %d %.3f\n", name, result, best))
  return 0
end

os.exit(main())
