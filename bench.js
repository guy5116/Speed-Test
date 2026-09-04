#!/usr/bin/env node
/*
 * bench.js -- the JavaScript entry in the language speed comparison.
 *
 * Usage: node bench.js <benchmark> <size> [reps]
 * Prints: OK <benchmark> <checksum> <compute_milliseconds>
 *
 * Same algorithm, same deterministic input, same checksum as every other
 * bench.* in this suite.
 *
 * Plain JavaScript on the default V8 configuration -- no WASM, no native
 * addons, no flags. Typed arrays are used where the other languages use a
 * flat array of a fixed width, because that is the honest equivalent: a
 * plain JS array of numbers is a different data structure, not a slower one.
 */
'use strict';

// ---------- shared deterministic PRNG (identical in every language here) ----------
// The others do `state = state * 6364136223846793005 + 1442695040888963407`
// in one 64-bit multiply. JavaScript has no 64-bit integer that is fast --
// BigInt would be correct and roughly an order of magnitude slower -- so the
// multiply is done by hand in 32-bit halves. This is the price of admission
// for JS the way the hand-rolled hash table is the price of admission for C.
const MUL_HI = 0x5851f42d, MUL_LO = 0x4c957f2d;   // 6364136223846793005
const ADD_HI = 0x14057b7e, ADD_LO = 0xf767814f;   // 1442695040888963407

let stateHi = 0, stateLo = 0;

function rngSeed(s) {
  stateHi = Math.floor(s / 4294967296) >>> 0;
  stateLo = (s >>> 0);
}

function rngNext() {
  const aLo = stateLo, aHi = stateHi;
  // 32x32 -> 64 of the low halves, via 16-bit limbs
  const a0 = aLo & 0xffff, a1 = aLo >>> 16;
  const b0 = MUL_LO & 0xffff, b1 = MUL_LO >>> 16;
  const p0 = a0 * b0;
  const p1 = a1 * b0 + (p0 >>> 16);
  let carry = p1 >>> 16;
  const p1b = (p1 & 0xffff) + a0 * b1;
  carry += p1b >>> 16;
  const mulLo = (((p1b & 0xffff) << 16) | (p0 & 0xffff)) >>> 0;
  // high half: carry + a1*b1 + the two cross terms that only affect bits 32+
  const mulHi = (carry + a1 * b1 + Math.imul(aLo, MUL_HI) + Math.imul(aHi, MUL_LO)) >>> 0;

  const lo = (mulLo + ADD_LO) >>> 0;
  stateLo = lo;
  stateHi = (mulHi + ADD_HI + (lo < mulLo ? 1 : 0)) >>> 0;
  return stateHi >>> 1;   // (state >> 33) is just the high word shifted once
}

// ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
function benchMandelbrot(n) {
  let total = 0;
  for (let py = 0; py < n; py++) {
    const ci = 2.0 * py / n - 1.0;
    for (let px = 0; px < n; px++) {
      const cr = 2.0 * px / n - 1.5;
      let zr = 0.0, zi = 0.0;
      let i = 0;
      while (i < 255) {
        const zr2 = zr * zr, zi2 = zi * zi;
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
function benchSieve(n) {
  const comp = new Uint8Array(n + 1);
  let count = 0;
  for (let i = 2; i <= n; i++) {
    if (comp[i] === 0) {
      count++;
      for (let j = i * i; j <= n; j += i) comp[j] = 1;
    }
  }
  return count;
}

// ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
// Hand-written, not Array.prototype.sort -- the point is to measure the
// language, not the quality of its sort library.
function insertionSort(a, lo, hi) {
  for (let i = lo + 1; i <= hi; i++) {
    const v = a[i];
    let j = i - 1;
    while (j >= lo && a[j] > v) { a[j + 1] = a[j]; j--; }
    a[j + 1] = v;
  }
}

function quicksort(a, lo, hi) {
  while (hi - lo > 16) {
    const mid = lo + ((hi - lo) >> 1);
    let t;
    // median-of-three: order a[lo] <= a[mid] <= a[hi]
    if (a[mid] < a[lo])  { t = a[mid]; a[mid] = a[lo];  a[lo]  = t; }
    if (a[hi]  < a[lo])  { t = a[hi];  a[hi]  = a[lo];  a[lo]  = t; }
    if (a[hi]  < a[mid]) { t = a[hi];  a[hi]  = a[mid]; a[mid] = t; }
    const pivot = a[mid];

    let i = lo, j = hi;
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

function benchQuicksort(n) {
  const a = new Uint32Array(n);
  rngSeed(12345);
  for (let i = 0; i < n; i++) a[i] = rngNext();
  quicksort(a, 0, n - 1);
  let h = 0;
  for (let i = 0; i < n; i++) h = (Math.imul(h, 31) + a[i]) >>> 0;  // order-sensitive
  return h;
}

// ---------- 4. word count: string hashing + hash map ----------
// A Map with string keys, one line, like Go's and Java's -- and like theirs,
// the hashing underneath is native code, so this is the benchmark where an
// interpreted-ish language is closest to C.
const VOCAB = 5000;

function benchWordcount(n) {
  rngSeed(12345);
  const words = new Array(VOCAB);
  for (let i = 0; i < VOCAB; i++) {
    const len = 3 + rngNext() % 6;
    let w = '';
    for (let c = 0; c < len; c++) w += String.fromCharCode(97 + rngNext() % 26);
    words[i] = w;
  }

  const counts = new Map();
  let maxc = 0;
  for (let k = 0; k < n; k++) {
    const ra = rngNext() % VOCAB;
    const rb = rngNext() % VOCAB;
    const w = words[(ra * rb) / VOCAB | 0];   // triangular, so counts vary
    const c = (counts.get(w) || 0) + 1;
    counts.set(w, c);
    if (c > maxc) maxc = c;
  }
  return counts.size * 1000003 + maxc;
}

// ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
// Nothing here computes; the entire cost is allocating little objects and
// letting V8's generational GC take them back.
function makeTree(d) {
  if (d === 0) return { l: null, r: null };
  return { l: makeTree(d - 1), r: makeTree(d - 1) };
}

function checkTree(t) {
  if (t.l === null) return 1;
  return 1 + checkTree(t.l) + checkTree(t.r);
}

function benchBinarytrees(n) {
  let h = 0;
  for (let k = 0; k < n; k++) {
    const t = makeTree(11);   // 4,095 nodes, built and thrown away
    h = (Math.imul(h, 31) + checkTree(t) + k) >>> 0;
  }
  return h;
}

// ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
// The matrix entries are < 100, so every product and every row sum fits
// exactly in a double -- no Math.imul needed until the final checksum.
function benchMatmul(n) {
  const a = new Uint32Array(n * n);
  const b = new Uint32Array(n * n);
  const c = new Uint32Array(n * n);
  rngSeed(12345);
  for (let i = 0; i < n * n; i++) a[i] = rngNext() % 100;
  for (let i = 0; i < n * n; i++) b[i] = rngNext() % 100;
  for (let i = 0; i < n; i++) {
    const ib = i * n;
    for (let j = 0; j < n; j++) {
      let s = 0;
      for (let k = 0; k < n; k++) s += a[ib + k] * b[k * n + j];
      c[ib + j] = s;
    }
  }
  let h = 0;
  for (let i = 0; i < n * n; i++) h = (Math.imul(h, 31) + c[i]) >>> 0;
  return h;
}

const BENCHMARKS = {
  mandelbrot: benchMandelbrot,
  sieve: benchSieve,
  quicksort: benchQuicksort,
  wordcount: benchWordcount,
  binarytrees: benchBinarytrees,
  matmul: benchMatmul,
};

function main() {
  const argv = process.argv.slice(2);
  if (argv.length < 2) {
    process.stderr.write('usage: node bench.js <benchmark> <size> [reps]\n');
    return 2;
  }
  const name = argv[0];
  const size = parseInt(argv[1], 10);
  let reps = argv.length > 2 ? parseInt(argv[2], 10) : 1;
  if (!(reps >= 1)) reps = 1;

  const fn = BENCHMARKS[name];
  if (!fn) {
    process.stderr.write('unknown benchmark: ' + name + '\n');
    return 2;
  }

  // Best-of-N: the fastest run is the one least polluted by scheduler noise,
  // cold caches and (for the JIT languages) not-yet-compiled code.
  let best = Infinity;
  let result = null;
  for (let r = 0; r < reps; r++) {
    const t0 = process.hrtime.bigint();
    const v = fn(size);
    const elapsed = Number(process.hrtime.bigint() - t0) / 1e6;
    if (elapsed < best) best = elapsed;
    if (r === 0) result = v;
    else if (v !== result) {
      process.stderr.write('nondeterministic result!\n');
      return 3;
    }
  }

  process.stdout.write('OK ' + name + ' ' + result + ' ' + best.toFixed(3) + '\n');
  return 0;
}

process.exit(main());
