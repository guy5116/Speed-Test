# How much does your language cost you?

![ci](https://github.com/guy5116/Speed-Test/actions/workflows/ci.yml/badge.svg)

Fifteen languages — **x86-64 assembly, C, C++, Rust, Swift, Go, Java, C#,
JavaScript, Lua, Perl, PHP, Python, Ruby, COBOL** — plus a **NumPy** row for
Python, running the *same six algorithms* on the *same input*, timed side by
side. Every entry runs at its shipped best: `-O3` and `-march=native` where
there is a compiler, the tracing JIT for PHP, YJIT for Ruby — so a slow row
is the language's bill, not the code's.

The goal is to make the difference **feel** real. Ratios like "40×" slide off
the brain. "Your overnight job finishes next Tuesday" does not.

```bash
python3 run.py            # the standard run, ~9 min (~3 with --skip cobol)
python3 run.py --quick    # a few seconds, good for a demo or a talk
python3 run.py --heavy    # the genuinely massive scale, an hour and 6 GB
```

The runner compiles everything itself and skips any language whose toolchain
is missing, so you can run it with whatever you happen to have installed.

---

## What it looks like

While each language works, the row it is about to fill shows a bar, an elapsed
clock and a guess at what is left:

```
    ⣻ Python     ██████████████████▌░░░░░░░░░░░░░░░░░░░░       3.1s  ~4s left
```

That estimate is predicted from earlier results rather than measured -- see
"Honesty rails" below for why it cannot affect the numbers. The finished run
looks like this:

```
  ──────────────────────────────────────────────────────────────────────────
  1/4  MANDELBROT
  ──────────────────────────────────────────────────────────────────────────
  800 x 800 grid, up to 255 iterations per pixel
  Measures: raw floating-point arithmetic in a tight loop

    C          ▋                                       135.0 ms    1.0x
    C++        ▋                                       133.4 ms    1.0x
    Rust       ▋                                       136.3 ms    1.0x
    Go         ▊                                       158.5 ms    1.2x
    Java       ▊                                       154.3 ms    1.2x
    JavaScript ▊                                       152.3 ms    1.1x
    Lua        ███████▌                                 1.362 s   10.2x
    Python     ██████████████████████████████████████   6.729 s   50.5x
    checksum 66,049,637 -- all 8 languages agree

  ══════════════════════════════════════════════════════════════════════════
  SUMMARY -- slowdown versus the fastest language, per benchmark
  ══════════════════════════════════════════════════════════════════════════

                mandelbrot       sieve   quicksort   wordcount     average
    C                 1.0x        1.0x        1.1x        1.0x        1.0x
    C++               1.0x        1.0x        1.1x        1.9x        1.3x
    Rust              1.0x        1.1x        1.0x        1.2x        1.1x
    Go                1.2x        1.0x        1.0x        1.1x        1.1x
    Java              1.2x        1.0x        1.2x        1.2x        1.1x
    JavaScript        1.1x        1.2x        1.7x        3.5x        1.9x
    Lua              10.2x        7.4x        6.2x        4.3x        7.0x
    Python           50.5x       16.5x       29.3x       28.1x       31.1x

  ══════════════════════════════════════════════════════════════════════════
  PUT IT ON A HUMAN SCALE
  ══════════════════════════════════════════════════════════════════════════

  An overnight 8-hour job in C takes:
    C          8 hr 0 min            (baseline)
    C++        9 hr 36 min
    Rust       8 hr 22 min
    Go         8 hr 11 min
    Java       8 hr 48 min
    JavaScript 14 hr 28 min
    Lua        2 days 4 hr
    Python     8 days 23 hr

  ══════════════════════════════════════════════════════════════════════════
  AND BEFORE ANY WORK HAPPENS AT ALL
  ══════════════════════════════════════════════════════════════════════════

    C          735 microseconds
    Rust       770 microseconds
    C++        1 millisecond
    Go         1 millisecond
    Lua        1 millisecond
    Python     13 milliseconds
    JavaScript 23 milliseconds
    Java       64 milliseconds
```

*(Real output, 12-core x86-64 Linux: gcc/g++ 15.3, rustc 1.97, go 1.26,
OpenJDK 21, node 24, Lua 5.4, CPython 3.14. Whole run: 1 min 57 sec. This
sample predates the Perl, Ruby, Swift and COBOL entries and benchmarks 5 and
6 — run it yourself for the full sixteen-row table. Your numbers will
differ anyway; that is the point of shipping the runner rather than a
screenshot.)*

Three things worth staring at in that table. **C, C++ and Rust are tied**, and
Go and Java are within 20% of them — the interesting gap in 2026 is not
between compiled languages, it is between compiled and interpreted.
**JavaScript is closer to Java than to Python**, which surprises people who
still file it under "scripting language". And **the C++ row loses to plain C
on benchmark 4 by 1.9×** — `std::unordered_map` is required by the standard to
be a chained hash table, one heap node per element, so it chases pointers where
`bench.c`'s hand-rolled open-addressing table walks a flat array. The
convenience is real and so is the bill.

---

## The six workloads, and why each one is here

They are deliberately chosen to *disagree* with each other. A benchmark suite
where every test says the same thing is a benchmark suite that is only
measuring one thing.

| # | Benchmark | Stresses | The point it makes |
|---|-----------|----------|--------------------|
| 1 | **Mandelbrot** | Float arithmetic, tight loop, zero allocation | The worst case for an interpreter. Nowhere to hide. |
| 2 | **Sieve of Eratosthenes** | Integer math over a 50 M-entry array | Memory-bound. The CPU waits on RAM, so the gap *narrows*. |
| 3 | **Quicksort** | Branches, swaps, recursion, cache misses | Hand-written in all fifteen, so it measures the language rather than the quality of its sort library. |
| 4 | **Word frequency count** | String hashing into a hash map | The revenge of the scripting languages — see below. |
| 5 | **Binary trees** | Allocation, pointer chasing, GC pressure | Nothing computes; the whole cost is memory management. The JVM's allocator tends to *beat malloc/free* here — see the GC caveat below before quoting that. |
| 6 | **Matrix multiply** | n³ multiply-adds over flat 2D arrays | The classic numeric kernel, and the widest gap in the whole suite: index arithmetic on every operation is exactly what an interpreter does worst. |

Benchmark 4 is the important one, and it is the one people usually leave out.
Python's `dict`, Java's `HashMap`, JavaScript's `Map` and Lua's table are all
written in C or C++. When your Python program spends its time *inside* them, it
is already running at close to C speed, and the interpreter tax shrinks
dramatically. Meanwhile C has no built-in map at all, so `bench.c` carries a
hand-rolled open-addressing hash table — and `bench.asm` carries the same
table again, an abstraction level lower: roughly 40 lines to do what most of
the others do in one.

That trade — *your* time against the *computer's* time — is the actual
decision most people are making, and it is invisible in a suite that only
measures arithmetic.

---

## What each language is here to show

| | Why it is in the list |
|---|---|
| **Assembly** | The control group. Hand-written x86-64, no compiler at all — every instruction that runs is one somebody typed. Where `gcc -O3` beats it (and on some benchmarks it does), the gap is the measured value of fifty years of compiler engineering; where they tie, the benchmark was memory-bound all along. Linux only, and it links libc for `malloc` and `printf` like everyone else. |
| **C** | The floor. Also the language that has to hand-roll a hash table to play. |
| **C++** | C's numbers with `std::unordered_map` and `std::vector` instead of `malloc`. The abstraction is supposed to be free; benchmark 4 says how free. |
| **Rust** | Bounds-checked on every index, no `unsafe`, no crates, `rustc` at a tuned release profile (`opt-level=3`, `target-cpu=native`, one codegen unit). Prices the safety directly. Its `HashMap` also defaults to SipHash — cryptographically strong and slower than everyone else's — which is why benchmark 4 is its worst row and why `ahash`/`FxHash` exist. |
| **Swift** | The same deal as Rust — no unsafe code, no Foundation — with two differences. First, it builds `-Ounchecked`: Swift's supported flag for waiving the bounds and overflow checks `-O` keeps, a shortcut Rust only offers by rewriting the source with `unsafe` (rebuild with `-O` to see what the checks cost). Second, and bigger: ARC. Benchmark 5 is the row to watch, because Swift pays a retain/release toll on every object where a tracing GC pays in bursts; that is CPython's memory story at compiled-language speed. Its `Dictionary` also hashes with SipHash like Rust's, so benchmark 4 carries the same tax. The `&*`/`&+` wrapping operators run the shared PRNG natively. |
| **Go** | Compiled with a GC and a fast build. Note that this suite is single-threaded, which is the one thing Go is famous for making easy — so it *understates* Go badly. |
| **Java** | The JIT. Cold it looks like a scripting language; warm it looks like C. Watching that gap close between `--quick` and the standard scale is the most instructive thing in the suite. |
| **C#** | The other managed-runtime JIT, with one honest advantage over Java here: real unsigned integers. `ulong` and `uint` run the shared PRNG and the quicksort with no sign gymnastics at all, and the .NET JIT prices within a few percent of the JVM on most rows. |
| **JavaScript** | The other JIT, and a reminder that "scripting language" says nothing about speed. Its handicap here is arithmetic, not dispatch: JS has no fast 64-bit integer, so the shared PRNG has to be assembled from 32-bit halves (see below). |
| **Lua** | The small interpreter done well. Roughly the same *design* as CPython — a bytecode VM, dynamic types, tables everywhere — and several times faster at it, which is a useful counterweight to "interpreted languages are slow". |
| **Perl** | The original scripting language, still gluing Unix together. Its hash and string machinery is C and fast — benchmark 4 is its best row, where it routinely beats Python — and its numeric loops are the bill: benchmark 6 is among the slowest rows in the suite. It needed two judgement calls to play (`use integer`, `vec()`), noted below. |
| **PHP** | The language serving most of the web, on the same footing as its peers for once — and at full strength: the runner switches on the tracing JIT that ships inside opcache but sleeps on the CLI by default. Benchmark 4 runs on its native associative array. Its handicap is the same one JavaScript has: ints are signed 64-bit and *overflow to float*, so the shared PRNG is assembled from 32-bit halves (see below). |
| **Ruby** | CRuby with YJIT on. The JIT has shipped inside every standard Ruby since 3.1 and simply defaults to off; the runner enables it the same way it hands gcc `-O3`, so this row is Ruby at its own shipped best — not a lab build, the way LuaJIT would be for Lua. |
| **Python** | The ceiling on interpreter overhead, and the language most people actually reach for. Read the caveats before quoting its numbers. |
| **NumPy** | Python's defence, measured instead of asserted. The exact same interpreter as the row above, with the loops handed to NumPy so they run as C — and, for matmul, as BLAS, which tends to win that benchmark outright. It enters the three benchmarks that have an honest vectorised form (mandelbrot, sieve, matmul) and sits the others out rather than cheat them (see the honesty rails). The gap between this row and the Python row is the interpreter tax; the gap between this row and C is what the escape hatch actually costs. |
| **COBOL** | The oldest language here by decades, still clearing your card payments, compiled to native code via C by GnuCOBOL. The parts of the suite that are moves, compares and adds run near C speed — it all but *ties C on benchmark 5*, since that one is really timing libc's allocator. The bill is arithmetic: COBOL's is decimal, so every multiply routes through GMP, and floating point costs microseconds an operation — which is why the runner sits it out of mandelbrot (see below). A language built for money math, priced here on loops it was never meant to run — and, like the assembly entry, useful mostly as a control group. |

---

## Honesty rails

A language benchmark is very easy to accidentally rig. Guards used here:

**Every language prints a checksum.** All sixteen entries compute the same value from the
same deterministic PRNG (an identical 64-bit LCG in each file). If the
checksums ever disagree, the runner prints `CHECKSUMS DISAGREE` **and drops
that row from every summary, leaderboard and results.json** — an invalid
comparison must not leave numbers behind. This also stops C's optimizer from
deleting a loop whose result nobody uses — a classic way to accidentally
"prove" C is infinitely fast.

**The floats are pinned down.** GCC contracts `a*b + c` into fused
multiply-adds by default (`-ffp-contract=fast`), which changes the rounding of
mandelbrot's inner loop — invisibly at the standard size, and a genuine
checksum mismatch at `--heavy`. The C and C++ builds therefore carry
`-ffp-contract=off`, and `bench.go` forces its intermediate roundings with
explicit `float64()` conversions, so every language computes the same doubles.

**The PRNG has its own conformance test.** `python3 run.py --selftest` runs a
hidden benchmark that checksums the first million raw generator outputs in
every language against a reference computed in arbitrary-precision Python.
Several entries build the 64-bit multiply out of 32-bit halves (JavaScript,
PHP, Ruby, COBOL); this validates every bit of that code directly instead of
hoping quicksort trips over a wrong one.

**Golden checksums are recorded.** `golden.json` holds cross-verified
checksums for every benchmark at the `--quick`, standard and `--heavy` sizes.
Within-run agreement cannot catch a bug that every implementation shares;
the goldens can, and they let a single-language run validate itself.
`tools/golden.py --check` re-derives every value from at least two
independent implementations; `--write` is the only way the file changes.

**A failing comparison fails the run.** A checksum disagreement or a golden
mismatch does not just exclude the row -- the run exits non-zero, so a CI
job cannot stay green while the suite proves nothing. `--strict` extends
this to any language that fails, times out, or is missing from `--require`.

**Best-of-N, inside one process — and the spread is reported, not hidden.**
Each program runs its benchmark N times (default 3) and reports the fastest,
*plus* the median and the worst. The repeats happen *in-process*, which
matters enormously for Java: the JIT needs to see code run hot before it
compiles it. Timing a single cold JVM run measures the JIT's warmup, not the
language. Best-of-N suppresses scheduler noise and cold caches — but
suppressing noise is not the same as pretending there was none, so each row
shows `±N%` (worst versus best), rows past 15% get a `⚠ noisy` flag, and
`results.json` records `seconds_median` and `spread` alongside the best.

**Startup is measured separately, not hidden.** The JVM's ~55 ms of boot time
is meaningless for an 8-hour batch job and completely decisive for a CLI tool
invoked in a shell loop. Those are different questions, so they get different
numbers.

**The progress bars are predictions, never measurements.** While a language is
working, the line it will eventually occupy fills with a progress bar. The
runner has no way to ask a child process how far along it is, and instrumenting
one to find out would change the thing being timed -- so the bar fills against
an *estimate* assembled from results that already exist: the languages that
already finished this run, and previous runs on this machine, cached in
`build/timings.json`. Nothing is polled and no extra process is started; the
drawing costs one line redrawn eight times a second on a thread that would have
been spinning anyway. A run with the bars takes exactly as long as one without.

Because the estimate can be wrong -- a first run on a new machine has nothing
but the constants at the top of `run.py` to go on -- the fill is deliberately
honest. It is linear to 90% and asymptotic after that, so a run that overshoots
its estimate crawls towards the end of the track rather than sitting at 100%
pretending to be finished. Delete `build/timings.json` at any time; the next
run rebuilds it. The startup-cost bar is the one exception: progress there is
exactly "languages measured so far", and it is real.

In practice: a repeat run at the same scale predicts itself to within a few
percent, and a scale it has never seen before -- `--quick` yesterday, standard
today -- starts out 10-20% off and closes most of that after the first couple
of languages report in.

**Only compute time is compared.** Each program times its own hot region with
a monotonic clock. Process launch, compilation and interpreter startup are
excluded from the headline figure and reported on their own. (Lua is the one
exception: `os.clock()` is its only sub-second timer and it reports CPU rather
than wall time. For a single-threaded compute loop those are the same number.)

**No language gets a library that another one is denied.** Every quicksort is
hand-written — no `qsort`, no `std::sort`, no `sort_unstable`, no
`table.sort`, no `Arrays.sort`. Benchmark 3 is there to measure the language,
and a sort-library shootout is a different and also interesting test that this
is deliberately not. The NumPy row is the one sanctioned exception, and it is
fenced: it exists precisely to measure the library escape hatch, it still has
to match every checksum bit for bit, and wherever the library would replace
the *algorithm* rather than the loop — the hand-written quicksort, the string
hash map, the tree allocation — it sits out with a note instead of pretending.

---

## Where the sixteen files genuinely differ

A few places where "identical algorithm" needed a judgement call. All of them
are visible in the source, and all of them are noted here rather than buried.

**JavaScript has to fake the 64-bit multiply.** The shared PRNG is
`state = state * 6364136223846793005 + 1442695040888963407` mod 2⁶⁴ — one
instruction everywhere else. JS has no fast 64-bit integer, and `BigInt` is
correct but roughly an order of magnitude slower, so `bench.js` does the
multiply by hand in 32-bit halves with `Math.imul`. That is about six
operations where C spends one, and the cost lands inside the timed region of
benchmarks 3 and 4. It is a real cost of the language — the same kind of
tax as C's hand-rolled hash table — but it is a *language* tax, not a *VM*
tax, and V8 is not to blame for it.

**PHP fakes the same multiply, for a different reason.** PHP has 64-bit
integers, but they are signed and they *overflow to float* instead of
wrapping — one step past `PHP_INT_MAX` and the PRNG would silently turn into
noise (which the runner would catch as a checksum mismatch). So `bench.php`
splits the state into two 32-bit halves and does the multiply in partial
products that each stay inside 63 bits. Same tax as JavaScript's, landing in
the same benchmarks.

**COBOL fakes it too, for a third reason.** COBOL arithmetic is decimal with
a 38-digit ceiling, and a full 64×64-bit multiply needs 39, so `bench.cob`
runs the same split-halves routine as JavaScript and PHP. The halves and
carries come out of a little-endian `REDEFINES` overlay on the 64-bit
temporaries, because the polite spelling (`FUNCTION MOD`) costs about 3 µs a
call in GnuCOBOL and the overlay costs nothing. Three more judgement calls,
all in the same spirit: the big arrays live in `CALL "malloc"` memory because
COBOL tables are sized at compile time; quicksort and the binary trees run on
explicit stacks because `PERFORM` cannot recurse; and benchmark 5 calls libc's
`malloc`/`free` directly rather than COBOL's own `ALLOCATE`/`FREE`, whose
bookkeeping registry is searched linearly on every FREE — timing that would
time the registry, not the allocator. For benchmark 4 each vocabulary word is
stored both as text (for the equality probes) and as byte values (for the
hashing), because a `PIC X` character is not a number and converting one
mid-hash costs more than the hash — the same reasoning as Perl's `vec()`.
Every occurrence still hashes all of its bytes.

**COBOL sits out mandelbrot.** GnuCOBOL routes every floating-point operation
through its decimal engine at roughly 3 µs apiece — about three orders of
magnitude over C, or twenty-odd minutes at the standard scale. The
implementation in `bench.cob` is exact (it produces the shared checksum,
bit for bit); the runner just declines to make you watch it, and prints a note
in the mandelbrot section instead. Run it yourself if you like:
`./build/bench_cobol mandelbrot 200 1`. This is the LuaJIT precedent
inverted: LuaJIT is excluded because it *cannot* reproduce the number, COBOL
because it can, at a price nobody should pay by default. The float row and
the near-tie on benchmark 5 are the same finding from opposite ends: what
COBOL sells is decimal money arithmetic and code that mostly moves records
around, and what it never promised anyone was floating point.

**The assembly entry is the "no compiler" baseline, not an optimized one.**
`bench.asm` is scalar SSE2, no unrolling, no scheduling cleverness — a
competent-but-naive translation of `bench.c`, assembled by NASM exactly as
written. That is the point: the rows where gcc's output beats it show what
the optimizer buys you over the same algorithm typed by hand. It is also the
one entry that is platform-locked (x86-64 Linux, System V ABI) — everyone
else brought a portable language, and that portability is worth remembering
when reading its row.

**Lua's sieve wants a gigabyte.** Lua has no byte array, so `comp` is a plain
table with one integer per index: about 16 bytes per entry where C spends 1.
At the standard scale that is ~1 GB of RAM, and `--heavy` wants six times
that. A bitset would fit in 16 MB — but it would also be doing eight times
less memory traffic than everyone else on the one benchmark that exists to
*measure* memory traffic, so it would not be the same test. The memory number
is itself a finding; the runtime number stays comparable.

**Perl, PHP and Ruby's byte arrays are strings.** The sieve wants a flat
array of bytes. A Perl array costs ~50 bytes per element, a PHP array ~16 and
a Ruby array 8+, so `bench.pl` pokes a string with `vec()`, `bench.php`
indexes a mutable string one byte at a time, and `bench.rb` uses
`String#getbyte`/`setbyte` — the same reasoning as JavaScript's typed arrays:
these are the languages' honest equivalents of C's `unsigned char*`, not a
trick. (One asymmetry inside that choice: Perl *reads* through `vec()` but
*stores* through 4-arg `substr`, because an lvalue `vec` assignment routes
through Perl's magic-SV machinery and measures at twice the cost of the whole
loop around it.) Perl also scopes `use integer` around its integer-heavy
loops to get native 64-bit wrapping arithmetic; on a 32-bit perl the bits
would come out differently, and the runner would catch the checksum mismatch
and refuse the comparison. Ruby's integers go arbitrary-precision past 2^62,
which would put the shared PRNG's full-width multiply in Bignum — three heap
allocations per call — so `bench.rb` keeps the state in two 32-bit halves and
multiplies in 16-bit limbs, the same decomposition `bench.js` and `bench.php`
use for their own overflow reasons. Perl, PHP, Lua and Ruby also paste the
two-line PRNG inline in their hottest loops: none of the four has an inliner,
and a function call around two arithmetic ops costs more than the ops.

`bench.php` additionally sets `memory_limit` to `-1` for itself. That *is*
the documented CLI default, but distro `php.ini` files (Nix, Debian) often
cap it at 128 MB, which kills the sieve and quicksort at `--heavy` scale
with a fatal error before they can print. No other language here runs under
an artificial memory cap, so lifting PHP's keeps the footing equal.

**LuaJIT is excluded, and would change everything.** `bench.lua` needs Lua
5.3+ because it needs an integer that wraps at 64 bits; LuaJIT is a 5.1
dialect where every number is a double, so it cannot reproduce the shared
PRNG without dropping into the FFI. The runner detects LuaJIT and skips it
with a note rather than silently reporting a mismatched checksum. This matters
for how you read the Lua row: it is *reference Lua*, and LuaJIT is commonly
one to two orders of magnitude faster on exactly this kind of loop. The Lua
row is a fact about the reference interpreter, not about the language.

---

## What these numbers do NOT mean

Please read this section before quoting the output at anyone.

**"Python is 30× slower than C" is false as a general claim.** It is true for
a hand-written numeric loop, which is precisely the thing you should never
write in pure Python. The NumPy row is that rewrite, measured: the same
interpreter lands within a small factor of C on mandelbrot and the sieve, and
on matmul it tends to *win outright* — BLAS is the fastest matmul on the
machine in any language. Real Python programs spend most of
their time in C extensions, and their real-world gap is usually far smaller
than benchmark 1 suggests. **Benchmark 4 is the more representative number for
typical code.**

**"C, C++ and Rust are tied" is the finding, not a rounding error.** They land
within noise of each other on nearly every benchmark because they compile to
roughly the same machine code through roughly the same backends. Anyone
claiming a consistent double-digit-percent win for one of the three on
straight-line numeric code is measuring their compiler flags, their allocator,
or their afternoon. The one row where they *do* separate is benchmark 4, and
neither cause is a fact about the language: Rust's default `HashMap` hashes
with SipHash (swap in `FxHash` and the row moves), and C++'s
`std::unordered_map` is required to chain, so it allocates a node per element
and chases pointers. Both are *library defaults you can change*, which is a
much less exciting headline than "C++ is 1.9× slower than C" and is what the
number actually says.

**These are single-threaded — except the garbage collectors.** Go's headline
feature is that spreading this work across cores is nearly free; this suite
deliberately measures none of that, so it understates Go considerably. And
"single-threaded" quietly means *your code*: G1, .NET's server GC, Go's
background marker and V8's parallel scavenger all use spare cores, which is
part of why the managed runtimes look so good on benchmark 5. Run with
`--serial-gc` to confine the runtimes to one thread and see how much of that
row is borrowed cores.

**Benchmark 4 is not the same work in every language.** Three asymmetries to
know before quoting it. The input generator is a real share of the timed loop
in the interpreters — in pure Python the two PRNG calls per word cost several
times the dict work they feed. Java, Python, V8, PHP and Lua cache or intern
string hashes, so they hash each of the 5,000 words once and probe ever after,
while C, C++, Rust, Go, Swift, C# and Ruby re-hash the bytes on all 10M
lookups. And Python and JavaScript pay two map operations per word (get then
set) where the single-lookup idiom exists in C++ (`++counts[w]`), Rust
(`entry()`), Java, Go, C# and Swift. All sixteen still do the same *job*; they
do not all do the same *work*, which is exactly the kind of thing a "hash map
benchmark" is usually hiding.

**Java's ranking depends entirely on how long you let it run.** On `--quick`
it looks 2–3× slower than C; at the standard scale above it averages 1.1×. That
is not noise, it is the JIT reaching steady state. Anyone who benchmarks Java
with a single short cold run is measuring warmup and reporting it as the
language. Run `--quick` and then the standard scale back to back and watch the
Java row move.

**The four interpreters are not interchangeable data points.** Lua being
several times faster than CPython on these loops says something about
interpreter design, and nothing about which one you should write your program
in — Lua's whole standard library is smaller than Python's `csv` module. Perl
and Ruby each have benchmarks where they beat Python and benchmarks where they
lose to it, which is exactly why the suite reports per-benchmark rows instead
of one number. And the Lua row here is *reference Lua* — LuaJIT would rewrite
it, but cannot reproduce the shared PRNG (see above) — while the Ruby row runs
with YJIT on, since that JIT ships in the standard binary.

**The result depends on your machine.** CPU, cache sizes, compiler version and
what else is running all move these numbers. Run it yourself; don't trust a
screenshot, including the one above.

**Performance is one axis of maybe eight.** Development speed, memory safety,
library ecosystem, hiring, deployment, and how likely you are to ship a
use-after-free are all absent here. A language being 30× slower than C is
frequently, and correctly, irrelevant.

---

## Files

```
bench.asm     Assembly     nasm -felf64, linked against libc (x86-64 Linux only)
bench.c       C            gcc -O3 -march=native -ffp-contract=off
bench.cpp     C++          g++ -O3 -march=native -ffp-contract=off -std=c++17
bench.rs      Rust         rustc -Copt-level=3 -Ctarget-cpu=native (no Cargo, no crates, no unsafe)
bench.swift   Swift        swiftc -Ounchecked (no packages, no Foundation)
bench.go      Go           go build, GOAMD64=v3 on x86-64
Bench.java    Java         javac
bench.cs      C#           dotnet build -c Release, server GC (run.py generates the csproj in build/)
bench.js      JavaScript   node, no flags
bench.lua     Lua          lua 5.3+, reference interpreter
bench.pl      Perl         perl 5, core modules only
bench.php     PHP          php CLI + opcache tracing JIT
bench.py      Python       plain interpreter, no numpy
bench_numpy.py  NumPy      the same python3, loops in NumPy/BLAS (enters 3 of the 6)
bench.rb      Ruby         ruby --yjit
bench.cob     COBOL        cobc -x -O3 -fno-trunc -fstatic-call (GnuCOBOL 3+)
run.py        builds, runs, compares, prints
golden.json   cross-verified checksums for the stock sizes (see honesty rails)
tests/        unit tests plus the cross-language oracle at tiny sizes:
              python3 -m unittest discover tests
tools/golden.py           regenerates or verifies golden.json from at least
                          two independent implementations
.github/workflows/ci.yml  lint, unit tests, PRNG selftest, a strict --quick
                          suite, golden verification and an FMA-drift canary,
                          on Linux and macOS
```

Summary tables and the leaderboard use the **geometric mean** of the
per-benchmark slowdowns (Fleming & Wallace, CACM 1986): unlike the arithmetic
mean it does not overweight the worst benchmark, and the ranking cannot change
with the choice of baseline. `results.json` also records each run's peak RSS
per language and benchmark — Lua's 1 GB sieve is a finding, so it is measured
rather than asserted.

`run.py` also writes a few files into `build/`, all safe to delete:

- `timings.json` — how long everything took last time, used only to give the
  progress bars something to fill against; never read back into a result.
- `buildinfo.json` — which compile flags each binary actually got (fallbacks
  included), so a cached binary can still report them.
- `report.html` — the run's numbers as charts: a leaderboard, one bar chart
  per benchmark, startup costs and the full table. Self-contained, no
  network, light and dark mode. Open it in any browser.
- `results.json` — the same numbers for anything that would rather parse
  than look.

Compiled languages are only rebuilt when their source (or `run.py`, which
holds the flags) is newer than the binary, and all builds run in parallel --
a repeat run spends its time benchmarking, not compiling. Touch the source
or delete `build/` to force a rebuild.

Each benchmark program shares one interface, so you can also run them alone:

```
./build/bench_asm  mandelbrot 800 3    # <benchmark> <size> [reps]
./build/bench_c    mandelbrot 800 3
./build/bench_cpp  mandelbrot 800 3
./build/bench_rust sieve 50000000
./build/bench_swift sieve 50000000
./build/bench_go   sieve 50000000
java -cp build Bench quicksort 4000000
dotnet build/csharp/out/bench_cs.dll quicksort 4000000
node bench.js      quicksort 4000000
lua bench.lua      wordcount 10000000
perl bench.pl      binarytrees 4000
php bench.php      binarytrees 4000
python3 bench.py   wordcount 10000000
ruby bench.rb      matmul 500
./build/bench_cobol binarytrees 4000
```

Output is always `OK <benchmark> <checksum> <milliseconds>`.

---

## Requirements

Any subset works — missing toolchains are skipped with a note, and the runner
only needs two languages present to have something to compare.

- **Assembly** — `nasm`, plus a C compiler to link against libc; x86-64 Linux only, skipped elsewhere
- **C** — `gcc` or `clang`
- **C++** — `g++` or `clang++` (C++17)
- **Rust** — `rustc`; Cargo is not needed, `bench.rs` depends on nothing
- **Swift** — `swiftc` (the toolchain alone; no packages, no Foundation).
  On Nix, `swiftc` only works inside Swift's own clang stdenv — `shell.nix`
  here sets that up
- **Go** — `go`
- **Java** — a **JDK** (needs `javac`, not just `java`)
- **C#** — the **.NET SDK** (`dotnet`, not just a runtime)
- **JavaScript** — `node`
- **Lua** — `lua` **5.3 or newer**; LuaJIT and 5.1/5.2 are detected and skipped
- **Perl** — `perl` 5, a 64-bit build (core modules only; the PRNG needs 64-bit integers)
- **PHP** — `php` 7.3 or newer (needs `hrtime`; the runner enables opcache's
  tracing JIT when the extension is present, and runs without it otherwise)
- **Python** — `python3` (also required to run `run.py` itself)
- **NumPy** — any `python3` that can `import numpy` (the row is skipped with a
  note otherwise)
- **Ruby** — `ruby`
- **COBOL** — GnuCOBOL **3 or newer** (`cobc`), plus a little-endian machine
  for the PRNG's `REDEFINES` overlay — every x86-64 and mainstream ARM box
  qualifies (a checksum mismatch, not a wrong result, if it ever doesn't)

Also: about **1 GB of free RAM** at the standard scale if Lua is in the run
(6 GB at `--heavy`) — see "Where the sixteen files genuinely differ" above.

## Options

```
--quick            tiny workload, a few seconds
--heavy            6x the standard workload (needs ~6 GB free if Lua is in)
--scale N          arbitrary multiplier (--scale 50 for something brutal)
--reps N           runs per language, fastest wins (default 3)
--min-time T       grow --reps per language until the timed compute should
                   last at least T seconds (cap 50): fast languages repeat
                   more instead of reporting sub-millisecond timings
--warmup N         N untimed in-process runs before the timed ones, honoured
                   by the JIT runtimes (Java, C#, JavaScript, PHP, Ruby) --
                   the JMH treatment for cold-start complaints
--pin [CORE]       pin every benchmark process to one CPU core via taskset
                   (steadier numbers, the way the Benchmarks Game runs; note
                   it also squeezes JIT compiler threads onto that core)
--serial-gc        confine the GC runtimes to one thread (Java serial GC,
                   .NET workstation GC, GOMAXPROCS=1, V8 single-threaded GC)
--shuffle          fresh random language order per benchmark, so nobody
                   always runs coldest or hottest
--seed N           seed for --shuffle, recorded in results.json so a
                   shuffled run can be reproduced exactly
--strict           exit non-zero if any language fails, times out, or is
                   missing from --require -- for CI; checksum failures
                   always exit non-zero, flag or no flag
--require a,b      language keys that must be available; with --strict a
                   missing one fails the run instead of shrinking it
--selftest         verify every language's PRNG bit-for-bit and exit
--only a,b         just these benchmarks
--skip-bench a,b   exclude benchmarks, keep everything else
--skip python,lua  exclude languages, e.g. when they are the slow part
--racing           grand prix mode: every language is a racecar, each
                   benchmark replays as an animated race, podium at the end
--no-animation     keep the colour, but no bars and nothing moves
--plain            no colour, no banner, no animation -- for logs and pipes
```

`--skip` takes either key or name: `asm c cpp rust swift go java csharp js
lua perl php python numpy ruby cobol`, or `assembly`, `c++`, `c#`,
`javascript`.
`--skip-bench` takes benchmark keys: `mandelbrot sieve quicksort wordcount
binarytrees matmul` — the complement of `--only`, for when you want to drop
one or two rather than name the rest.
COBOL roughly doubles the standard run's wall time all by itself; `--skip
cobol` when that stops being funny.

Colour is on whenever stdout is a terminal. The runner uses 24-bit colour if
the terminal advertises it and falls back to the basic 16 otherwise. Turn it
off with `NO_COLOR=1` or `--plain`; force the 16-colour path with
`SPEEDTEST_NO_TRUECOLOR=1`. Piping to a file already strips everything.
