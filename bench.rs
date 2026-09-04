// bench.rs -- the Rust entry in the language speed comparison.
//
// Usage: bench <benchmark> <size> [reps]
// Prints: OK <benchmark> <checksum> <compute_milliseconds>
//
// Same algorithm, same deterministic input, same checksum as every other
// bench.* in this suite.
//
// Built with a bare `rustc -O` -- no Cargo, no crates, no `unsafe`. Every
// index below is bounds-checked, which is exactly the trade this entry is
// here to price: safety that you are told costs nothing, measured.

use std::collections::HashMap;
use std::env;
use std::process;
use std::time::Instant;

// ---------- shared deterministic PRNG (identical in every language here) ----------
struct Rng(u64);

impl Rng {
    fn new(seed: u64) -> Rng {
        Rng(seed)
    }
    #[inline]
    fn next(&mut self) -> u32 {
        // wrapping_* because overflow panics in debug builds; the other
        // languages wrap silently and we must produce the same bits.
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (self.0 >> 33) as u32 // top 31 bits
    }
}

// ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
fn bench_mandelbrot(n: i32) -> i64 {
    let mut total: i64 = 0;
    let fnn = n as f64;
    for py in 0..n {
        let ci = 2.0 * py as f64 / fnn - 1.0;
        for px in 0..n {
            let cr = 2.0 * px as f64 / fnn - 1.5;
            let (mut zr, mut zi) = (0.0f64, 0.0f64);
            let mut i = 0;
            while i < 255 {
                let (zr2, zi2) = (zr * zr, zi * zi);
                if zr2 + zi2 > 4.0 {
                    break;
                }
                zi = 2.0 * zr * zi + ci;
                zr = zr2 - zi2 + cr;
                i += 1;
            }
            total += i as i64;
        }
    }
    total
}

// ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
fn bench_sieve(n: i32) -> i64 {
    let n = n as usize;
    let mut comp = vec![0u8; n + 1];
    let mut count: i64 = 0;
    for i in 2..=n {
        if comp[i] == 0 {
            count += 1;
            let mut j = i * i;
            while j <= n {
                comp[j] = 1;
                j += i;
            }
        }
    }
    count
}

// ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
// Hand-written, not slice::sort_unstable -- the point is to measure the
// language, not the quality of its sort library.
fn insertion_sort(a: &mut [u32], lo: isize, hi: isize) {
    for i in lo + 1..=hi {
        let v = a[i as usize];
        let mut j = i - 1;
        while j >= lo && a[j as usize] > v {
            a[(j + 1) as usize] = a[j as usize];
            j -= 1;
        }
        a[(j + 1) as usize] = v;
    }
}

// Indices are isize rather than usize on purpose: the C original lets `j` run
// one step past `lo`, and a usize would panic there instead of wrapping. Same
// algorithm, same swap sequence, same checksum.
fn quicksort(a: &mut [u32], mut lo: isize, mut hi: isize) {
    while hi - lo > 16 {
        let mid = lo + (hi - lo) / 2;
        // median-of-three: order a[lo] <= a[mid] <= a[hi]
        if a[mid as usize] < a[lo as usize] {
            a.swap(mid as usize, lo as usize);
        }
        if a[hi as usize] < a[lo as usize] {
            a.swap(hi as usize, lo as usize);
        }
        if a[hi as usize] < a[mid as usize] {
            a.swap(hi as usize, mid as usize);
        }
        let pivot = a[mid as usize];

        let (mut i, mut j) = (lo, hi);
        while i <= j {
            while a[i as usize] < pivot {
                i += 1;
            }
            while a[j as usize] > pivot {
                j -= 1;
            }
            if i <= j {
                a.swap(i as usize, j as usize);
                i += 1;
                j -= 1;
            }
        }
        // recurse into the smaller half, loop on the larger: depth stays O(log n)
        if j - lo < hi - i {
            quicksort(a, lo, j);
            lo = i;
        } else {
            quicksort(a, i, hi);
            hi = j;
        }
    }
    insertion_sort(a, lo, hi);
}

fn bench_quicksort(n: i32) -> i64 {
    let n = n as usize;
    let mut rng = Rng::new(12345);
    let mut a: Vec<u32> = (0..n).map(|_| rng.next()).collect();
    quicksort(&mut a, 0, n as isize - 1);
    let mut h: u32 = 0;
    for i in 0..n {
        h = h.wrapping_mul(31).wrapping_add(a[i]); // order-sensitive checksum
    }
    h as i64
}

// ---------- 4. word count: string hashing + hash map ----------
// std::collections::HashMap is one line, like Go's and Java's. It also hashes
// with SipHash by default -- cryptographically strong, and slower than the
// others' hashes. That choice shows up in this number; see the README.
const VOCAB: usize = 5000;

fn bench_wordcount(n: i32) -> i64 {
    let mut rng = Rng::new(12345);
    let mut words: Vec<String> = Vec::with_capacity(VOCAB);
    for _ in 0..VOCAB {
        let len = 3 + (rng.next() % 6) as usize;
        let mut w = String::with_capacity(len);
        for _ in 0..len {
            w.push((b'a' + (rng.next() % 26) as u8) as char);
        }
        words.push(w);
    }

    let mut counts: HashMap<&str, i64> = HashMap::new();
    let mut maxc: i64 = 0;
    for _ in 0..n {
        let ra = rng.next() % VOCAB as u32;
        let rb = rng.next() % VOCAB as u32;
        let w: &str = &words[((ra * rb) / VOCAB as u32) as usize]; // triangular, so counts vary
        let c = counts.entry(w).or_insert(0);
        *c += 1;
        if *c > maxc {
            maxc = *c;
        }
    }
    counts.len() as i64 * 1000003 + maxc
}

// ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
// Box per node, ownership does the freeing: dropping the tree at the end of
// each loop iteration walks and deallocates every node, so Rust pays its
// teardown right here in the loop, like C's free_tree and Perl's refcounts.
struct Tree {
    l: Option<Box<Tree>>,
    r: Option<Box<Tree>>,
}

fn make_tree(d: i32) -> Box<Tree> {
    if d == 0 {
        Box::new(Tree { l: None, r: None })
    } else {
        Box::new(Tree {
            l: Some(make_tree(d - 1)),
            r: Some(make_tree(d - 1)),
        })
    }
}

fn check_tree(t: &Tree) -> i64 {
    match (&t.l, &t.r) {
        (Some(l), Some(r)) => 1 + check_tree(l) + check_tree(r),
        _ => 1,
    }
}

fn bench_binarytrees(n: i32) -> i64 {
    let mut h: u32 = 0;
    for k in 0..n {
        let t = make_tree(11); // 4,095 nodes, built and thrown away
        h = h
            .wrapping_mul(31)
            .wrapping_add(check_tree(&t) as u32)
            .wrapping_add(k as u32);
    }
    h as i64
}

// ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
fn bench_matmul(n: i32) -> i64 {
    let n = n as usize;
    let mut rng = Rng::new(12345);
    let a: Vec<u32> = (0..n * n).map(|_| rng.next() % 100).collect();
    let b: Vec<u32> = (0..n * n).map(|_| rng.next() % 100).collect();
    let mut c = vec![0u32; n * n];
    for i in 0..n {
        let ib = i * n;
        for j in 0..n {
            let mut s: u32 = 0;
            for k in 0..n {
                s = s.wrapping_add(a[ib + k].wrapping_mul(b[k * n + j]));
            }
            c[ib + j] = s;
        }
    }
    let mut h: u32 = 0;
    for i in 0..n * n {
        h = h.wrapping_mul(31).wrapping_add(c[i]);
    }
    h as i64
}

fn dispatch(name: &str, size: i32) -> i64 {
    match name {
        "mandelbrot" => bench_mandelbrot(size),
        "sieve" => bench_sieve(size),
        "quicksort" => bench_quicksort(size),
        "wordcount" => bench_wordcount(size),
        "binarytrees" => bench_binarytrees(size),
        "matmul" => bench_matmul(size),
        _ => {
            eprintln!("unknown benchmark: {}", name);
            process::exit(2);
        }
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: {} <benchmark> <size> [reps]", args[0]);
        process::exit(2);
    }
    let name = args[1].clone();
    let size: i32 = args[2].parse().unwrap_or(0);
    let mut reps: i32 = if args.len() > 3 {
        args[3].parse().unwrap_or(1)
    } else {
        1
    };
    if reps < 1 {
        reps = 1;
    }

    // Best-of-N: the fastest run is the one least polluted by scheduler noise,
    // cold caches and (for the JIT languages) not-yet-compiled code.
    let mut best = f64::INFINITY;
    let mut result: i64 = 0;
    for r in 0..reps {
        let t0 = Instant::now();
        let v = dispatch(&name, size);
        let elapsed = t0.elapsed().as_secs_f64() * 1000.0;
        if elapsed < best {
            best = elapsed;
        }
        if r == 0 {
            result = v;
        } else if v != result {
            eprintln!("nondeterministic result!");
            process::exit(3);
        }
    }

    println!("OK {} {} {:.3}", name, result, best);
}
