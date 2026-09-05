>>SOURCE FORMAT FREE
*> ---------------------------------------------------------------------
*> bench.cob -- the COBOL entry in the language speed comparison.
*>
*> Usage: bench <benchmark> <size> [reps]
*> Prints: OK <benchmark> <checksum> <best_ms> <median_ms> <worst_ms>
*>
*> Same algorithm, same deterministic input, same checksum as every other
*> bench.* in this suite. GnuCOBOL 3+, compiled to native code via C with
*>     cobc -x -O2 -fno-trunc -fstatic-call
*> (-fno-trunc gives binary fields C-style wraparound instead of decimal
*> truncation; -fstatic-call links CALL "malloc"/"free"/"clock_gettime"
*> straight to libc).
*>
*> COBOL needed a few judgement calls to play, all visible below and all
*> noted in the README:
*>   - The shared 64-bit PRNG is done in 32-bit halves: COBOL arithmetic
*>     is decimal with a 38-digit ceiling, and a full 64x64 multiply needs
*>     39. Same tax JavaScript and PHP pay, for their own reasons. The
*>     halves and carries come out of a REDEFINES overlay (little-endian),
*>     because GnuCOBOL's FUNCTION MOD costs ~3us per call and a plain
*>     COMPUTE costs ~70ns.
*>   - Tables are sized at compile time, so the big arrays live in
*>     CALL "malloc" memory addressed through BASED items.
*>   - PERFORM cannot recurse, so quicksort and the trees use explicit
*>     stacks. Same shape, same work, no call frames.
*>   - The runner sits COBOL out of mandelbrot by default: GnuCOBOL routes
*>     every floating-point operation through its decimal engine at ~3us
*>     apiece, roughly 2000x C. The implementation below is exact (it
*>     matches the shared checksum) -- run it directly if you want to
*>     watch: ./build/bench_cobol mandelbrot 200 1
*> ---------------------------------------------------------------------
IDENTIFICATION DIVISION.
PROGRAM-ID. bench.

DATA DIVISION.
WORKING-STORAGE SECTION.

*> ---------- driver state ----------
01 WS-ARGC          PIC 9(4).
01 WS-BENCH         PIC X(20).
01 WS-ARG-TXT       PIC X(20).
01 WS-SIZE          USAGE BINARY-DOUBLE.
01 WS-N32           USAGE BINARY-LONG.
01 WS-REPS          USAGE BINARY-LONG.
01 WS-R             USAGE BINARY-LONG.
01 WS-RESULT        USAGE BINARY-DOUBLE.
01 WS-FIRST         USAGE BINARY-DOUBLE.
*> hidden prng conformance benchmark (run.py --selftest)
01 PR-H             USAGE BINARY-LONG UNSIGNED.
01 PR-I             USAGE BINARY-DOUBLE.
01 WS-NOW-MS        USAGE COMP-2.
01 WS-T0            USAGE COMP-2.
01 WS-ELAPSED       USAGE COMP-2.
*> every rep's time, so the report can carry best, median and worst;
*> 100,000 slots is 800 KB of BSS and far beyond any sane --reps
01 WS-TIMES-TAB.
   05 WS-TIME       OCCURS 100000 USAGE COMP-2.
01 WS-SI            USAGE BINARY-LONG.
01 WS-SJ            USAGE BINARY-LONG.
01 WS-MED-IX        USAGE BINARY-LONG.
01 WS-ST            USAGE COMP-2.
01 WS-DONE          PIC 9.
01 OUT-CHK          PIC Z(18)9.
01 OUT-MS           PIC Z(9)9.999.
01 OUT-MS2          PIC Z(9)9.999.
01 OUT-MS3          PIC Z(9)9.999.

01 CGT-TS.
   05 CGT-SEC       PIC S9(18) COMP-5.
   05 CGT-NSEC      PIC S9(18) COMP-5.

*> ---------- shared deterministic PRNG (identical in every language here) ----------
*> state = state * 6364136223846793005 + 1442695040888963407  (mod 2^64),
*> kept as two 32-bit halves; the multiplier splits into 1481765933 * 2^32
*> + 1284865837, the increment into 335903614 * 2^32 + 4150755663. Every
*> intermediate stays under 2^63, and the REDEFINES overlays pick low
*> words and carries out of the 64-bit temporaries for free. rng_next
*> returns the top 31 bits of the state, which live in the high half.
01 RNG-SH           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-SL           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-T1           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-T1-P REDEFINES RNG-T1.
   05 RNG-T1-LO     USAGE BINARY-LONG UNSIGNED.
   05 RNG-T1-HI     USAGE BINARY-LONG UNSIGNED.
01 RNG-T2           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-T2-P REDEFINES RNG-T2.
   05 RNG-T2-LO     USAGE BINARY-LONG UNSIGNED.
   05 RNG-T2-HI     USAGE BINARY-LONG UNSIGNED.
01 RNG-T3           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-T3-P REDEFINES RNG-T3.
   05 RNG-T3-LO     USAGE BINARY-LONG UNSIGNED.
   05 RNG-T3-HI     USAGE BINARY-LONG UNSIGNED.
01 RNG-T4           USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-T4-P REDEFINES RNG-T4.
   05 RNG-T4-LO     USAGE BINARY-LONG UNSIGNED.
   05 RNG-T4-HI     USAGE BINARY-LONG UNSIGNED.
01 RNG-OUT          USAGE BINARY-DOUBLE UNSIGNED.
01 RNG-Q            USAGE BINARY-DOUBLE UNSIGNED.

*> ---------- 1. mandelbrot ----------
01 MB-STATE.
   05 MB-TOTAL      USAGE BINARY-DOUBLE.
   05 MB-PY         USAGE BINARY-LONG.
   05 MB-PX         USAGE BINARY-LONG.
   05 MB-ITER       USAGE BINARY-LONG.
   05 MB-FN         USAGE COMP-2.
   05 MB-FP         USAGE COMP-2.
   05 MB-CI         USAGE COMP-2.
   05 MB-CR         USAGE COMP-2.
   05 MB-ZR         USAGE COMP-2.
   05 MB-ZI         USAGE COMP-2.
   05 MB-ZR2        USAGE COMP-2.
   05 MB-ZI2        USAGE COMP-2.

*> ---------- 2. sieve ----------
01 SV-PTR           USAGE POINTER.
01 SV-TAB BASED.
   05 SV-FLAG       USAGE BINARY-CHAR UNSIGNED OCCURS 100000000 TIMES.
01 SV-ONE           USAGE BINARY-CHAR UNSIGNED VALUE 1.
01 SV-STATE.
   05 SV-COUNT      USAGE BINARY-DOUBLE.
   05 SV-BYTES      USAGE BINARY-DOUBLE.
   05 SV-I          USAGE BINARY-DOUBLE.
   05 SV-J          USAGE BINARY-DOUBLE.

*> ---------- 3. quicksort ----------
01 QS-PTR           USAGE POINTER.
01 QS-TAB BASED.
   05 QS-EL         USAGE BINARY-LONG UNSIGNED OCCURS 100000000 TIMES.
01 QS-STATE.
   05 QS-BYTES      USAGE BINARY-DOUBLE.
   05 QS-SP         USAGE BINARY-LONG.
   05 QS-LO         USAGE BINARY-LONG.
   05 QS-HI         USAGE BINARY-LONG.
   05 QS-MID        USAGE BINARY-LONG.
   05 QS-I          USAGE BINARY-LONG.
   05 QS-J          USAGE BINARY-LONG.
   05 QS-PIVOT      USAGE BINARY-LONG UNSIGNED.
   05 QS-T          USAGE BINARY-LONG UNSIGNED.
   05 QS-V          USAGE BINARY-LONG UNSIGNED.
   05 QS-H          USAGE BINARY-LONG UNSIGNED.
01 QS-STACK.
   05 QS-SLO        USAGE BINARY-LONG OCCURS 128 TIMES.
   05 QS-SHI        USAGE BINARY-LONG OCCURS 128 TIMES.

*> ---------- 4. word count ----------
*> Each word is kept twice: as text for the equality probes, and as byte
*> values for the hashing -- COBOL's PIC X characters are not numbers,
*> and converting one mid-loop (FUNCTION ORD) costs more than the hash.
*> The same reasoning as Perl's vec() sieve: the honest equivalent, not
*> a shortcut. Every occurrence still hashes all of its bytes.
01 WC-WORDS.
   05 WC-WORD-ENT OCCURS 5000 TIMES.
      10 WC-WORD-TXT PIC X(8).
      10 WC-WORD-LEN USAGE BINARY-LONG.
      10 WC-WORD-BYTE USAGE BINARY-DOUBLE UNSIGNED OCCURS 8 TIMES.
*> open-addressing hash table, 2^14 slots, linear probing -- the same
*> price of admission bench.c pays for having no built-in map
01 WC-TBL.
   05 WC-SLOT OCCURS 16384 TIMES.
      10 WC-SLOT-TXT PIC X(8).
      10 WC-SLOT-LEN USAGE BINARY-LONG.
      10 WC-SLOT-CNT USAGE BINARY-LONG.
01 WS-ALPHA         PIC X(26) VALUE "abcdefghijklmnopqrstuvwxyz".
01 WC-STATE.
   05 WC-K          USAGE BINARY-DOUBLE.
   05 WC-I          USAGE BINARY-LONG.
   05 WC-C          USAGE BINARY-LONG.
   05 WC-CH         USAGE BINARY-LONG.
   05 WC-LEN        USAGE BINARY-LONG.
   05 WC-RA         USAGE BINARY-DOUBLE UNSIGNED.
   05 WC-RB         USAGE BINARY-DOUBLE UNSIGNED.
   05 WC-W          USAGE BINARY-LONG.
   05 WC-H          USAGE BINARY-DOUBLE UNSIGNED.
   05 WC-Q          USAGE BINARY-DOUBLE UNSIGNED.
   05 WC-IDX        USAGE BINARY-LONG.
   05 WC-DISTINCT   USAGE BINARY-LONG.
   05 WC-MAXC       USAGE BINARY-LONG.

*> ---------- 5. binary trees ----------
01 BT-NODE BASED.
   05 BT-L          USAGE POINTER.
   05 BT-R          USAGE POINTER.
01 BT-STATE.
   05 BT-ROOT       USAGE POINTER.
   05 BT-P          USAGE POINTER.
   05 BT-PL         USAGE POINTER.
   05 BT-PR         USAGE POINTER.
   05 BT-SP         USAGE BINARY-LONG.
   05 BT-D          USAGE BINARY-LONG.
   05 BT-K          USAGE BINARY-DOUBLE.
   05 BT-COUNT      USAGE BINARY-DOUBLE.
   05 BT-H          USAGE BINARY-LONG UNSIGNED.
01 BT-STACK.
   05 BT-SPTR       USAGE POINTER OCCURS 64 TIMES.
   05 BT-SDEP       USAGE BINARY-LONG OCCURS 64 TIMES.

*> ---------- 6. matmul ----------
01 MM-PTR-A         USAGE POINTER.
01 MM-PTR-B         USAGE POINTER.
01 MM-PTR-C         USAGE POINTER.
01 MM-TAB-A BASED.
   05 MM-A          USAGE BINARY-LONG UNSIGNED OCCURS 16000000 TIMES.
01 MM-TAB-B BASED.
   05 MM-B          USAGE BINARY-LONG UNSIGNED OCCURS 16000000 TIMES.
01 MM-TAB-C BASED.
   05 MM-C          USAGE BINARY-LONG UNSIGNED OCCURS 16000000 TIMES.
01 MM-STATE.
   05 MM-NN         USAGE BINARY-LONG.
   05 MM-BYTES      USAGE BINARY-DOUBLE.
   05 MM-X          USAGE BINARY-LONG.
   05 MM-I          USAGE BINARY-LONG.
   05 MM-J          USAGE BINARY-LONG.
   05 MM-IA         USAGE BINARY-LONG.
   05 MM-IB         USAGE BINARY-LONG.
   05 MM-IC         USAGE BINARY-LONG.
   05 MM-KB         USAGE BINARY-LONG.
   05 MM-S          USAGE BINARY-LONG UNSIGNED.
   05 MM-H          USAGE BINARY-LONG UNSIGNED.

PROCEDURE DIVISION.
MAIN-PARA.
    ACCEPT WS-ARGC FROM ARGUMENT-NUMBER
    IF WS-ARGC < 2
        DISPLAY "usage: bench <benchmark> <size> [reps]" UPON SYSERR
        MOVE 2 TO RETURN-CODE
        GOBACK
    END-IF
    DISPLAY 1 UPON ARGUMENT-NUMBER
    ACCEPT WS-BENCH FROM ARGUMENT-VALUE
    DISPLAY 2 UPON ARGUMENT-NUMBER
    ACCEPT WS-ARG-TXT FROM ARGUMENT-VALUE
    COMPUTE WS-SIZE = FUNCTION NUMVAL(WS-ARG-TXT)
    MOVE WS-SIZE TO WS-N32
    MOVE 1 TO WS-REPS
    IF WS-ARGC >= 3
        DISPLAY 3 UPON ARGUMENT-NUMBER
        ACCEPT WS-ARG-TXT FROM ARGUMENT-VALUE
        COMPUTE WS-REPS = FUNCTION NUMVAL(WS-ARG-TXT)
    END-IF
    IF WS-REPS < 1
        MOVE 1 TO WS-REPS
    END-IF
    IF WS-REPS > 100000
        MOVE 100000 TO WS-REPS
    END-IF
    EVALUATE WS-BENCH
        WHEN "mandelbrot"
        WHEN "sieve"
        WHEN "quicksort"
        WHEN "wordcount"
        WHEN "binarytrees"
        WHEN "matmul"
        WHEN "prng"
            CONTINUE
        WHEN OTHER
            DISPLAY "unknown benchmark: " FUNCTION TRIM(WS-BENCH)
                UPON SYSERR
            MOVE 2 TO RETURN-CODE
            GOBACK
    END-EVALUATE

    *> Best-of-N: the fastest run is the one least polluted by scheduler
    *> noise and cold caches. Median and worst ride along so the runner
    *> can report the spread.
    PERFORM VARYING WS-R FROM 1 BY 1 UNTIL WS-R > WS-REPS
        PERFORM GET-NOW-MS
        MOVE WS-NOW-MS TO WS-T0
        PERFORM RUN-ONE
        PERFORM GET-NOW-MS
        COMPUTE WS-ELAPSED = WS-NOW-MS - WS-T0
        MOVE WS-ELAPSED TO WS-TIME(WS-R)
        IF WS-R = 1
            MOVE WS-RESULT TO WS-FIRST
        ELSE
            IF WS-RESULT NOT = WS-FIRST
                DISPLAY "nondeterministic result!" UPON SYSERR
                MOVE 3 TO RETURN-CODE
                GOBACK
            END-IF
        END-IF
    END-PERFORM

    *> insertion-sort the rep times; reps is tiny, this costs nothing
    PERFORM VARYING WS-SI FROM 2 BY 1 UNTIL WS-SI > WS-REPS
        MOVE WS-TIME(WS-SI) TO WS-ST
        COMPUTE WS-SJ = WS-SI - 1
        MOVE 0 TO WS-DONE
        PERFORM UNTIL WS-DONE = 1
            IF WS-SJ < 1
                MOVE 1 TO WS-DONE
            ELSE
                IF WS-TIME(WS-SJ) > WS-ST
                    MOVE WS-TIME(WS-SJ) TO WS-TIME(WS-SJ + 1)
                    COMPUTE WS-SJ = WS-SJ - 1
                ELSE
                    MOVE 1 TO WS-DONE
                END-IF
            END-IF
        END-PERFORM
        MOVE WS-ST TO WS-TIME(WS-SJ + 1)
    END-PERFORM

    MOVE WS-FIRST TO OUT-CHK
    COMPUTE OUT-MS ROUNDED = WS-TIME(1)
    COMPUTE WS-MED-IX = WS-REPS / 2
    ADD 1 TO WS-MED-IX
    COMPUTE OUT-MS2 ROUNDED = WS-TIME(WS-MED-IX)
    COMPUTE OUT-MS3 ROUNDED = WS-TIME(WS-REPS)
    DISPLAY "OK " FUNCTION TRIM(WS-BENCH) " "
        FUNCTION TRIM(OUT-CHK) " " FUNCTION TRIM(OUT-MS) " "
        FUNCTION TRIM(OUT-MS2) " " FUNCTION TRIM(OUT-MS3)
    GOBACK.

RUN-ONE.
    EVALUATE WS-BENCH
        WHEN "mandelbrot"  PERFORM BENCH-MANDELBROT
        WHEN "sieve"       PERFORM BENCH-SIEVE
        WHEN "quicksort"   PERFORM BENCH-QUICKSORT
        WHEN "wordcount"   PERFORM BENCH-WORDCOUNT
        WHEN "binarytrees" PERFORM BENCH-BINARYTREES
        WHEN "matmul"      PERFORM BENCH-MATMUL
        WHEN "prng"        PERFORM BENCH-PRNG
    END-EVALUATE.

GET-NOW-MS.
    *> CLOCK_MONOTONIC (1 on Linux) straight from libc; COBOL's own
    *> CURRENT-DATE only resolves hundredths of a second
    CALL "clock_gettime" USING BY VALUE 1 BY REFERENCE CGT-TS END-CALL
    COMPUTE WS-NOW-MS = CGT-SEC * 1000 + CGT-NSEC / 1000000.

RNG-SEED-12345.
    MOVE 0 TO RNG-SH
    MOVE 12345 TO RNG-SL.

RNG-NEXT.
    COMPUTE RNG-T1 = RNG-SL * 1284865837 + 4150755663
    COMPUTE RNG-T2 = RNG-SL * 1481765933
    COMPUTE RNG-T3 = RNG-SH * 1284865837
    COMPUTE RNG-T4 = RNG-T2-LO + RNG-T3-LO + RNG-T1-HI + 335903614
    MOVE RNG-T1-LO TO RNG-SL
    MOVE RNG-T4-LO TO RNG-SH
    COMPUTE RNG-OUT = RNG-SH / 2.

*> ---------- hidden: prng conformance -- not part of the scored suite ----------
*> run.py --selftest uses it to validate the split-halves PRNG above directly;
*> -fno-trunc makes the unsigned 32-bit store wrap, like the other checksums.
BENCH-PRNG.
    PERFORM RNG-SEED-12345
    MOVE 0 TO PR-H
    PERFORM VARYING PR-I FROM 1 BY 1 UNTIL PR-I > WS-SIZE
        PERFORM RNG-NEXT
        COMPUTE PR-H = PR-H * 31 + RNG-OUT
    END-PERFORM
    MOVE PR-H TO WS-RESULT.

*> ---------- 1. mandelbrot: tight floating-point loop, zero allocation ----------
BENCH-MANDELBROT.
    MOVE 0 TO MB-TOTAL
    MOVE WS-SIZE TO MB-FN
    PERFORM VARYING MB-PY FROM 0 BY 1 UNTIL MB-PY >= WS-N32
        MOVE MB-PY TO MB-FP
        COMPUTE MB-CI = 2.0 * MB-FP / MB-FN - 1.0
        PERFORM VARYING MB-PX FROM 0 BY 1 UNTIL MB-PX >= WS-N32
            MOVE MB-PX TO MB-FP
            COMPUTE MB-CR = 2.0 * MB-FP / MB-FN - 1.5
            MOVE 0 TO MB-ZR
            MOVE 0 TO MB-ZI
            MOVE 0 TO MB-ITER
            PERFORM UNTIL MB-ITER >= 255
                COMPUTE MB-ZR2 = MB-ZR * MB-ZR
                COMPUTE MB-ZI2 = MB-ZI * MB-ZI
                IF MB-ZR2 + MB-ZI2 > 4.0
                    EXIT PERFORM
                END-IF
                COMPUTE MB-ZI = 2.0 * MB-ZR * MB-ZI + MB-CI
                COMPUTE MB-ZR = MB-ZR2 - MB-ZI2 + MB-CR
                ADD 1 TO MB-ITER
            END-PERFORM
            ADD MB-ITER TO MB-TOTAL
        END-PERFORM
    END-PERFORM
    MOVE MB-TOTAL TO WS-RESULT.

*> ---------- 2. sieve of eratosthenes: integer math over a large flat array ----------
*> Number v lives at subscript v (byte offset v - 1): computing v + 1 on
*> every access would cost more than the access.
BENCH-SIEVE.
    COMPUTE SV-BYTES = WS-SIZE + 1
    CALL "calloc" USING BY VALUE SV-BYTES BY VALUE 1
        RETURNING SV-PTR END-CALL
    IF SV-PTR = NULL
        PERFORM OOM-ABORT
    END-IF
    SET ADDRESS OF SV-TAB TO SV-PTR
    MOVE 0 TO SV-COUNT
    PERFORM VARYING SV-I FROM 2 BY 1 UNTIL SV-I > WS-SIZE
        IF SV-FLAG(SV-I) = 0
            ADD 1 TO SV-COUNT
            COMPUTE SV-J = SV-I * SV-I
            PERFORM UNTIL SV-J > WS-SIZE
                MOVE SV-ONE TO SV-FLAG(SV-J)
                ADD SV-I TO SV-J
            END-PERFORM
        END-IF
    END-PERFORM
    CALL "free" USING BY VALUE SV-PTR END-CALL
    MOVE SV-COUNT TO WS-RESULT.

*> ---------- 3. quicksort: branches, swaps, explicit stack, random access ----------
BENCH-QUICKSORT.
    COMPUTE QS-BYTES = WS-SIZE * 4
    CALL "malloc" USING BY VALUE QS-BYTES RETURNING QS-PTR END-CALL
    IF QS-PTR = NULL
        PERFORM OOM-ABORT
    END-IF
    SET ADDRESS OF QS-TAB TO QS-PTR
    PERFORM RNG-SEED-12345
    PERFORM VARYING QS-I FROM 1 BY 1 UNTIL QS-I > WS-N32
        PERFORM RNG-NEXT
        MOVE RNG-OUT TO QS-EL(QS-I)
    END-PERFORM

    MOVE 1 TO QS-SP
    MOVE 1 TO QS-SLO(1)
    MOVE WS-N32 TO QS-SHI(1)
    PERFORM UNTIL QS-SP = 0
        MOVE QS-SLO(QS-SP) TO QS-LO
        MOVE QS-SHI(QS-SP) TO QS-HI
        SUBTRACT 1 FROM QS-SP
        PERFORM UNTIL QS-HI - QS-LO <= 16
            COMPUTE QS-MID = QS-LO + (QS-HI - QS-LO) / 2
            *> median-of-three: order el(lo) <= el(mid) <= el(hi)
            IF QS-EL(QS-MID) < QS-EL(QS-LO)
                MOVE QS-EL(QS-MID) TO QS-T
                MOVE QS-EL(QS-LO) TO QS-EL(QS-MID)
                MOVE QS-T TO QS-EL(QS-LO)
            END-IF
            IF QS-EL(QS-HI) < QS-EL(QS-LO)
                MOVE QS-EL(QS-HI) TO QS-T
                MOVE QS-EL(QS-LO) TO QS-EL(QS-HI)
                MOVE QS-T TO QS-EL(QS-LO)
            END-IF
            IF QS-EL(QS-HI) < QS-EL(QS-MID)
                MOVE QS-EL(QS-HI) TO QS-T
                MOVE QS-EL(QS-MID) TO QS-EL(QS-HI)
                MOVE QS-T TO QS-EL(QS-MID)
            END-IF
            MOVE QS-EL(QS-MID) TO QS-PIVOT
            MOVE QS-LO TO QS-I
            MOVE QS-HI TO QS-J
            PERFORM UNTIL QS-I > QS-J
                PERFORM UNTIL QS-EL(QS-I) >= QS-PIVOT
                    ADD 1 TO QS-I
                END-PERFORM
                PERFORM UNTIL QS-EL(QS-J) <= QS-PIVOT
                    SUBTRACT 1 FROM QS-J
                END-PERFORM
                IF QS-I <= QS-J
                    MOVE QS-EL(QS-I) TO QS-T
                    MOVE QS-EL(QS-J) TO QS-EL(QS-I)
                    MOVE QS-T TO QS-EL(QS-J)
                    ADD 1 TO QS-I
                    SUBTRACT 1 FROM QS-J
                END-IF
            END-PERFORM
            *> keep working the smaller half, stack the larger:
            *> the stack stays O(log n) deep
            IF QS-J - QS-LO < QS-HI - QS-I
                ADD 1 TO QS-SP
                MOVE QS-I TO QS-SLO(QS-SP)
                MOVE QS-HI TO QS-SHI(QS-SP)
                MOVE QS-J TO QS-HI
            ELSE
                ADD 1 TO QS-SP
                MOVE QS-LO TO QS-SLO(QS-SP)
                MOVE QS-J TO QS-SHI(QS-SP)
                MOVE QS-I TO QS-LO
            END-IF
        END-PERFORM
        *> insertion sort for the short tail
        COMPUTE QS-I = QS-LO + 1
        PERFORM UNTIL QS-I > QS-HI
            MOVE QS-EL(QS-I) TO QS-V
            COMPUTE QS-J = QS-I - 1
            PERFORM UNTIL QS-J < QS-LO
                IF QS-EL(QS-J) <= QS-V
                    EXIT PERFORM
                END-IF
                COMPUTE QS-MID = QS-J + 1
                MOVE QS-EL(QS-J) TO QS-EL(QS-MID)
                SUBTRACT 1 FROM QS-J
            END-PERFORM
            COMPUTE QS-MID = QS-J + 1
            MOVE QS-V TO QS-EL(QS-MID)
            ADD 1 TO QS-I
        END-PERFORM
    END-PERFORM

    *> order-sensitive checksum; -fno-trunc makes the store wrap at 2^32
    MOVE 0 TO QS-H
    PERFORM VARYING QS-I FROM 1 BY 1 UNTIL QS-I > WS-N32
        COMPUTE QS-H = QS-H * 31 + QS-EL(QS-I)
    END-PERFORM
    CALL "free" USING BY VALUE QS-PTR END-CALL
    MOVE QS-H TO WS-RESULT.

*> ---------- 4. word count: string hashing + hash map ----------
BENCH-WORDCOUNT.
    PERFORM RNG-SEED-12345
    PERFORM VARYING WC-I FROM 1 BY 1 UNTIL WC-I > 5000
        PERFORM RNG-NEXT
        COMPUTE RNG-Q = RNG-OUT / 6
        COMPUTE WC-LEN = RNG-OUT - RNG-Q * 6 + 3
        MOVE SPACES TO WC-WORD-TXT(WC-I)
        PERFORM VARYING WC-C FROM 1 BY 1 UNTIL WC-C > WC-LEN
            PERFORM RNG-NEXT
            COMPUTE RNG-Q = RNG-OUT / 26
            COMPUTE WC-CH = RNG-OUT - RNG-Q * 26 + 1
            MOVE WS-ALPHA(WC-CH:1) TO WC-WORD-TXT(WC-I)(WC-C:1)
            COMPUTE WC-WORD-BYTE(WC-I, WC-C) = WC-CH + 96
        END-PERFORM
        MOVE WC-LEN TO WC-WORD-LEN(WC-I)
    END-PERFORM

    INITIALIZE WC-TBL
    MOVE 0 TO WC-DISTINCT
    MOVE 0 TO WC-MAXC
    PERFORM VARYING WC-K FROM 1 BY 1 UNTIL WC-K > WS-SIZE
        PERFORM RNG-NEXT
        COMPUTE WC-Q = RNG-OUT / 5000
        COMPUTE WC-RA = RNG-OUT - WC-Q * 5000
        PERFORM RNG-NEXT
        COMPUTE WC-Q = RNG-OUT / 5000
        COMPUTE WC-RB = RNG-OUT - WC-Q * 5000
        COMPUTE WC-W = (WC-RA * WC-RB) / 5000 + 1
        *> hash the word's bytes; the exact hash is a private detail
        *> (the checksum only sees the counts). At 8 chars max, h tops
        *> out near 2^53, so it needs no reduction until the very end.
        MOVE WC-WORD-LEN(WC-W) TO WC-LEN
        MOVE 5381 TO WC-H
        PERFORM VARYING WC-C FROM 1 BY 1 UNTIL WC-C > WC-LEN
            COMPUTE WC-H = WC-H * 33 + WC-WORD-BYTE(WC-W, WC-C)
        END-PERFORM
        COMPUTE WC-Q = WC-H / 16384
        COMPUTE WC-IDX = WC-H - WC-Q * 16384 + 1
        PERFORM FOREVER
            IF WC-SLOT-LEN(WC-IDX) = 0
                MOVE WC-WORD-TXT(WC-W) TO WC-SLOT-TXT(WC-IDX)
                MOVE WC-LEN TO WC-SLOT-LEN(WC-IDX)
                MOVE 1 TO WC-SLOT-CNT(WC-IDX)
                ADD 1 TO WC-DISTINCT
                IF WC-MAXC < 1
                    MOVE 1 TO WC-MAXC
                END-IF
                EXIT PERFORM
            END-IF
            IF WC-SLOT-TXT(WC-IDX) = WC-WORD-TXT(WC-W)
                ADD 1 TO WC-SLOT-CNT(WC-IDX)
                IF WC-SLOT-CNT(WC-IDX) > WC-MAXC
                    MOVE WC-SLOT-CNT(WC-IDX) TO WC-MAXC
                END-IF
                EXIT PERFORM
            END-IF
            ADD 1 TO WC-IDX
            IF WC-IDX > 16384
                MOVE 1 TO WC-IDX
            END-IF
        END-PERFORM
    END-PERFORM
    COMPUTE WS-RESULT = WC-DISTINCT * 1000003 + WC-MAXC.

*> ---------- 5. binary trees: allocation, pointer chasing, memory management ----------
*> Nothing here computes; the entire cost is malloc, free and following
*> POINTERs -- COBOL pays retail at every node, exactly like C. (libc
*> malloc via static CALL: GnuCOBOL's own ALLOCATE/FREE keeps a registry
*> that FREE searches linearly, which would time the registry, not the
*> allocator.)
BENCH-BINARYTREES.
    MOVE 0 TO BT-H
    PERFORM VARYING BT-K FROM 0 BY 1 UNTIL BT-K >= WS-SIZE
        *> build a perfect depth-11 tree: 4,095 nodes, thrown away after
        CALL "malloc" USING BY VALUE 16 RETURNING BT-ROOT END-CALL
        IF BT-ROOT = NULL
            PERFORM OOM-ABORT
        END-IF
        MOVE 1 TO BT-SP
        SET BT-SPTR(1) TO BT-ROOT
        MOVE 11 TO BT-SDEP(1)
        PERFORM UNTIL BT-SP = 0
            SET BT-P TO BT-SPTR(BT-SP)
            MOVE BT-SDEP(BT-SP) TO BT-D
            SUBTRACT 1 FROM BT-SP
            IF BT-D > 0
                CALL "malloc" USING BY VALUE 16 RETURNING BT-PL END-CALL
                CALL "malloc" USING BY VALUE 16 RETURNING BT-PR END-CALL
                SET ADDRESS OF BT-NODE TO BT-P
                SET BT-L TO BT-PL
                SET BT-R TO BT-PR
                SUBTRACT 1 FROM BT-D
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-PL
                MOVE BT-D TO BT-SDEP(BT-SP)
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-PR
                MOVE BT-D TO BT-SDEP(BT-SP)
            ELSE
                SET ADDRESS OF BT-NODE TO BT-P
                SET BT-L TO NULL
                SET BT-R TO NULL
            END-IF
        END-PERFORM

        *> walk it, counting nodes
        MOVE 0 TO BT-COUNT
        MOVE 1 TO BT-SP
        SET BT-SPTR(1) TO BT-ROOT
        PERFORM UNTIL BT-SP = 0
            SET BT-P TO BT-SPTR(BT-SP)
            SUBTRACT 1 FROM BT-SP
            ADD 1 TO BT-COUNT
            SET ADDRESS OF BT-NODE TO BT-P
            IF BT-L NOT = NULL
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-L
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-R
            END-IF
        END-PERFORM
        COMPUTE BT-H = BT-H * 31 + BT-COUNT + BT-K

        *> tear it down, node by node
        MOVE 1 TO BT-SP
        SET BT-SPTR(1) TO BT-ROOT
        PERFORM UNTIL BT-SP = 0
            SET BT-P TO BT-SPTR(BT-SP)
            SUBTRACT 1 FROM BT-SP
            SET ADDRESS OF BT-NODE TO BT-P
            IF BT-L NOT = NULL
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-L
                ADD 1 TO BT-SP
                SET BT-SPTR(BT-SP) TO BT-R
            END-IF
            CALL "free" USING BY VALUE BT-P END-CALL
        END-PERFORM
    END-PERFORM
    MOVE BT-H TO WS-RESULT.

*> ---------- 6. matmul: triple-nested loops over flat 2D arrays ----------
BENCH-MATMUL.
    COMPUTE MM-NN = WS-SIZE * WS-SIZE
    COMPUTE MM-BYTES = MM-NN * 4
    CALL "malloc" USING BY VALUE MM-BYTES RETURNING MM-PTR-A END-CALL
    CALL "malloc" USING BY VALUE MM-BYTES RETURNING MM-PTR-B END-CALL
    CALL "malloc" USING BY VALUE MM-BYTES RETURNING MM-PTR-C END-CALL
    IF MM-PTR-A = NULL OR MM-PTR-B = NULL OR MM-PTR-C = NULL
        PERFORM OOM-ABORT
    END-IF
    SET ADDRESS OF MM-TAB-A TO MM-PTR-A
    SET ADDRESS OF MM-TAB-B TO MM-PTR-B
    SET ADDRESS OF MM-TAB-C TO MM-PTR-C
    PERFORM RNG-SEED-12345
    PERFORM VARYING MM-X FROM 1 BY 1 UNTIL MM-X > MM-NN
        PERFORM RNG-NEXT
        COMPUTE RNG-Q = RNG-OUT / 100
        COMPUTE MM-A(MM-X) = RNG-OUT - RNG-Q * 100
    END-PERFORM
    PERFORM VARYING MM-X FROM 1 BY 1 UNTIL MM-X > MM-NN
        PERFORM RNG-NEXT
        COMPUTE RNG-Q = RNG-OUT / 100
        COMPUTE MM-B(MM-X) = RNG-OUT - RNG-Q * 100
    END-PERFORM
    PERFORM VARYING MM-I FROM 0 BY 1 UNTIL MM-I >= WS-N32
        COMPUTE MM-IB = MM-I * WS-N32
        PERFORM VARYING MM-J FROM 1 BY 1 UNTIL MM-J > WS-N32
            MOVE 0 TO MM-S
            COMPUTE MM-IA = MM-IB + 1
            MOVE MM-J TO MM-KB
            PERFORM UNTIL MM-KB > MM-NN
                COMPUTE MM-S = MM-S + MM-A(MM-IA) * MM-B(MM-KB)
                ADD 1 TO MM-IA
                ADD WS-N32 TO MM-KB
            END-PERFORM
            COMPUTE MM-IC = MM-IB + MM-J
            MOVE MM-S TO MM-C(MM-IC)
        END-PERFORM
    END-PERFORM
    MOVE 0 TO MM-H
    PERFORM VARYING MM-X FROM 1 BY 1 UNTIL MM-X > MM-NN
        COMPUTE MM-H = MM-H * 31 + MM-C(MM-X)
    END-PERFORM
    CALL "free" USING BY VALUE MM-PTR-A END-CALL
    CALL "free" USING BY VALUE MM-PTR-B END-CALL
    CALL "free" USING BY VALUE MM-PTR-C END-CALL
    MOVE MM-H TO WS-RESULT.

OOM-ABORT.
    DISPLAY "out of memory" UPON SYSERR
    MOVE 1 TO RETURN-CODE
    GOBACK.
