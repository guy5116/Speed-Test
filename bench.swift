/*
 * bench.swift -- the Swift entry in the language speed comparison.
 *
 * Usage: bench <benchmark> <size> [reps]
 * Prints: OK <benchmark> <checksum> <best_ms> <median_ms> <worst_ms>
 *
 * Same algorithm, same deterministic input, same checksum as every other
 * bench.* in this suite.
 *
 * Plain Swift, no Foundation, no packages: compiled with a bare `swiftc -O`.
 * Arrays keep their bounds checks (like Rust); the wrapping operators
 * (&*, &+) are the language's own way of asking for C's overflow semantics,
 * not an unsafe door.
 */

#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif

func nowMS() -> Double {
    var ts = timespec()
    clock_gettime(CLOCK_MONOTONIC, &ts)
    return Double(ts.tv_sec) * 1000.0 + Double(ts.tv_nsec) / 1e6
}

// ---------- shared deterministic PRNG (identical in every language here) ----------
var rngState: UInt64 = 0

func rngSeed(_ s: UInt64) { rngState = s }

func rngNext() -> UInt32 {
    rngState = rngState &* 6364136223846793005 &+ 1442695040888963407
    return UInt32(truncatingIfNeeded: rngState >> 33)  // top 31 bits
}

// ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
func benchMandelbrot(_ n: Int) -> Int64 {
    var total: Int64 = 0
    for py in 0..<n {
        let ci = 2.0 * Double(py) / Double(n) - 1.0
        for px in 0..<n {
            let cr = 2.0 * Double(px) / Double(n) - 1.5
            var zr = 0.0, zi = 0.0
            var i = 0
            while i < 255 {
                let zr2 = zr * zr, zi2 = zi * zi
                if zr2 + zi2 > 4.0 { break }
                zi = 2.0 * zr * zi + ci
                zr = zr2 - zi2 + cr
                i += 1
            }
            total += Int64(i)
        }
    }
    return total
}

// ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
func benchSieve(_ n: Int) -> Int64 {
    var comp = [Bool](repeating: false, count: n + 1)
    var count: Int64 = 0
    var i = 2
    while i <= n {
        if !comp[i] {
            count += 1
            var j = i * i
            while j <= n {
                comp[j] = true
                j += i
            }
        }
        i += 1
    }
    return count
}

// ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
func insertionSort(_ a: inout [UInt32], _ lo: Int, _ hi: Int) {
    var i = lo + 1  // not a closed range: hi < lo happens on empty partitions
    while i <= hi {
        let v = a[i]
        var j = i - 1
        while j >= lo && a[j] > v {
            a[j + 1] = a[j]
            j -= 1
        }
        a[j + 1] = v
        i += 1
    }
}

func quicksort(_ a: inout [UInt32], _ lo: Int, _ hi: Int) {
    var lo = lo, hi = hi
    while hi - lo > 16 {
        let mid = lo + (hi - lo) / 2
        // median-of-three: order a[lo] <= a[mid] <= a[hi]
        if a[mid] < a[lo] { a.swapAt(mid, lo) }
        if a[hi] < a[lo] { a.swapAt(hi, lo) }
        if a[hi] < a[mid] { a.swapAt(hi, mid) }
        let pivot = a[mid]

        var i = lo, j = hi
        while i <= j {
            while a[i] < pivot { i += 1 }
            while a[j] > pivot { j -= 1 }
            if i <= j {
                a.swapAt(i, j)
                i += 1
                j -= 1
            }
        }
        // recurse into the smaller half, loop on the larger: depth stays O(log n)
        if j - lo < hi - i {
            quicksort(&a, lo, j)
            lo = i
        } else {
            quicksort(&a, i, hi)
            hi = j
        }
    }
    insertionSort(&a, lo, hi)
}

func benchQuicksort(_ n: Int) -> Int64 {
    rngSeed(12345)
    // filled by index: append() pays a uniqueness + capacity check per
    // element even after reserveCapacity, and this loop is inside the timing
    var a = [UInt32](repeating: 0, count: n)
    for i in 0..<n { a[i] = rngNext() }
    quicksort(&a, 0, n - 1)
    var h: UInt32 = 0
    for i in 0..<n { h = h &* 31 &+ a[i] }  // order-sensitive checksum
    return Int64(h)
}

// ---------- 4. word count: string hashing + hash map ----------
// Swift's Dictionary and String hashing live in the standard library
// (SipHash-1-3, like Rust's default), so this row prices that default.
let VOCAB = 5000

func benchWordcount(_ n: Int) -> Int64 {
    rngSeed(12345)
    var words = [String]()
    words.reserveCapacity(VOCAB)
    for _ in 0..<VOCAB {
        let len = 3 + Int(rngNext() % 6)
        // build as UTF-8 bytes: appending Characters one at a time drags in
        // grapheme breaking, which is pure overhead for plain ASCII
        var bytes = [UInt8]()
        bytes.reserveCapacity(len)
        for _ in 0..<len {
            bytes.append(UInt8(97 + rngNext() % 26))
        }
        words.append(String(decoding: bytes, as: UTF8.self))
    }

    var counts = [String: Int](minimumCapacity: 8192)
    var maxc = 0
    for _ in 0..<n {
        let ra = Int(rngNext()) % VOCAB
        let rb = Int(rngNext()) % VOCAB
        let w = words[(ra * rb) / VOCAB]  // triangular, so counts vary
        // one hash + probe via the index, not the two a read-then-write
        // subscript pair costs; values[i] updates in place, no rehash
        if let i = counts.index(forKey: w) {
            let c = counts.values[i] + 1
            counts.values[i] = c
            if c > maxc { maxc = c }
        } else {
            counts[w] = 1
            if maxc < 1 { maxc = 1 }
        }
    }
    return Int64(counts.count) * 1000003 + Int64(maxc)
}

// ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
// Nothing here computes; the whole cost is class instances and ARC retain/
// release traffic -- Swift pays per-object like CPython's refcounts, not in
// GC bursts like Java.
final class Node {
    var l: Node?
    var r: Node?
}

func makeTree(_ d: Int) -> Node {
    let t = Node()
    if d > 0 {
        t.l = makeTree(d - 1)
        t.r = makeTree(d - 1)
    }
    return t
}

func checkTree(_ t: Node) -> Int {
    // test only l, like every other language: binding r too would retain/
    // release a second child per node, ARC traffic no one else pays
    guard let l = t.l else { return 1 }
    return 1 + checkTree(l) + checkTree(t.r!)
}

func benchBinarytrees(_ n: Int) -> Int64 {
    var h: UInt32 = 0
    for k in 0..<n {
        let t = makeTree(11)  // 4,095 nodes, built and thrown away
        h = h &* 31 &+ UInt32(checkTree(t)) &+ UInt32(k)
    }
    return Int64(h)
}

// ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
func benchMatmul(_ n: Int) -> Int64 {
    rngSeed(12345)
    var a = [UInt32](repeating: 0, count: n * n)
    var b = [UInt32](repeating: 0, count: n * n)
    var c = [UInt32](repeating: 0, count: n * n)
    for i in 0..<(n * n) { a[i] = rngNext() % 100 }
    for i in 0..<(n * n) { b[i] = rngNext() % 100 }
    for i in 0..<n {
        let ib = i * n
        for j in 0..<n {
            var s: UInt32 = 0
            var bi = j  // walks b down the column; saves a multiply per step
            for k in 0..<n { s = s &+ a[ib + k] &* b[bi]; bi += n }
            c[ib + j] = s
        }
    }
    var h: UInt32 = 0
    for i in 0..<(n * n) { h = h &* 31 &+ c[i] }
    return Int64(h)
}

// ---------- hidden: prng conformance check, not part of the scored suite ----------
// run.py --selftest uses it to validate each language's PRNG directly; several
// languages implement the 64-bit multiply in 32-bit halves and this checks every bit.
func benchPrng(_ n: Int) -> Int64 {
    rngSeed(12345)
    var h: UInt32 = 0
    for _ in 0..<n { h = h &* 31 &+ rngNext() }
    return Int64(h)
}

// ---------- driver ----------
func dispatch(_ name: String, _ size: Int) -> Int64? {
    switch name {
    case "mandelbrot": return benchMandelbrot(size)
    case "sieve": return benchSieve(size)
    case "quicksort": return benchQuicksort(size)
    case "wordcount": return benchWordcount(size)
    case "binarytrees": return benchBinarytrees(size)
    case "matmul": return benchMatmul(size)
    case "prng": return benchPrng(size)
    default: return nil
    }
}

func fmt3(_ x: Double) -> String {
    // "%.3f" without pulling in Foundation for String(format:)
    let t = Int((x * 1000.0).rounded())
    return "\(t / 1000)." + String(1000 + t % 1000).dropFirst()
}

func fail(_ msg: String, _ code: Int32) -> Never {
    fputs(msg + "\n", stderr)
    exit(code)
}

let args = CommandLine.arguments
if args.count < 3 {
    fail("usage: \(args[0]) <benchmark> <size> [reps]", 2)
}
let name = args[1]
guard let size = Int(args[2]) else { fail("bad size: \(args[2])", 2) }
let reps = max(args.count > 3 ? (Int(args[3]) ?? 1) : 1, 1)

// Best-of-N: the fastest run is the one least polluted by scheduler noise
// and cold caches. Median and worst ride along so the runner can report
// the spread.
var times = [Double]()
times.reserveCapacity(reps)
var result: Int64 = 0
for r in 0..<reps {
    let t0 = nowMS()
    guard let v = dispatch(name, size) else { fail("unknown benchmark: \(name)", 2) }
    times.append(nowMS() - t0)
    if r == 0 {
        result = v
    } else if v != result {
        fail("nondeterministic result!", 3)
    }
}

times.sort()
print("OK \(name) \(result) \(fmt3(times[0])) \(fmt3(times[reps / 2])) \(fmt3(times[reps - 1]))")
