; bench.asm -- the x86-64 assembly entry in the language speed comparison.
;
; Usage: bench_asm <benchmark> <size> [reps]
; Prints: OK <benchmark> <checksum> <compute_milliseconds>
;
; Same algorithm, same deterministic input, same checksum as every other
; bench.* in this suite.
;
; Hand-written NASM for x86-64 Linux (System V ABI), linked against libc for
; malloc/free, strcmp, clock_gettime and printf -- the same runtime services
; every compiled entry gets from its standard library. Scalar SSE2 only
; (which is simply where x86-64 keeps its doubles), no unrolling, no
; instruction scheduling cleverness: this is the honest "what you write is
; what runs" entry. Where gcc -O2 beats it -- and on some benchmarks it
; will -- the gap is the value of fifty years of compiler engineering,
; measured directly.

default rel

extern printf
extern fprintf
extern stderr
extern malloc
extern calloc
extern free
extern strcmp
extern atoi
extern clock_gettime
extern exit

global main

%define VOCAB     5000
%define TBL_SIZE  16384          ; 1 << 14, slots of {key ptr, count} = 16 bytes

section .rodata

fmt_ok:        db "OK %s %lld %.3f", 10, 0
msg_usage:     db "usage: %s <benchmark> <size> [reps]", 10, 0
msg_unknown:   db "unknown benchmark: %s", 10, 0
msg_oom:       db "out of memory", 10, 0
msg_nondet:    db "nondeterministic result!", 10, 0

n_mandelbrot:  db "mandelbrot", 0
n_sieve:       db "sieve", 0
n_quicksort:   db "quicksort", 0
n_wordcount:   db "wordcount", 0
n_binarytrees: db "binarytrees", 0
n_matmul:      db "matmul", 0
n_prng:        db "prng", 0

align 8
d_one:         dq 1.0
d_onehalf:     dq 1.5
d_two:         dq 2.0
d_four:        dq 4.0
d_thousand:    dq 1000.0
d_million:     dq 1.0e6
d_huge:        dq 1.0e300

section .bss

rng_state:     resq 1
words:         resb VOCAB * 16   ; the wordcount vocabulary, 16 bytes a slot

section .text

; ---------- double now_ms(void) -- CLOCK_MONOTONIC as milliseconds ----------
now_ms:
    sub     rsp, 24              ; struct timespec + alignment
    mov     edi, 1               ; CLOCK_MONOTONIC
    mov     rsi, rsp
    call    clock_gettime
    cvtsi2sd xmm0, qword [rsp]   ; tv_sec
    mulsd   xmm0, [d_thousand]
    cvtsi2sd xmm1, qword [rsp+8] ; tv_nsec
    divsd   xmm1, [d_million]
    addsd   xmm0, xmm1
    add     rsp, 24
    ret

; ---------- shared deterministic PRNG (identical in every language here) ----------
; state = state * 6364136223846793005 + 1442695040888963407; return top 31 bits.
; imul keeps the low 64 bits of the product, which *is* arithmetic mod 2^64.
; Clobbers rax and rdx only; result in eax.
rng_next:
    mov     rax, [rng_state]
    mov     rdx, 6364136223846793005
    imul    rax, rdx
    mov     rdx, 1442695040888963407
    add     rax, rdx
    mov     [rng_state], rax
    shr     rax, 33
    ret

; ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
; edi = n; returns total in rax.
bench_mandelbrot:
    xor     r8, r8               ; total
    pxor    xmm15, xmm15
    cvtsi2sd xmm15, edi          ; (double)n
    movsd   xmm14, [d_two]
    movsd   xmm13, [d_four]
    xor     r9d, r9d             ; py = 0
.py_loop:
    cmp     r9d, edi
    jge     .done
    pxor    xmm2, xmm2
    cvtsi2sd xmm2, r9d
    mulsd   xmm2, xmm14
    divsd   xmm2, xmm15
    subsd   xmm2, [d_one]        ; ci = 2.0*py/n - 1.0
    xor     r10d, r10d           ; px = 0
.px_loop:
    cmp     r10d, edi
    jge     .py_next
    pxor    xmm3, xmm3
    cvtsi2sd xmm3, r10d
    mulsd   xmm3, xmm14
    divsd   xmm3, xmm15
    subsd   xmm3, [d_onehalf]    ; cr = 2.0*px/n - 1.5
    xorpd   xmm4, xmm4           ; zr = 0.0
    xorpd   xmm5, xmm5           ; zi = 0.0
    xor     ecx, ecx             ; i = 0
.iter:
    cmp     ecx, 255
    jge     .iter_done
    movapd  xmm6, xmm4
    mulsd   xmm6, xmm4           ; zr2 = zr*zr
    movapd  xmm7, xmm5
    mulsd   xmm7, xmm5           ; zi2 = zi*zi
    movapd  xmm0, xmm6
    addsd   xmm0, xmm7
    ucomisd xmm0, xmm13
    ja      .iter_done           ; zr2 + zi2 > 4.0
    movapd  xmm0, xmm4
    mulsd   xmm0, xmm14          ; 2.0*zr
    mulsd   xmm0, xmm5           ; *zi
    addsd   xmm0, xmm2           ; +ci  (the new zi, from the OLD zr)
    movapd  xmm4, xmm6
    subsd   xmm4, xmm7
    addsd   xmm4, xmm3           ; zr = zr2 - zi2 + cr
    movapd  xmm5, xmm0
    inc     ecx
    jmp     .iter
.iter_done:
    add     r8, rcx              ; total += i
    inc     r10d
    jmp     .px_loop
.py_next:
    inc     r9d
    jmp     .py_loop
.done:
    mov     rax, r8
    ret

; ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
; edi = n; returns prime count in rax.
bench_sieve:
    push    rbx
    push    r12
    push    r13
    mov     r12d, edi            ; n (zero-extended)
    lea     rdi, [r12+1]
    mov     esi, 1
    call    calloc
    test    rax, rax
    jz      oom_exit
    mov     rbx, rax             ; comp
    xor     r13, r13             ; count
    mov     rcx, 2               ; i
.outer:
    cmp     rcx, r12
    jg      .done
    cmp     byte [rbx+rcx], 0
    jne     .next
    inc     r13
    mov     rax, rcx
    imul    rax, rcx             ; j = i*i (64-bit, i*i can pass 2^32)
.inner:
    cmp     rax, r12
    jg      .next
    mov     byte [rbx+rax], 1
    add     rax, rcx
    jmp     .inner
.next:
    inc     rcx
    jmp     .outer
.done:
    mov     rdi, rbx
    call    free
    mov     rax, r13
    pop     r13
    pop     r12
    pop     rbx
    ret

; ---------- 3. quicksort: branches, swaps, recursion, random memory access ----------
; The values are unsigned 32-bit, so every value comparison below is jae/jbe
; (unsigned); every index comparison is jg/jl (signed). Getting those two
; families of jcc straight is the entire price of writing this by hand.

; insertion_sort(rdi = a, esi = lo, edx = hi)
insertion_sort:
    movsxd  rsi, esi
    movsxd  rdx, edx
    mov     rax, rsi
    inc     rax                  ; i = lo + 1
.loop_i:
    cmp     rax, rdx
    jg      .done
    mov     ecx, [rdi+rax*4]     ; v = a[i]
    mov     r8, rax
    dec     r8                   ; j = i - 1
.loop_j:
    cmp     r8, rsi
    jl      .insert
    mov     r9d, [rdi+r8*4]
    cmp     r9d, ecx
    jbe     .insert              ; a[j] <= v: stop
    mov     [rdi+r8*4+4], r9d    ; a[j+1] = a[j]
    dec     r8
    jmp     .loop_j
.insert:
    mov     [rdi+r8*4+4], ecx    ; a[j+1] = v
    inc     rax
    jmp     .loop_i
.done:
    ret

; quicksort(rdi = a, esi = lo, edx = hi), recursive
quicksort:
    push    rbx
    push    r12
    push    r13
    mov     rbx, rdi             ; a
    mov     esi, esi             ; zero the upper halves: the ABI leaves them
    mov     edx, edx             ; undefined and we use rsi/rdx to address
.top:
    mov     eax, edx
    sub     eax, esi
    cmp     eax, 16
    jle     .small
    mov     ecx, eax
    shr     ecx, 1
    add     ecx, esi             ; mid = lo + (hi-lo)/2
    ; median-of-three: order a[lo] <= a[mid] <= a[hi]
    mov     r8d, [rbx+rcx*4]
    mov     r9d, [rbx+rsi*4]
    cmp     r8d, r9d
    jae     .m1
    mov     [rbx+rcx*4], r9d
    mov     [rbx+rsi*4], r8d
.m1:
    mov     r8d, [rbx+rdx*4]
    mov     r9d, [rbx+rsi*4]
    cmp     r8d, r9d
    jae     .m2
    mov     [rbx+rdx*4], r9d
    mov     [rbx+rsi*4], r8d
.m2:
    mov     r8d, [rbx+rdx*4]
    mov     r9d, [rbx+rcx*4]
    cmp     r8d, r9d
    jae     .m3
    mov     [rbx+rdx*4], r9d
    mov     [rbx+rcx*4], r8d
.m3:
    mov     r10d, [rbx+rcx*4]    ; pivot = a[mid]
    mov     eax, esi             ; i = lo
    mov     ecx, edx             ; j = hi
.part:
    cmp     eax, ecx
    jg      .part_done
.scan_i:
    mov     r8d, [rbx+rax*4]
    cmp     r8d, r10d
    jae     .scan_j              ; a[i] >= pivot: stop
    inc     eax
    jmp     .scan_i
.scan_j:
    mov     r9d, [rbx+rcx*4]
    cmp     r9d, r10d
    jbe     .maybe_swap          ; a[j] <= pivot: stop
    dec     ecx
    jmp     .scan_j
.maybe_swap:
    cmp     eax, ecx
    jg      .part_done
    mov     [rbx+rax*4], r9d     ; swap a[i], a[j]
    mov     [rbx+rcx*4], r8d
    inc     eax
    dec     ecx
    jmp     .part
.part_done:
    ; recurse into the smaller half, loop on the larger: depth stays O(log n)
    mov     r8d, ecx
    sub     r8d, esi             ; j - lo
    mov     r9d, edx
    sub     r9d, eax             ; hi - i
    cmp     r8d, r9d
    jge     .right_first
    mov     r12d, eax            ; save i
    mov     r13d, edx            ; save hi
    mov     rdi, rbx
    mov     edx, ecx             ; quicksort(a, lo, j)
    call    quicksort
    mov     esi, r12d            ; lo = i
    mov     edx, r13d
    jmp     .top
.right_first:
    mov     r12d, esi            ; save lo
    mov     r13d, ecx            ; save j
    mov     rdi, rbx
    mov     esi, eax             ; quicksort(a, i, hi)
    call    quicksort
    mov     esi, r12d
    mov     edx, r13d            ; hi = j
    jmp     .top
.small:
    mov     rdi, rbx
    call    insertion_sort
    pop     r13
    pop     r12
    pop     rbx
    ret

; edi = n; returns checksum in rax.
bench_quicksort:
    push    rbx
    push    r12
    push    r13
    mov     r12d, edi            ; n
    lea     rdi, [r12*4]
    call    malloc
    test    rax, rax
    jz      oom_exit
    mov     rbx, rax             ; a
    mov     qword [rng_state], 12345
    xor     r13d, r13d           ; i
.fill:
    cmp     r13d, r12d
    jge     .fill_done
    call    rng_next
    mov     [rbx+r13*4], eax
    inc     r13d
    jmp     .fill
.fill_done:
    mov     rdi, rbx
    xor     esi, esi
    mov     edx, r12d
    dec     edx
    call    quicksort
    xor     eax, eax             ; h
    xor     ecx, ecx             ; i
.sum:
    cmp     ecx, r12d
    jge     .sum_done
    imul    eax, eax, 31
    add     eax, [rbx+rcx*4]     ; h = h*31 + a[i]  (order-sensitive checksum)
    inc     ecx
    jmp     .sum
.sum_done:
    mov     r13d, eax
    mov     rdi, rbx
    call    free
    mov     eax, r13d            ; zero-extends: (long long)(uint32_t)h
    pop     r13
    pop     r12
    pop     rbx
    ret

; ---------- 4. word count: string hashing + hash map ----------
; The same open-addressing table bench.c hand-rolls, hand-rolled one level
; further down. FNV-1a into 2^14 slots of {char *key, int64 count}, linear
; probing. This is what "just use a dict" costs when nobody wrote it for you.
; edi = n; returns distinct*1000003 + maxcount in rax.
bench_wordcount:
    push    rbx
    push    rbp
    push    r12
    push    r13
    push    r14
    push    r15
    sub     rsp, 8
    mov     r14d, edi            ; n
    ; -- generate the same 5,000-word vocabulary as everyone else --
    mov     qword [rng_state], 12345
    xor     r12d, r12d           ; i
.vocab:
    cmp     r12d, VOCAB
    jge     .vocab_done
    call    rng_next
    xor     edx, edx
    mov     ecx, 6
    div     ecx
    lea     r13d, [rdx+3]        ; len = 3 + rng % 6
    mov     eax, r12d
    shl     eax, 4
    lea     r15, [words]
    add     r15, rax             ; &words[i]
    xor     ebp, ebp             ; c
.char:
    cmp     ebp, r13d
    jge     .char_done
    call    rng_next
    xor     edx, edx
    mov     ecx, 26
    div     ecx
    add     edx, 'a'
    mov     [r15+rbp], dl
    inc     ebp
    jmp     .char
.char_done:
    mov     byte [r15+r13], 0
    inc     r12d
    jmp     .vocab
.vocab_done:
    ; -- the table --
    mov     edi, TBL_SIZE
    mov     esi, 16
    call    calloc
    test    rax, rax
    jz      oom_exit
    mov     rbx, rax             ; tbl
    xor     r12d, r12d           ; k
    xor     r13, r13             ; distinct
    xor     r15, r15             ; maxc
.count:
    cmp     r12d, r14d
    jge     .count_done
    call    rng_next
    xor     edx, edx
    mov     ecx, VOCAB
    div     ecx
    mov     ebp, edx             ; ra
    call    rng_next
    xor     edx, edx
    mov     ecx, VOCAB
    div     ecx                  ; edx = rb
    mov     eax, ebp
    imul    eax, edx             ; ra*rb (max 25e6, fits easily)
    xor     edx, edx
    mov     ecx, VOCAB
    div     ecx                  ; (ra*rb)/VOCAB: triangular, so counts vary
    shl     eax, 4
    lea     rbp, [words]
    add     rbp, rax             ; w
    ; FNV-1a
    mov     eax, 2166136261
    mov     rcx, rbp
.hash:
    movzx   edx, byte [rcx]
    test    edx, edx
    jz      .hash_done
    xor     eax, edx
    imul    eax, eax, 16777619
    inc     rcx
    jmp     .hash
.hash_done:
    and     eax, TBL_SIZE - 1    ; idx
.probe:
    mov     rcx, rax
    shl     rcx, 4
    add     rcx, rbx             ; slot = &tbl[idx]
    mov     rdx, [rcx]           ; slot->key
    test    rdx, rdx
    jnz     .occupied
    mov     [rcx], rbp           ; empty: claim it
    mov     qword [rcx+8], 1
    inc     r13                  ; distinct++
    cmp     r15, 1
    jge     .next_k
    mov     r15, 1
    jmp     .next_k
.occupied:
    mov     r8, rdx              ; inline strcmp: equal or not is all we need
    mov     r9, rbp
.cmploop:
    movzx   esi, byte [r8]
    movzx   edi, byte [r9]
    cmp     esi, edi
    jne     .collide
    test    esi, esi
    jz      .match
    inc     r8
    inc     r9
    jmp     .cmploop
.match:
    mov     rdx, [rcx+8]
    inc     rdx
    mov     [rcx+8], rdx
    cmp     rdx, r15
    jle     .next_k
    mov     r15, rdx             ; maxc = count
    jmp     .next_k
.collide:
    inc     eax
    and     eax, TBL_SIZE - 1    ; linear probe onward
    jmp     .probe
.next_k:
    inc     r12d
    jmp     .count
.count_done:
    imul    rax, r13, 1000003
    add     rax, r15
    mov     r12, rax
    mov     rdi, rbx
    call    free
    mov     rax, r12
    add     rsp, 8
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rbp
    pop     rbx
    ret

; ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
; Nothing here computes; the entire cost is malloc, free and following
; pointers. Assembly gets no discount on that: the allocator is the same
; glibc malloc that bench.c calls, one node at a time.

; make_tree(edi = depth) -> rax
make_tree:
    push    rbx
    push    r12
    push    r13                  ; alignment
    mov     ebx, edi
    mov     edi, 16
    call    malloc
    test    rax, rax
    jz      oom_exit
    mov     r12, rax
    test    ebx, ebx
    jz      .leaf
    lea     edi, [rbx-1]
    call    make_tree
    mov     [r12], rax           ; t->l
    lea     edi, [rbx-1]
    call    make_tree
    mov     [r12+8], rax         ; t->r
    jmp     .out
.leaf:
    mov     qword [r12], 0
    mov     qword [r12+8], 0
.out:
    mov     rax, r12
    pop     r13
    pop     r12
    pop     rbx
    ret

; check_tree(rdi = t) -> rax = node count
check_tree:
    push    rbx
    push    r12
    push    r13                  ; alignment
    mov     rbx, rdi
    mov     rdi, [rbx]
    test    rdi, rdi
    jnz     .rec
    mov     eax, 1
    jmp     .out
.rec:
    call    check_tree           ; rdi = t->l already
    mov     r12, rax
    mov     rdi, [rbx+8]
    call    check_tree
    lea     rax, [r12+rax+1]
.out:
    pop     r13
    pop     r12
    pop     rbx
    ret

; free_tree(rdi = t)
free_tree:
    push    rbx
    mov     rbx, rdi
    mov     rdi, [rbx]
    test    rdi, rdi
    jz      .leaf
    call    free_tree
    mov     rdi, [rbx+8]
    call    free_tree
.leaf:
    mov     rdi, rbx
    call    free
    pop     rbx
    ret

; edi = n; returns checksum in rax.
bench_binarytrees:
    push    rbx
    push    r12
    push    r13
    push    r14
    push    r15                  ; alignment
    mov     r12d, edi            ; n
    xor     ebx, ebx             ; h
    xor     r13d, r13d           ; k
.loop:
    cmp     r13d, r12d
    jge     .done
    mov     edi, 11              ; 4,095 nodes, built and thrown away
    call    make_tree
    mov     r14, rax
    mov     rdi, rax
    call    check_tree
    imul    ebx, ebx, 31
    add     ebx, eax             ; h = h*31 + check + k
    add     ebx, r13d
    mov     rdi, r14
    call    free_tree
    inc     r13d
    jmp     .loop
.done:
    mov     eax, ebx
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rbx
    ret

; ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
; edi = n; returns checksum in rax.
bench_matmul:
    push    rbx
    push    rbp
    push    r12
    push    r13
    push    r14
    push    r15
    sub     rsp, 8
    mov     r12d, edi            ; n
    mov     eax, r12d
    imul    eax, r12d
    mov     r13d, eax            ; nn = n*n
    lea     rdi, [r13*4]
    call    malloc
    test    rax, rax
    jz      oom_exit
    mov     rbx, rax             ; a
    lea     rdi, [r13*4]
    call    malloc
    test    rax, rax
    jz      oom_exit
    mov     r14, rax             ; b
    lea     rdi, [r13*4]
    call    malloc
    test    rax, rax
    jz      oom_exit
    mov     r15, rax             ; c
    mov     qword [rng_state], 12345
    xor     ebp, ebp
.fill_a:
    cmp     ebp, r13d
    jge     .fill_a_done
    call    rng_next
    xor     edx, edx
    mov     ecx, 100
    div     ecx
    mov     [rbx+rbp*4], edx
    inc     ebp
    jmp     .fill_a
.fill_a_done:
    xor     ebp, ebp
.fill_b:
    cmp     ebp, r13d
    jge     .fill_b_done
    call    rng_next
    xor     edx, edx
    mov     ecx, 100
    div     ecx
    mov     [r14+rbp*4], edx
    inc     ebp
    jmp     .fill_b
.fill_b_done:
    ; c[i][j] = sum over k of a[i][k] * b[k][j]; the b walk goes down a
    ; column, one cache line sacrificed per element, in every language alike
    xor     esi, esi             ; i
.mi:
    cmp     esi, r12d
    jge     .mul_done
    mov     edi, esi
    imul    edi, r12d            ; ib = i*n
    xor     r8d, r8d             ; j
.mj:
    cmp     r8d, r12d
    jge     .mi_next
    xor     r9d, r9d             ; s
    xor     r10d, r10d           ; k
    lea     r11, [r14+r8*4]      ; &b[j], stride n
    lea     rax, [rbx+rdi*4]     ; &a[ib]
.mk:
    cmp     r10d, r12d
    jge     .mk_done
    mov     edx, [rax+r10*4]     ; a[ib+k]
    imul    edx, [r11]           ; * b[k*n+j]
    add     r9d, edx
    lea     r11, [r11+r12*4]
    inc     r10d
    jmp     .mk
.mk_done:
    mov     ecx, edi
    add     ecx, r8d
    mov     [r15+rcx*4], r9d     ; c[ib+j] = s
    inc     r8d
    jmp     .mj
.mi_next:
    inc     esi
    jmp     .mi
.mul_done:
    xor     eax, eax             ; h
    xor     ecx, ecx
.sum:
    cmp     ecx, r13d
    jge     .sum_done
    imul    eax, eax, 31
    add     eax, [r15+rcx*4]
    inc     ecx
    jmp     .sum
.sum_done:
    mov     ebp, eax
    mov     rdi, rbx
    call    free
    mov     rdi, r14
    call    free
    mov     rdi, r15
    call    free
    mov     eax, ebp
    add     rsp, 8
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rbp
    pop     rbx
    ret

; ---------- dispatch(rdi = name, esi = size) -> rax ----------
dispatch:
    push    rbx
    push    r12
    push    r13
    mov     rbx, rdi
    mov     r12d, esi
    lea     rsi, [n_mandelbrot]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .mandelbrot
    lea     rsi, [n_sieve]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .sieve
    lea     rsi, [n_quicksort]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .quicksort
    lea     rsi, [n_wordcount]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .wordcount
    lea     rsi, [n_binarytrees]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .binarytrees
    lea     rsi, [n_matmul]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .matmul
    lea     rsi, [n_prng]
    mov     rdi, rbx
    call    strcmp
    test    eax, eax
    jz      .prng
    mov     rdi, [stderr]
    lea     rsi, [msg_unknown]
    mov     rdx, rbx
    xor     eax, eax
    call    fprintf
    mov     edi, 2
    call    exit
.mandelbrot:
    mov     edi, r12d
    call    bench_mandelbrot
    jmp     .ret
.sieve:
    mov     edi, r12d
    call    bench_sieve
    jmp     .ret
.quicksort:
    mov     edi, r12d
    call    bench_quicksort
    jmp     .ret
.wordcount:
    mov     edi, r12d
    call    bench_wordcount
    jmp     .ret
.binarytrees:
    mov     edi, r12d
    call    bench_binarytrees
    jmp     .ret
.matmul:
    mov     edi, r12d
    call    bench_matmul
    jmp     .ret
.prng:
    mov     edi, r12d
    call    bench_prng
.ret:
    pop     r13
    pop     r12
    pop     rbx
    ret

; ---------- main(edi = argc, rsi = argv) ----------
main:
    push    rbx
    push    r12
    push    r13
    push    r14
    push    r15
    sub     rsp, 32              ; [rsp]=best  [rsp+8]=result  [rsp+16]=t0  [rsp+24]=v
    mov     r14d, edi            ; argc
    mov     rbx, rsi             ; argv
    cmp     r14d, 3
    jl      .usage
    mov     rdi, [rbx+16]        ; argv[2]
    call    atoi
    mov     r12d, eax            ; size
    mov     r13d, 1              ; reps
    cmp     r14d, 4
    jl      .reps_done
    mov     rdi, [rbx+24]        ; argv[3]
    call    atoi
    mov     r13d, eax
    cmp     r13d, 1
    jge     .reps_done
    mov     r13d, 1
.reps_done:
    ; Best-of-N: the fastest run is the one least polluted by scheduler noise
    ; and cold caches. (No JIT to warm up here; the "compile" already happened.)
    movsd   xmm0, [d_huge]
    movsd   [rsp], xmm0          ; best
    xor     r15d, r15d           ; r
.rep:
    cmp     r15d, r13d
    jge     .report
    call    now_ms
    movsd   [rsp+16], xmm0
    mov     rdi, [rbx+8]         ; argv[1]
    mov     esi, r12d
    call    dispatch
    mov     [rsp+24], rax
    call    now_ms
    subsd   xmm0, [rsp+16]       ; elapsed
    movsd   xmm1, [rsp]
    ucomisd xmm0, xmm1
    jae     .no_best
    movsd   [rsp], xmm0
.no_best:
    test    r15d, r15d
    jnz     .check
    mov     rax, [rsp+24]
    mov     [rsp+8], rax         ; result = v
    jmp     .rep_next
.check:
    mov     rax, [rsp+24]
    cmp     rax, [rsp+8]
    je      .rep_next
    mov     rdi, [stderr]
    lea     rsi, [msg_nondet]
    xor     eax, eax
    call    fprintf
    mov     eax, 3
    jmp     .out
.rep_next:
    inc     r15d
    jmp     .rep
.report:
    lea     rdi, [fmt_ok]
    mov     rsi, [rbx+8]
    mov     rdx, [rsp+8]
    movsd   xmm0, [rsp]
    mov     eax, 1               ; one vector register holds a vararg
    call    printf
    xor     eax, eax
.out:
    add     rsp, 32
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rbx
    ret
.usage:
    mov     rdi, [stderr]
    lea     rsi, [msg_usage]
    mov     rdx, [rbx]           ; argv[0]
    xor     eax, eax
    call    fprintf
    mov     eax, 2
    jmp     .out

; ---------- hidden: prng conformance -- not part of the scored suite ----------
; run.py --selftest uses it to validate each language's PRNG directly.
; edi = n; h = (h*31 + rng_next()) mod 2^32, seed 12345; returns h in rax.
bench_prng:
    push    rbx
    push    r12
    mov     r12d, edi
    mov     rax, 12345
    mov     [rng_state], rax
    xor     ebx, ebx             ; h
    test    r12d, r12d
    jle     .done
.loop:
    call    rng_next             ; clobbers rax/rdx only; result in eax
    mov     edx, ebx
    shl     edx, 5
    sub     edx, ebx             ; h*31, wrapping in 32 bits
    add     edx, eax
    mov     ebx, edx
    dec     r12d
    jnz     .loop
.done:
    mov     eax, ebx             ; zero-extends into rax
    pop     r12
    pop     rbx
    ret

; ---------- shared failure exit ----------
oom_exit:
    and     rsp, -16             ; jumped to from arbitrary frames; exit() never returns
    mov     rdi, [stderr]
    lea     rsi, [msg_oom]
    xor     eax, eax
    call    fprintf
    mov     edi, 1
    call    exit

section .note.GNU-stack noalloc noexec nowrite progbits
