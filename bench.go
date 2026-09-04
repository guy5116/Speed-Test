// bench.go -- the Go entry in the language speed comparison.
//
// Usage: bench <benchmark> <size>
// Prints: OK <benchmark> <checksum> <compute_milliseconds>
//
// Same algorithm, same deterministic input, same checksum as every other
// bench.* in this suite.
package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"time"
)

// ---------- shared deterministic PRNG (identical in every language here) ----------
var rngState uint64

func rngSeed(s uint64) { rngState = s }

func rngNext() uint32 {
	rngState = rngState*6364136223846793005 + 1442695040888963407
	return uint32(rngState >> 33) // top 31 bits
}

// ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
func benchMandelbrot(n int) int64 {
	var total int64
	fn := float64(n)
	for py := 0; py < n; py++ {
		ci := 2.0*float64(py)/fn - 1.0
		for px := 0; px < n; px++ {
			cr := 2.0*float64(px)/fn - 1.5
			zr, zi := 0.0, 0.0
			i := 0
			for i < 255 {
				zr2, zi2 := zr*zr, zi*zi
				if zr2+zi2 > 4.0 {
					break
				}
				zi = 2.0*zr*zi + ci
				zr = zr2 - zi2 + cr
				i++
			}
			total += int64(i)
		}
	}
	return total
}

// ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
func benchSieve(n int) int64 {
	comp := make([]byte, n+1)
	var count int64
	for i := 2; i <= n; i++ {
		if comp[i] == 0 {
			count++
			for j := i * i; j <= n; j += i {
				comp[j] = 1
			}
		}
	}
	return count
}

// ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
func insertionSort(a []uint32, lo, hi int) {
	for i := lo + 1; i <= hi; i++ {
		v := a[i]
		j := i - 1
		for j >= lo && a[j] > v {
			a[j+1] = a[j]
			j--
		}
		a[j+1] = v
	}
}

func quicksort(a []uint32, lo, hi int) {
	for hi-lo > 16 {
		mid := lo + (hi-lo)/2
		// median-of-three: order a[lo] <= a[mid] <= a[hi]
		if a[mid] < a[lo] {
			a[mid], a[lo] = a[lo], a[mid]
		}
		if a[hi] < a[lo] {
			a[hi], a[lo] = a[lo], a[hi]
		}
		if a[hi] < a[mid] {
			a[hi], a[mid] = a[mid], a[hi]
		}
		pivot := a[mid]

		i, j := lo, hi
		for i <= j {
			for a[i] < pivot {
				i++
			}
			for a[j] > pivot {
				j--
			}
			if i <= j {
				a[i], a[j] = a[j], a[i]
				i++
				j--
			}
		}
		// recurse into the smaller half, loop on the larger: depth stays O(log n)
		if j-lo < hi-i {
			quicksort(a, lo, j)
			lo = i
		} else {
			quicksort(a, i, hi)
			hi = j
		}
	}
	insertionSort(a, lo, hi)
}

func benchQuicksort(n int) int64 {
	a := make([]uint32, n)
	rngSeed(12345)
	for i := 0; i < n; i++ {
		a[i] = rngNext()
	}
	quicksort(a, 0, n-1)
	var h uint32
	for _, v := range a {
		h = h*31 + v // order-sensitive checksum
	}
	return int64(h)
}

// ---------- 4. word count: string hashing + hash map ----------
// Go gets a built-in map, so this is ~5 lines instead of the ~40 in bench.c.
const vocabSize = 5000

func benchWordcount(n int) int64 {
	words := make([]string, vocabSize)
	rngSeed(12345)
	for i := 0; i < vocabSize; i++ {
		length := 3 + int(rngNext()%6)
		buf := make([]byte, length)
		for c := 0; c < length; c++ {
			buf[c] = byte('a' + rngNext()%26)
		}
		words[i] = string(buf)
	}

	// *int slots: counts[w]++ plus the max check would be two or three map
	// lookups per word; holding the pointer makes it one, and the 5,000
	// little allocations happen once per distinct word, not per lookup
	counts := make(map[string]*int, 8192)
	var maxc int64
	for k := 0; k < n; k++ {
		ra := rngNext() % vocabSize
		rb := rngNext() % vocabSize
		w := words[(ra*rb)/vocabSize] // triangular, so counts vary
		p := counts[w]
		if p == nil {
			p = new(int)
			counts[w] = p
		}
		*p++
		if int64(*p) > maxc {
			maxc = int64(*p)
		}
	}
	return int64(len(counts))*1000003 + maxc
}

// ---------- 5. binary trees: allocation, pointer chasing, garbage collection ----------
// Nothing here computes; the entire cost is allocating nodes and letting the
// GC take them back. This is the benchmark Go's runtime exists to survive.
type node struct{ l, r *node }

func makeTree(d int) *node {
	if d == 0 {
		return &node{}
	}
	return &node{makeTree(d - 1), makeTree(d - 1)}
}

func checkTree(t *node) int64 {
	if t.l == nil {
		return 1
	}
	return 1 + checkTree(t.l) + checkTree(t.r)
}

func benchBinarytrees(n int) int64 {
	var h uint32
	for k := 0; k < n; k++ {
		t := makeTree(11) // 4,095 nodes, built and thrown away
		h = h*31 + uint32(checkTree(t)) + uint32(k)
	}
	return int64(h)
}

// ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
func benchMatmul(n int) int64 {
	a := make([]uint32, n*n)
	b := make([]uint32, n*n)
	c := make([]uint32, n*n)
	rngSeed(12345)
	for i := range a {
		a[i] = rngNext() % 100
	}
	for i := range b {
		b[i] = rngNext() % 100
	}
	for i := 0; i < n; i++ {
		ib := i * n
		// ranging over the row slice drops a's bounds check, and the
		// additive bk replaces the k*n multiply the compiler was emitting
		arow := a[ib : ib+n]
		for j := 0; j < n; j++ {
			var s uint32
			bk := j
			for _, av := range arow {
				s += av * b[bk]
				bk += n
			}
			c[ib+j] = s
		}
	}
	var h uint32
	for _, v := range c {
		h = h*31 + v
	}
	return int64(h)
}

func dispatch(name string, size int) int64 {
	switch name {
	case "mandelbrot":
		return benchMandelbrot(size)
	case "sieve":
		return benchSieve(size)
	case "quicksort":
		return benchQuicksort(size)
	case "wordcount":
		return benchWordcount(size)
	case "binarytrees":
		return benchBinarytrees(size)
	case "matmul":
		return benchMatmul(size)
	}
	fmt.Fprintf(os.Stderr, "unknown benchmark: %s\n", name)
	os.Exit(2)
	return 0
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintf(os.Stderr, "usage: %s <benchmark> <size> [reps]\n", os.Args[0])
		os.Exit(2)
	}
	name := os.Args[1]
	size, err := strconv.Atoi(os.Args[2])
	if err != nil {
		fmt.Fprintf(os.Stderr, "bad size: %v\n", err)
		os.Exit(2)
	}
	reps := 1
	if len(os.Args) > 3 {
		reps, _ = strconv.Atoi(os.Args[3])
	}
	if reps < 1 {
		reps = 1
	}

	// Best-of-N: the fastest run is the one least polluted by scheduler noise,
	// cold caches and (for the JIT languages) not-yet-compiled code.
	best := math.Inf(1)
	var result int64
	for r := 0; r < reps; r++ {
		start := time.Now()
		v := dispatch(name, size)
		elapsed := float64(time.Since(start).Nanoseconds()) / 1e6
		if elapsed < best {
			best = elapsed
		}
		if r == 0 {
			result = v
		} else if v != result {
			fmt.Fprintln(os.Stderr, "nondeterministic result!")
			os.Exit(3)
		}
	}

	fmt.Printf("OK %s %d %.3f\n", name, result, best)
}
