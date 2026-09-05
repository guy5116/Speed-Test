#!/usr/bin/env python3
"""
run.py -- build, run and compare the benchmarks in all fifteen languages:
x86-64 assembly, C, C++, Rust, Swift, Go, Java, C#, JavaScript, Lua, Perl,
PHP, Python, Ruby and COBOL.

    python3 run.py                # the standard run
    python3 run.py --quick        # ~15s, for a demo
    python3 run.py --heavy        # the "massive task" scale, several minutes
    python3 run.py --scale 20     # 20x the standard workload
    python3 run.py --only mandelbrot,sieve
    python3 run.py --skip python,lua   # when you don't want to wait for them
    python3 run.py --racing       # grand prix mode: racecars, replays, a podium

Every language runs the identical algorithm on the identical input and prints
a checksum. The runner refuses to report a comparison if the checksums differ.

While a language works, the runner draws a progress bar. It cannot ask a child
process how far along it is, so the bar fills against a *predicted* duration
built from results that already exist: the languages that finished earlier in
this run, and earlier runs on this machine cached in build/timings.json. The
timed process is never polled or instrumented, so the bars cost it nothing.
"""
import argparse
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")

# ---------------------------------------------------------------- presentation

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
TRUECOLOR = USE_COLOR and os.environ.get("SPEEDTEST_NO_TRUECOLOR") is None and (
    os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    or "256color" in os.environ.get("TERM", "")
    or "TERM_PROGRAM" in os.environ
    or "WT_SESSION" in os.environ)
# turned off by --plain / --no-animation; the bar thread needs a spare core,
# so on a 1- or 2-core box it must not compete with the timed child
ANIMATE = USE_COLOR and (os.cpu_count() or 1) > 2


def c(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


BOLD = lambda s: c(s, "1")
DIM = lambda s: c(s, "2")
GREEN = lambda s: c(s, "32")
YELLOW = lambda s: c(s, "33")
RED = lambda s: c(s, "31")
CYAN = lambda s: c(s, "36")

# A hue is (r, g, b, fallback). The fallback is the plain ANSI code used on
# terminals that cannot do 24-bit colour, so nothing here is load-bearing.
PINK = (255, 64, 160, "95")
AQUA = (0, 229, 255, "96")
LIME = (60, 255, 140, "92")
GOLD = (255, 205, 40, "93")
CORAL = (255, 80, 90, "91")
VIOLET = (170, 120, 255, "95")
SKY = (90, 170, 255, "94")
STEEL = (108, 120, 142, "2")
TRACK = (48, 54, 68, "2")
SILVER = (198, 204, 214, "37")
BRONZE = (205, 127, 50, "33")


def _lerp(a, b, t):
    return (int(round(a[0] + (b[0] - a[0]) * t)),
            int(round(a[1] + (b[1] - a[1]) * t)),
            int(round(a[2] + (b[2] - a[2]) * t)))


def hue(text, col, bold=False):
    """Paint text in 24-bit colour, degrading to the hue's ANSI fallback."""
    if not USE_COLOR:
        return text
    if not TRUECOLOR:
        return c(text, ("1;" + col[3]) if bold else col[3])
    return "\033[%s38;2;%d;%d;%dm%s\033[0m" % (
        "1;" if bold else "", col[0], col[1], col[2], text)


def gradient(text, a, b, bold=False):
    """Paint text with a colour that slides from a to b, character by character."""
    if not USE_COLOR:
        return text
    if not TRUECOLOR:
        return c(text, ("1;" + a[3]) if bold else a[3])
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        r, g, bl = _lerp(a, b, float(i) / n)
        out.append("\033[%s38;2;%d;%d;%dm%s" % ("1;" if bold else "", r, g, bl, ch))
    return "".join(out) + "\033[0m"


# green -> yellow -> orange -> red, for everything where bigger is worse
_HEAT = [(60, 255, 140, "92"), (190, 255, 60, "92"), (255, 205, 40, "93"),
         (255, 140, 40, "33"), (255, 70, 80, "91")]


def heat(t):
    t = min(max(t, 0.0), 1.0) * (len(_HEAT) - 1)
    i = int(t)
    if i >= len(_HEAT) - 1:
        return _HEAT[-1]
    return _lerp(_HEAT[i], _HEAT[i + 1], t - i) + (_HEAT[i][3],)


def heat_ratio(ratio):
    """1x is green, 100x is deep red, on a log scale so the middle stays useful."""
    return heat(math.log10(max(ratio, 1.0)) / 2.0)


W = 74      # display width
NAMEW = 10  # widest language name ("JavaScript")
BADGES = ((u"\u2460", GOLD), (u"\u2461", SILVER), (u"\u2462", BRONZE))


def rule(char=u"\u2500"):
    return char * W


def neon_rule(char=u"\u2501"):
    return gradient(char * W, PINK, AQUA)


def banner_box(text, a=PINK, b=AQUA, tint=GOLD):
    """A framed title. The frame fades a -> b, the text sits in `tint`."""
    top = u"\u256d" + u"\u2500" * (W - 2) + u"\u256e"
    bot = u"\u2570" + u"\u2500" * (W - 2) + u"\u256f"
    return [gradient(top, a, b),
            hue(u"\u2502", a) + " " + hue(text.ljust(W - 4), tint, bold=True)
            + " " + hue(u"\u2502", b),
            gradient(bot, a, b)]


_FONT = {
    "S": ("###", "#  ", "###", "  #", "###"),
    "P": ("###", "# #", "###", "#  ", "#  "),
    "E": ("###", "#  ", "###", "#  ", "###"),
    "D": ("## ", "# #", "# #", "# #", "## "),
    "T": ("###", " # ", " # ", " # ", " # "),
    "M": ("#   #", "## ##", "# # #", "#   #", "#   #"),
    "A": ("###", "# #", "###", "# #", "# #"),
    "B": ("## ", "# #", "## ", "# #", "## "),
    "Y": ("# #", "# #", " # ", " # ", " # "),
    "G": ("####", "#   ", "# ##", "#  #", "####"),
    "U": ("# #", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", "###"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    " ": ("  ", "  ", "  ", "  ", "  "),
}


def banner(word="SPEED TEST"):
    """Block capitals, drawn with full blocks and lit by a two-axis gradient."""
    rows = []
    for r in range(5):
        line = " ".join(_FONT[ch][r] for ch in word if ch in _FONT)
        rows.append(line.replace("#", u"\u2588"))
    return rows


_SMALLCAPS = {"a": u"\u1d00", "c": u"\u1d04", "e": u"\u1d07", "g": u"\u0262",
              "h": u"\u029c", "i": u"\u026a", "n": u"\u0274", "t": u"\u1d1b",
              "w": u"\u1d21"}


def smallcaps(text):
    return "".join(_SMALLCAPS.get(ch, ch) for ch in text)


def shimmer(text, phase):
    """The credit gradient, phase-shifted so it can ripple across the text."""
    if not TRUECOLOR:
        return hue(text, PINK, bold=True)
    n = max(len(text) - 1, 1)
    out = []
    for i, ch in enumerate(text):
        t = 0.5 + 0.5 * math.sin(2 * math.pi * float(i) / n - phase)
        r, g, b = _lerp(PINK, AQUA, t)
        out.append("\033[1;38;2;%d;%d;%dm%s" % (r, g, b, ch))
    return "".join(out) + "\033[0m"


def human_time(seconds):
    """Turn a duration into something a person can picture."""
    if seconds < 1e-3:
        n = int(round(seconds * 1e6))
        return "%d microsecond%s" % (n, "" if n == 1 else "s")
    if seconds < 1:
        n = int(round(seconds * 1e3))
        return "%d millisecond%s" % (n, "" if n == 1 else "s")
    if seconds < 90:
        return "%.1f seconds" % seconds
    if seconds < 3600:
        m, s = divmod(int(round(seconds)), 60)
        return "%d min %d sec" % (m, s)
    if seconds < 86400:
        h, rem = divmod(int(round(seconds)), 3600)
        m = rem // 60
        return "%d hr %d min" % (h, m)
    d, rem = divmod(int(round(seconds)), 86400)
    h = rem // 3600
    return "%d %s %d hr" % (d, "day" if d == 1 else "days", h)


def gmean(ratios):
    """Geometric mean -- the correct average for ratios (Fleming & Wallace,
    CACM 1986). An arithmetic mean of slowdowns overweights the worst
    benchmark and changes with the choice of baseline; the geometric mean
    does neither."""
    return math.exp(sum(math.log(r) for r in ratios) / len(ratios))


def commas(n):
    return "{:,}".format(int(n))


# ---------------------------------------------------------------- the languages

class Language:
    def __init__(self, key, name, color, tint=None):
        self.key = key
        self.name = name
        self.color = color          # plain ANSI, for 8-colour terminals
        self.tint = (tint + (color,)) if tint else (200, 200, 210, color)
        self.available = False
        self.version = ""
        self.note = ""
        self.cmd = None
        self.reason = ""
        self.sits_out = {}      # benchmark key -> why this language skips it
        self.warmup = False     # honours the optional warm-up argument (JITs)


def detect_version(argv, transform=None):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        text = (out.stdout or out.stderr).strip().splitlines()
        text = text[0] if text else ""
        return transform(text) if transform else text
    except Exception:
        return ""


BUILD_NOTES = os.path.join(BUILD, "buildinfo.json")


def fresh(exe, *sources):
    """True when `exe` exists and is newer than every source that could change
    it -- including this runner, whose compile flags live in this file. Lets a
    repeat run skip recompiling what has not changed; the benchmark itself is
    identical either way."""
    try:
        t = os.path.getmtime(exe)
    except OSError:
        return False
    for src in sources + (os.path.abspath(__file__),):
        try:
            if os.path.getmtime(src) >= t:
                return False
        except OSError:
            return False
    return True


def _load_notes():
    """The note ('compiled -O2 -march=native') records which flags a build
    actually got, fallbacks included, so a cached binary can still report it."""
    try:
        with open(BUILD_NOTES) as fh:
            return dict(json.load(fh))
    except Exception:
        return {}


def build_all(skip):
    """Compile what can be compiled; report what can't. Every language builds
    on its own thread: the compiles are independent processes writing disjoint
    outputs, and none of them is being timed, so parallelism here is free."""
    os.makedirs(BUILD, exist_ok=True)
    saved_notes = _load_notes()

    def build_asm():
        lang = Language("asm", "Assembly", "37", (198, 204, 214))
        nasm = shutil.which("nasm")
        ld_cc = shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
            lang.reason = "bench.asm is x86-64 Linux only (System V ABI, ELF)"
            return lang
        if not nasm:
            lang.reason = "nasm not found"
            return lang
        if not ld_cc:
            lang.reason = "no C compiler found to link against libc"
            return lang
        obj = os.path.join(BUILD, "bench_asm.o")
        exe = os.path.join(BUILD, "bench_asm")
        src = os.path.join(HERE, "bench.asm")
        if not fresh(exe, src):
            proc = subprocess.run([nasm, "-felf64", "-o", obj, src],
                                  capture_output=True, text=True)
            if proc.returncode == 0:
                # -no-pie: the source uses direct references to libc data (stderr),
                # which need the classic copy-relocation link model
                proc = subprocess.run([ld_cc, "-no-pie", "-o", exe, obj],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "assemble/link failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([nasm, "--version"],
                                      lambda t: " ".join(t.split()[:3]).lower())
        lang.note = "hand-written, no compiler at all"
        return lang

    def build_c():
        lang = Language("c", "C", "36", (130, 205, 255))
        cc = shutil.which("gcc") or shutil.which("clang") or shutil.which("cc")
        if not cc:
            lang.reason = "no C compiler found (install gcc or clang)"
            return lang
        exe = os.path.join(BUILD, "bench_c")
        src = os.path.join(HERE, "bench.c")
        if fresh(exe, src):
            lang.note = saved_notes.get("c", "compiled -O3")
        else:
            # -ffp-contract=off: GCC contracts a*b+c into fused multiply-adds
            # by default, which changes float rounding -- at --heavy scale
            # that flips mandelbrot pixels and fails the cross-language
            # checksum. Everyone must compute the same doubles.
            flags = ["-O3", "-march=native", "-ffp-contract=off"]
            proc = subprocess.run([cc] + flags + ["-o", exe, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                # -march=native isn't universal (Apple silicon clang, some cross setups)
                flags = ["-O3", "-ffp-contract=off"]
                proc = subprocess.run([cc] + flags + ["-o", exe, src],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "compile failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
            lang.note = "compiled " + " ".join(flags)
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([cc, "--version"])
        return lang

    def build_cpp():
        lang = Language("cpp", "C++", "94", (0, 140, 235))
        cxx = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")
        if not cxx:
            lang.reason = "no C++ compiler found (install g++ or clang++)"
            return lang
        exe = os.path.join(BUILD, "bench_cpp")
        src = os.path.join(HERE, "bench.cpp")
        if fresh(exe, src):
            lang.note = saved_notes.get("cpp", "compiled -O3 -std=c++17")
        else:
            # -ffp-contract=off for the same reason as the C build: FMA
            # contraction changes mandelbrot's rounding at large sizes
            flags = ["-O3", "-march=native", "-ffp-contract=off", "-std=c++17"]
            proc = subprocess.run([cxx] + flags + ["-o", exe, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                flags = ["-O3", "-ffp-contract=off", "-std=c++17"]
                proc = subprocess.run([cxx] + flags + ["-o", exe, src],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "compile failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
            lang.note = "compiled " + " ".join(flags)
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([cxx, "--version"])
        return lang

    def build_rust():
        lang = Language("rust", "Rust", "91", (255, 125, 60))
        rustc = shutil.which("rustc")
        if not rustc:
            lang.reason = "rustc not found"
            return lang
        exe = os.path.join(BUILD, "bench_rust")
        src = os.path.join(HERE, "bench.rs")
        if not fresh(exe, src):
            # A bare rustc, no Cargo: bench.rs deliberately depends on nothing.
            # opt-level=3 + target-cpu=native + one codegen unit is what a
            # tuned cargo release profile would hand it.
            best = ["-C", "opt-level=3", "-C", "target-cpu=native",
                    "-C", "codegen-units=1"]
            proc = subprocess.run([rustc] + best + ["--edition", "2021", "-o", exe, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                # target-cpu=native can fail on odd cross setups; -O still works,
                # and --edition predates rustc 1.27 (the source is valid in 2015 too)
                proc = subprocess.run([rustc, "-O", "-o", exe, src],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "compile failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([rustc, "--version"],
                                      lambda t: " ".join(t.split()[:2]))
        lang.note = "compiled -Copt-level=3 -Ctarget-cpu=native, no crates, no unsafe"
        return lang

    def build_swift():
        lang = Language("swift", "Swift", "91", (240, 105, 60))
        swiftc = shutil.which("swiftc")
        if not swiftc:
            lang.reason = "swiftc not found"
            return lang
        exe = os.path.join(BUILD, "bench_swift")
        src = os.path.join(HERE, "bench.swift")
        note = "compiled -Ounchecked, no Foundation"
        if fresh(exe, src):
            note = saved_notes.get("swift", note)
        else:
            # -Ounchecked drops the bounds/overflow preconditions -O keeps;
            # the source already asks for wrapping arithmetic (&*, &+), so
            # the semantics it removes are ones this program never trips.
            proc = subprocess.run([swiftc, "-Ounchecked", "-o", exe, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                note = "compiled -O, no Foundation"
                proc = subprocess.run([swiftc, "-O", "-o", exe, src],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "compile failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([swiftc, "--version"],
                                      lambda t: " ".join(t.split()[:3]))
        lang.note = note
        return lang

    def build_go():
        lang = Language("go", "Go", "96", (0, 220, 220))
        go = shutil.which("go")
        if not go:
            lang.reason = "go not found"
            return lang
        exe = os.path.join(BUILD, "bench_go")
        src = os.path.join(HERE, "bench.go")
        note = "compiled, GC runtime"
        if fresh(exe, src):
            note = saved_notes.get("go", note)
        else:
            env = dict(os.environ)
            env.setdefault("GOCACHE", os.path.join(BUILD, ".gocache"))
            env.setdefault("GOFLAGS", "-mod=mod")
            attempts = [(dict(env), note)]
            if platform.machine() in ("x86_64", "amd64"):
                # GOAMD64=v3 lets the compiler assume AVX2-era hardware, the
                # closest Go gets to -march=native; fall back if this CPU isn't
                v3 = dict(env)
                v3["GOAMD64"] = "v3"
                attempts.insert(0, (v3, "compiled GOAMD64=v3, GC runtime"))
            for attempt_env, attempt_note in attempts:
                proc = subprocess.run([go, "build", "-o", exe, src],
                                      capture_output=True, text=True, cwd=HERE,
                                      env=attempt_env)
                if proc.returncode == 0:
                    note = attempt_note
                    break
            if proc.returncode != 0:
                lang.reason = "build failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version([go, "version"])
        lang.note = note
        return lang

    def build_java():
        lang = Language("java", "Java", "33", (235, 75, 75))
        javac, java = shutil.which("javac"), shutil.which("java")
        if not (javac and java):
            lang.reason = "javac/java not found (need a JDK, not just a JRE)"
            return lang
        src = os.path.join(HERE, "Bench.java")
        if not fresh(os.path.join(BUILD, "Bench.class"), src):
            proc = subprocess.run([javac, "-d", BUILD, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                lang.reason = "compile failed: " + proc.stderr.strip().splitlines()[-1][:60]
                return lang
        lang.available = True
        lang.cmd = [java, "-cp", BUILD, "Bench"]
        lang.version = detect_version([java, "-version"])
        lang.note = "bytecode + JIT"
        lang.warmup = True
        return lang

    def build_csharp():
        lang = Language("csharp", "C#", "95", (163, 100, 216))
        dotnet = shutil.which("dotnet")
        if not dotnet:
            lang.reason = "dotnet not found (need the .NET SDK, not just the runtime)"
            return lang
        ver = detect_version([dotnet, "--version"])
        m = re.match(r"(\d+)\.", ver or "")
        proj = os.path.join(BUILD, "csharp")
        out = os.path.join(proj, "out")
        os.makedirs(proj, exist_ok=True)
        # A csproj is the only way the SDK will compile anything, so generate a
        # minimal one pointing back at bench.cs. The TargetFramework has to
        # match whatever SDK is installed, hence the version sniff above.
        csproj = os.path.join(proj, "bench_cs.csproj")
        content = (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <PropertyGroup>\n'
            '    <OutputType>Exe</OutputType>\n'
            '    <TargetFramework>net%s.0</TargetFramework>\n'
            '    <AssemblyName>bench_cs</AssemblyName>\n'
            '    <Nullable>disable</Nullable>\n'
            '    <ImplicitUsings>disable</ImplicitUsings>\n'
            '    <InvariantGlobalization>true</InvariantGlobalization>\n'
            '    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>\n'
            '    <ServerGarbageCollection>true</ServerGarbageCollection>\n'
            '    <ConcurrentGarbageCollection>false</ConcurrentGarbageCollection>\n'
            '  </PropertyGroup>\n'
            '  <ItemGroup>\n'
            '    <Compile Include="%s" />\n'
            '  </ItemGroup>\n'
            '</Project>\n' % (m.group(1) if m else "8",
                              os.path.join(HERE, "bench.cs")))
        try:
            with open(csproj) as fh:
                unchanged = fh.read() == content
        except OSError:
            unchanged = False
        if not unchanged:
            # only touch it when it really changed, or its mtime would force a
            # rebuild every run
            with open(csproj, "w") as fh:
                fh.write(content)
        dll = os.path.join(out, "bench_cs.dll")
        if not fresh(dll, os.path.join(HERE, "bench.cs"), csproj):
            env = dict(os.environ)
            env.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
            env.setdefault("DOTNET_NOLOGO", "1")
            env.setdefault("DOTNET_SKIP_FIRST_TIME_EXPERIENCE", "1")
            proc = subprocess.run([dotnet, "build", "bench_cs.csproj", "-c", "Release",
                                   "-o", out, "--nologo", "-v", "quiet",
                                   "-nodeReuse:false"],
                                  capture_output=True, text=True, cwd=proj, env=env)
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "").strip().splitlines()
                lang.reason = "build failed: " + (msg[-1][:60] if msg else "no output")
                return lang
        lang.available = True
        lang.cmd = [dotnet, dll]
        lang.version = "dotnet " + (ver or "?")
        lang.note = "bytecode + JIT (.NET, server GC)"
        lang.warmup = True
        return lang

    def build_js():
        lang = Language("js", "JavaScript", "93", (255, 220, 50))
        node = shutil.which("node") or shutil.which("nodejs")
        if not node:
            lang.reason = "node not found"
            return lang
        lang.available = True
        lang.cmd = [node, os.path.join(HERE, "bench.js")]
        lang.version = detect_version([node, "--version"], lambda t: "node " + t)
        lang.note = "JIT (V8), no flags"
        lang.warmup = True
        return lang

    def build_lua():
        lang = Language("lua", "Lua", "34", (120, 140, 255))
        lua = (shutil.which("lua5.4") or shutil.which("lua5.3")
               or shutil.which("lua") or shutil.which("lua54") or shutil.which("lua53"))
        if not lua:
            lang.reason = "lua not found (need 5.3+ for 64-bit integers)"
            return lang
        banner = detect_version([lua, "-v"])
        m = re.match(r"Lua (\d+)\.(\d+)", banner)
        if "LuaJIT" in banner:
            # LuaJIT is a Lua 5.1 dialect: every number is a double, so the
            # shared 64-bit PRNG cannot be reproduced without the FFI.
            lang.reason = "LuaJIT has no 64-bit integers; needs Lua 5.3+ (see README)"
        elif not m or (int(m.group(1)), int(m.group(2))) < (5, 3):
            lang.reason = "%s is too old; the shared PRNG needs Lua 5.3+" % (banner or lua)
        else:
            lang.available = True
            lang.cmd = [lua, os.path.join(HERE, "bench.lua")]
            lang.version = banner.split("  Copyright")[0]
            lang.note = "interpreted, reference Lua"
        return lang

    def build_perl():
        lang = Language("perl", "Perl", "94", (170, 140, 235))
        perl = shutil.which("perl")
        if not perl:
            lang.reason = "perl not found"
            return lang
        lang.available = True
        lang.cmd = [perl, os.path.join(HERE, "bench.pl")]
        lang.version = detect_version(
            [perl, "-e", "print 'perl ', $^V"])
        lang.note = "interpreted, core modules only"
        return lang

    def build_php():
        lang = Language("php", "PHP", "35", (119, 123, 180))
        php = shutil.which("php")
        if not php:
            lang.reason = "php not found"
            return lang
        # PHP 8 ships a JIT inside opcache, off by default on the CLI. Probe
        # that it actually engages before claiming it; some builds lack the
        # extension entirely.
        jit = ["-d", "opcache.enable_cli=1", "-d", "opcache.jit=tracing",
               "-d", "opcache.jit_buffer_size=64M"]
        probe = subprocess.run(
            [php] + jit + ["-r",
             'exit((int)!(function_exists("opcache_get_status")'
             ' && (opcache_get_status()["jit"]["enabled"] ?? false)));'],
            capture_output=True, text=True)
        lang.available = True
        lang.warmup = True
        if probe.returncode == 0:
            lang.cmd = [php] + jit + [os.path.join(HERE, "bench.php")]
            lang.note = "opcache JIT (tracing)"
        else:
            lang.cmd = [php, os.path.join(HERE, "bench.php")]
            lang.note = "interpreted, no opcache JIT available"
        lang.version = detect_version([php, "--version"],
                                      lambda t: " ".join(t.split()[:2]))
        return lang

    def build_python():
        lang = Language("python", "Python", "35", (70, 235, 160))
        py = shutil.which("python3") or shutil.which("python") or sys.executable
        if not py:
            lang.reason = "python3 not found"
            return lang
        lang.available = True
        lang.cmd = [py, os.path.join(HERE, "bench.py")]
        lang.version = detect_version([py, "--version"])
        lang.note = "interpreted, no numpy"
        return lang

    def build_numpy():
        lang = Language("numpy", "NumPy", "36", (77, 171, 207))
        candidates = []
        for name in ("python3", "python", "python3.14", "python3.13", "python3.12"):
            p = shutil.which(name)
            if p and p not in candidates:
                candidates.append(p)
        py = None
        for cand in candidates:
            if subprocess.run([cand, "-c", "import numpy"],
                              capture_output=True).returncode == 0:
                py = cand
                break
        if not py:
            lang.reason = "no python with numpy found (pip install numpy)"
            return lang
        lang.available = True
        lang.cmd = [py, os.path.join(HERE, "bench_numpy.py")]
        lang.version = detect_version(
            [py, "-c", "import numpy, platform; "
             "print('python %s + numpy %s' % (platform.python_version(), numpy.__version__))"])
        lang.note = "same interpreter, loops pushed into C/BLAS"
        lang.sits_out["quicksort"] = (
            "sits this one out: the rule is the same hand-written sort, and "
            "np.sort is a different program (C introsort)")
        lang.sits_out["wordcount"] = (
            "sits this one out: no hash maps in NumPy; the vectorised form "
            "would skip the strings entirely")
        lang.sits_out["binarytrees"] = (
            "sits this one out: the workload is allocating little heap "
            "objects, the exact thing NumPy exists to avoid")
        return lang

    def build_ruby():
        lang = Language("ruby", "Ruby", "31", (225, 50, 90))
        ruby = shutil.which("ruby")
        if not ruby:
            lang.reason = "ruby not found"
            return lang
        # CRuby 3.2+ ships YJIT but leaves it off; enable it when this build
        # has it compiled in (not all distro rubies do). The call threshold
        # matters: YJIT compiles at method entry after N calls and has no
        # on-stack replacement, so the default of 30 would leave a top-level
        # bench_* body -- called `reps` times -- interpreted forever.
        yjit = ["--yjit", "--yjit-call-threshold=1"]
        probe = subprocess.run(
            [ruby] + yjit + ["-e",
             "exit(defined?(RubyVM::YJIT) && RubyVM::YJIT.enabled? ? 0 : 1)"],
            capture_output=True, text=True)
        lang.available = True
        lang.warmup = True
        if probe.returncode == 0:
            lang.cmd = [ruby] + yjit + [os.path.join(HERE, "bench.rb")]
            lang.note = "CRuby + YJIT"
        else:
            lang.cmd = [ruby, os.path.join(HERE, "bench.rb")]
            lang.note = "interpreted, no YJIT in this build"
        lang.version = detect_version([ruby, "--version"],
                                      lambda t: " ".join(t.split()[:2]))
        return lang

    def build_cobol():
        lang = Language("cobol", "COBOL", "33", (222, 203, 160))
        cobc = shutil.which("cobc")
        if not cobc:
            lang.reason = "cobc not found (install GnuCOBOL 3+)"
            return lang
        exe = os.path.join(BUILD, "bench_cobol")
        src = os.path.join(HERE, "bench.cob")
        if not fresh(exe, src):
            # -fno-trunc: C-style binary wraparound instead of decimal truncation
            # -fstatic-call: CALL "malloc"/"free"/"clock_gettime" link straight to libc
            proc = subprocess.run([cobc, "-x", "-O3", "-fno-trunc", "-fstatic-call",
                                   "-o", exe, src],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                # -O3 arrived with GnuCOBOL 3; older cobc still knows -O2
                proc = subprocess.run([cobc, "-x", "-O2", "-fno-trunc", "-fstatic-call",
                                       "-o", exe, src],
                                      capture_output=True, text=True)
            if proc.returncode != 0:
                msg = (proc.stderr or "").strip().splitlines()
                lang.reason = "compile failed: " + (msg[-1][:60] if msg else "no output")
                return lang
        lang.available = True
        lang.cmd = [exe]
        lang.version = detect_version(
            [cobc, "--version"],
            lambda t: t.replace("cobc (", "").replace(")", ""))
        lang.note = "compiled via C, decimal arithmetic"
        lang.sits_out["mandelbrot"] = (
            "sits this one out: GnuCOBOL floats run through its decimal "
            "engine, ~2000x (see README)")
        return lang

    builders = [build_asm, build_c, build_cpp, build_rust, build_swift,
                build_go, build_java, build_csharp, build_js, build_lua,
                build_perl, build_php, build_python, build_numpy, build_ruby,
                build_cobol]
    with ThreadPoolExecutor(max_workers=len(builders)) as pool:
        langs = list(pool.map(lambda fn: fn(), builders))

    try:
        with open(BUILD_NOTES, "w") as fh:
            json.dump(dict((l.key, l.note) for l in langs if l.available),
                      fh, indent=1, sort_keys=True)
    except Exception:
        pass

    for lang in langs:
        if lang.key in skip or lang.name.lower() in skip:
            lang.available = False
            lang.reason = "skipped by --skip"
    return langs


# ---------------------------------------------------------------- the workloads

BENCHMARKS = [
    {
        "key": "mandelbrot",
        "title": "MANDELBROT",
        "base": 800,
        "growth": "quadratic",   # work scales with N^2
        "what": "raw floating-point arithmetic in a tight loop",
        "why": ("No I/O, no allocation, no library calls. Just multiply-add,\n"
                "  hundreds of millions of times. Nowhere for a language to hide."),
        "describe": lambda n: "%s x %s grid, up to 255 iterations per pixel" % (commas(n), commas(n)),
    },
    {
        "key": "sieve",
        "title": "SIEVE OF ERATOSTHENES",
        "base": 50_000_000,
        "growth": "linear",
        "what": "integer math walking a huge flat array",
        "why": ("Memory-bound rather than compute-bound: the CPU spends its time\n"
                "  waiting on cache lines. Note how the gap narrows here."),
        "describe": lambda n: "find every prime below %s" % commas(n),
    },
    {
        "key": "quicksort",
        "title": "QUICKSORT",
        "base": 4_000_000,
        "growth": "linear",
        "what": "branches, swaps, recursion and unpredictable memory access",
        "why": ("A hand-written quicksort, identical in all fifteen languages, so\n"
                "  this measures the language and not the quality of its sort library."),
        "describe": lambda n: "sort %s 32-bit integers in place" % commas(n),
    },
    {
        "key": "wordcount",
        "title": "WORD FREQUENCY COUNT",
        "base": 10_000_000,
        "growth": "linear",
        "what": "string hashing into a hash map",
        "why": ("The revenge of the scripting languages. Python's dict, Perl's\n"
                "  hash, PHP's array, Java's HashMap, JS's Map and Lua's table are\n"
                "  written in C or C++ -- most of the real work here happens *below*\n"
                "  the language. C, the assembly and COBOL pay by hand-rolling a\n"
                "  hash table: ~40 lines versus one line."),
        "describe": lambda n: "count %s words against a 5,000-word vocabulary" % commas(n),
    },
    {
        "key": "binarytrees",
        "title": "BINARY TREES",
        "base": 4000,
        "growth": "linear",
        "what": "allocation, pointer chasing and garbage collection",
        "why": ("Nothing here computes anything: the whole benchmark is building\n"
                "  trees and throwing them away. C pays malloc/free retail at every\n"
                "  node; the GC languages pay wholesale, in bursts -- and the JVM's\n"
                "  allocator tends to win this one outright, which surprises people."),
        "describe": lambda n: "build and tear down %s trees of 4,095 nodes each" % commas(n),
    },
    {
        "key": "matmul",
        "title": "MATRIX MULTIPLY",
        "base": 500,
        "growth": "cubic",     # work scales with N^3
        "what": "triple-nested loops over flat 2D arrays",
        "why": ("The classic numeric kernel: n^3 multiply-adds, each one paying\n"
                "  2D index arithmetic. The inner loop walks one matrix down a\n"
                "  column, so the caches suffer -- in every language equally."),
        "describe": lambda n: "multiply two %s x %s integer matrices" % (commas(n), commas(n)),
    },
]


def sized(bench, scale):
    if bench["growth"] == "quadratic":
        n = int(bench["base"] * math.sqrt(scale))
    elif bench["growth"] == "cubic":
        n = int(bench["base"] * scale ** (1.0 / 3.0))
    else:
        n = int(bench["base"] * scale)
    return max(n, 8)


# Several entries parse the size into a 32-bit int (C's atoi, Java/C#/Rust);
# past INT32_MAX they overflow or throw, they don't slow down.
INT32_MAX = 2147483647


def int32_cap(bench):
    """Largest --scale whose size still fits a 32-bit int for this benchmark."""
    r = INT32_MAX / float(bench["base"])
    return r * r if bench["growth"] == "quadratic" else r ** 3 if bench["growth"] == "cubic" else r


# ------------------------------------------------- how long is this going to take

TIMING_CACHE = os.path.join(BUILD, "timings.json")

# Seconds per repetition at the standard size, for the fastest language, on the
# machine that produced the README numbers.
REF_SECONDS = {"mandelbrot": 0.133, "sieve": 0.12, "quicksort": 0.25,
               "wordcount": 0.28, "binarytrees": 0.1, "matmul": 0.07}

# The README's summary table: how much slower than the fastest language each
# language is, per benchmark. Deliberately rough -- it only has to get the first
# bar of a fresh run roughly right, and it is corrected the moment anything real
# finishes.
REF_RATIO = {
    "asm":    {"mandelbrot": 1.1,  "sieve": 1.0,  "quicksort": 1.1,  "wordcount": 1.2,
               "binarytrees": 1.5,  "matmul": 1.0},
    "c":      {"mandelbrot": 1.0,  "sieve": 1.0,  "quicksort": 1.1,  "wordcount": 1.0,
               "binarytrees": 1.5,  "matmul": 1.0},
    "cpp":    {"mandelbrot": 1.0,  "sieve": 1.0,  "quicksort": 1.1,  "wordcount": 1.9,
               "binarytrees": 1.5,  "matmul": 1.0},
    "rust":   {"mandelbrot": 1.0,  "sieve": 1.1,  "quicksort": 1.0,  "wordcount": 1.2,
               "binarytrees": 1.5,  "matmul": 1.0},
    "swift":  {"mandelbrot": 1.0,  "sieve": 1.0,  "quicksort": 1.1,  "wordcount": 2.2,
               "binarytrees": 4.0,  "matmul": 1.1},
    "go":     {"mandelbrot": 1.2,  "sieve": 1.0,  "quicksort": 1.0,  "wordcount": 1.1,
               "binarytrees": 3.0,  "matmul": 1.2},
    "java":   {"mandelbrot": 1.2,  "sieve": 1.0,  "quicksort": 1.2,  "wordcount": 1.2,
               "binarytrees": 1.0,  "matmul": 1.2},
    "csharp": {"mandelbrot": 1.2,  "sieve": 1.0,  "quicksort": 1.2,  "wordcount": 1.8,
               "binarytrees": 1.2,  "matmul": 1.3},
    "js":     {"mandelbrot": 1.1,  "sieve": 1.2,  "quicksort": 1.7,  "wordcount": 3.5,
               "binarytrees": 1.2,  "matmul": 2.8},
    "lua":    {"mandelbrot": 10.2, "sieve": 7.4,  "quicksort": 6.2,  "wordcount": 4.3,
               "binarytrees": 23.0, "matmul": 31.0},
    "perl":   {"mandelbrot": 57.0, "sieve": 34.0, "quicksort": 44.0, "wordcount": 19.0,
               "binarytrees": 38.0, "matmul": 110.0},
    "php":    {"mandelbrot": 9.0,  "sieve": 6.0,  "quicksort": 8.0,  "wordcount": 8.0,
               "binarytrees": 8.0,  "matmul": 20.0},
    "python": {"mandelbrot": 50.5, "sieve": 16.5, "quicksort": 29.3, "wordcount": 28.1,
               "binarytrees": 19.0, "matmul": 125.0},
    "numpy":  {"mandelbrot": 4.0,  "sieve": 1.5,  "matmul": 0.7},
    "ruby":   {"mandelbrot": 16.0, "sieve": 7.0,  "quicksort": 20.0, "wordcount": 25.0,
               "binarytrees": 9.0,  "matmul": 55.0},
    "cobol":  {"mandelbrot": 4000.0, "sieve": 35.0, "quicksort": 30.0, "wordcount": 150.0,
               "binarytrees": 2.0,  "matmul": 230.0},
}

REF_STARTUP = {"asm": 0.0006, "c": 0.0007, "cpp": 0.001, "rust": 0.0008, "swift": 0.002,
               "go": 0.001, "java": 0.064, "csharp": 0.08, "js": 0.023, "lua": 0.001,
               "perl": 0.006, "php": 0.025, "python": 0.013, "numpy": 0.11,
               "ruby": 0.06, "cobol": 0.003}
REF_BUILD = 12.0


def _host():
    """Cached timings only mean anything on the machine that produced them."""
    u = platform.uname()
    return "%s|%s|%s cores" % (u.system, u.machine, os.cpu_count())


class Estimator:
    """Predicts how long each run will take, so the progress bars have something
    to fill against.

    Everything it knows comes from measurements that have already happened: the
    languages that finished earlier in this run, and earlier runs cached in
    build/timings.json. It never asks the running process anything, so watching
    the bar costs the benchmark nothing. A first run on a new machine starts from
    the constants above -- usually right to within a factor of two, which is
    plenty for a bar, and it is close to exact by the second benchmark."""

    def __init__(self):
        self.rates = {}       # "lang|bench" -> {workload octave: seconds per rep}
        self.startup = {}     # lang key -> seconds
        self.build = REF_BUILD
        self.machine = 1.0    # how much slower this machine is than the reference
        self.bias = 1.0       # this run's guesses versus what actually happened;
                              # deliberately not cached, it is about today's
                              # conditions and about what we had to borrow
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self):
        try:
            with open(TIMING_CACHE) as fh:
                data = json.load(fh)
        except Exception:
            return
        if data.get("host") != _host():
            return            # someone else's numbers: the priors are the better guess
        self.rates = dict((k, dict(v)) for k, v in (data.get("rates") or {}).items()
                          if isinstance(v, dict))
        self.startup = dict(data.get("startup") or {})
        self.build = data.get("build") or REF_BUILD
        self.machine = data.get("machine") or 1.0

    def save(self):
        try:
            os.makedirs(BUILD, exist_ok=True)
            with open(TIMING_CACHE, "w") as fh:
                json.dump({"host": _host(), "machine": self.machine,
                           "build": self.build, "startup": self.startup,
                           "rates": self.rates}, fh, indent=1, sort_keys=True)
        except Exception:
            pass              # a progress bar is never worth failing a run over

    # -- the model --------------------------------------------------------
    @staticmethod
    def units(bench, size):
        """Work relative to the standard size, so a rate learned at one --scale
        still means something at another."""
        r = float(size) / bench["base"]
        if bench["growth"] == "quadratic":
            return r * r
        if bench["growth"] == "cubic":
            return r * r * r
        return r

    @staticmethod
    def bucket(units):
        """Rates are cached per octave of workload, not globally. A sieve of 50
        million is not simply eight times a sieve of 6 million -- it falls out of
        cache -- so a rate learned under --quick must not be trusted verbatim for
        a full run. Same scale as last time means an exact estimate; a new scale
        borrows the nearest octave and corrects itself after one language."""
        return str(int(round(math.log(max(units, 1e-9), 2))))

    def _rate(self, lang_key, bench_key, units):
        """Returns (seconds per rep, was it measured at this exact workload?)."""
        known = self.rates.get(lang_key + "|" + bench_key) or {}
        want = self.bucket(units)
        if want in known:
            return known[want], True
        if known:
            return known[min(known, key=lambda b: abs(int(b) - int(want)))], False
        return None, False

    def _prior(self, lang_key, bench_key):
        return (REF_SECONDS.get(bench_key, 0.25)
                * REF_RATIO.get(lang_key, {}).get(bench_key, 3.0)
                * self.machine)

    def startup_of(self, lang_key):
        if lang_key in self.startup:
            return self.startup[lang_key]
        return REF_STARTUP.get(lang_key, 0.01) * self.machine

    def estimate(self, lang_key, bench, size, reps):
        units = self.units(bench, size)
        rate, exact = self._rate(lang_key, bench["key"], units)
        if rate is None:
            rate = self._prior(lang_key, bench["key"])
        guess = self.startup_of(lang_key) + rate * units * reps
        # A rate measured at this exact workload is taken at face value. A rate
        # borrowed from another workload -- or from the constants above -- gets
        # corrected by however wrong this run's guesses have been so far.
        return guess if exact else guess * self.bias

    def total(self, langs, benches, scale, reps):
        return sum(self.estimate(l.key, b, sized(b, scale), reps)
                   for b in benches for l in langs
                   if b["key"] not in l.sits_out)

    # -- learning ---------------------------------------------------------
    def record(self, lang_key, bench, size, reps, wall):
        """Fold a finished run back in, for the rest of this run and the next one."""
        u = self.units(bench, size)
        if not wall or u <= 0 or reps <= 0:
            return
        predicted = self.estimate(lang_key, bench, size, reps)
        # Never let a bad startup guess eat the whole measurement: at --quick
        # scale a JVM launch can be most of the wall clock, and a rate of ~0
        # would poison the cache and hand the next run an instant bar.
        rate = max(wall - self.startup_of(lang_key), wall * 0.02) / (u * reps)
        self.rates.setdefault(lang_key + "|" + bench["key"], {})[self.bucket(u)] = rate
        reference = self._prior(lang_key, bench["key"]) / max(self.machine, 1e-9)
        if reference > 0:
            # This machine ran that `rate / reference` times slower than the
            # reference one did; carry that over to the languages we have not run
            # yet. Blended gently, so one outlier cannot drag every estimate with it.
            self.machine = math.exp(0.75 * math.log(max(self.machine, 1e-6))
                                    + 0.25 * math.log(max(rate / reference, 1e-6)))
        if wall > 0.05 and predicted > 0.05:
            # Runs too short to mean anything are left out: at those durations the
            # error is process launch and scheduler noise, not a bad prediction.
            self.bias = min(max(math.exp(0.6 * math.log(self.bias)
                                         + 0.4 * math.log(wall / predicted)), 0.1), 10.0)

    def record_startup(self, lang_key, secs):
        if secs and secs > 0:
            self.startup[lang_key] = secs

    def record_build(self, secs):
        if secs and secs > 0:
            self.build = secs


# ---------------------------------------------------------------- running

# Set in main() when --pin is given: a taskset prefix that nails every
# benchmark process to one core, the way the Benchmarks Game runs do. One
# core means no cross-CPU migrations mid-run and steadier cache behaviour.
PIN = []


def launch(argv, timeout=600.0):
    """Run one child process to completion. Returns (returncode, stdout,
    stderr, wall_seconds, peak_rss_mb, error); error is set when the child
    could not be launched or blew the deadline. The child is reaped with
    os.wait4 so its rusage comes back with it -- that is where the peak-RSS
    figure comes from -- and the deadline keeps one hung toolchain from
    hanging the whole run."""
    # Temp files, not pipes: only stdout is guaranteed to be one line. A child
    # that writes more than the pipe buffer (~64 KB) to stderr -- PHP
    # deprecation spam, a JVM hs_err dump -- would fill a pipe and block
    # forever, and the runner would report a timeout instead of the real error.
    # A file has no such limit, so the wait loop needs no reader thread.
    outf = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    errf = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    t0 = time.perf_counter()
    try:
        proc = subprocess.Popen(argv, stdout=outf, stderr=errf)
    except Exception as e:
        outf.close()
        errf.close()
        return None, None, None, None, None, "failed to launch: %s" % e

    rss_mb = None
    if hasattr(os, "wait4"):
        # Poll with WNOHANG rather than block: the child self-times its hot
        # region, so the few ms of polling latency never touch the benchmark.
        deadline = t0 + timeout
        while True:
            pid, status, ru = os.wait4(proc.pid, os.WNOHANG)
            if pid == proc.pid:
                proc.returncode = os.waitstatus_to_exitcode(status)
                # ru_maxrss is KB on Linux, bytes on macOS
                scale_div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
                rss_mb = ru.ru_maxrss / scale_div
                break
            if time.perf_counter() > deadline:
                proc.kill()
                os.wait4(proc.pid, 0)
                proc.returncode = -9
                outf.close()
                errf.close()
                return None, None, None, None, None, "timed out after %s" % (
                    human_time(timeout))
            time.sleep(0.02)
    else:                        # non-POSIX fallback: no rusage available
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            outf.close()
            errf.close()
            return None, None, None, None, None, "timed out after %s" % (
                human_time(timeout))
    wall = time.perf_counter() - t0
    outf.seek(0)
    out = outf.read()
    outf.close()
    errf.seek(0)
    errout = errf.read()
    errf.close()
    return proc.returncode, out, errout, wall, rss_mb, None


def parse_output(text):
    """Parse a child's one-line protocol output,
    `OK <bench> <checksum> <best_ms> [<median_ms> <worst_ms>]`.
    Returns (best_seconds, checksum, error, median_seconds, worst_seconds);
    the last two are None for the old 4-field form, so an entry that has not
    learned to report its spread still works."""
    parts = (text or "").split()
    if len(parts) not in (4, 6) or parts[0] != "OK":
        return None, None, "printed something unexpected: %r" % (text or "")[:80], None, None
    try:
        checksum = int(parts[2])
        times = [max(float(p) / 1000.0, 1e-9) for p in parts[3:]]
    except ValueError:
        return None, None, "printed something unexpected: %r" % (text or "")[:80], None, None
    # Clamped away from zero (in `times`): a compiled language at a tiny
    # --scale can report 0.000 ms, and a literal zero would poison every
    # ratio downstream.
    if len(times) == 3:
        return times[0], checksum, None, times[1], times[2]
    return times[0], checksum, None, None, None


def run_one(lang, bench_key, size, reps, warmup=0, timeout=600.0):
    """Returns (compute_seconds, checksum, wall_seconds, peak_rss_mb, error,
    median_seconds, worst_seconds). The error is returned rather than
    printed, so the caller can stop the spinner first."""
    argv = PIN + lang.cmd + [bench_key, str(size), str(reps)]
    if warmup and lang.warmup:
        argv.append(str(warmup))
    code, out, errout, wall, rss_mb, err = launch(argv, timeout)
    if err:
        return None, None, None, None, "%s %s" % (lang.name, err), None, None
    if code != 0:
        # PHP (among others) prints fatal errors to stdout, so look there too
        # before declaring "no output".
        msg = ((errout or "").strip() or (out or "").strip()).splitlines()
        return None, None, None, None, "%s exited %d: %s" % (
            lang.name, code, msg[-1] if msg else "no output"), None, None
    secs, checksum, perr, median, worst = parse_output(out)
    if perr:
        return None, None, None, None, "%s %s" % (lang.name, perr), None, None
    return secs, checksum, wall, rss_mb, None, median, worst


def validate_row(checksums, golden_value):
    """checksums: {lang_key: int} for one benchmark. Returns (ok, value, note).
    A row is valid only when every language agrees AND, if a golden value is
    recorded for this size, matches it -- within-run agreement can never catch
    a bug shared by every implementation, the golden can."""
    distinct = set(checksums.values())
    if not distinct:
        return False, None, "no language produced a result"
    if len(distinct) > 1:
        return False, None, "CHECKSUMS DISAGREE %r" % checksums
    value = distinct.pop()
    if golden_value is not None and value != golden_value:
        return False, value, ("checksum %s does not match the recorded golden %s "
                              "-- every implementation here shares a bug"
                              % (commas(value), commas(golden_value)))
    return True, value, ("matches golden" if golden_value is not None
                         else "no golden recorded for this size")


def measure_startup(lang, trials=5):
    """Time a run whose actual work is negligible, so what's left is launch cost:
    exec + dynamic linking + (VM boot | interpreter init). Best of `trials`."""
    best = None
    for _ in range(trials):
        t0 = time.perf_counter()
        # sieve, not mandelbrot: at n=8 both are negligible everywhere except
        # COBOL, whose decimal float engine turns even an 8x8 mandelbrot into
        # real work that would pollute its startup number
        try:
            proc = subprocess.run(PIN + lang.cmd + ["sieve", "8", "1"],
                                  capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return None      # a wedged interpreter skips the row, not the run
        wall = time.perf_counter() - t0
        if proc.returncode != 0:
            return None
        # subtract the tiny amount of real compute we just paid for
        try:
            wall -= float(proc.stdout.split()[3]) / 1000.0
        except (IndexError, ValueError):
            return None
        if best is None or wall < best:
            best = wall
    return best


def prng_selftest(active):
    """Run the hidden `prng` benchmark -- a checksum over the raw generator
    stream -- in every language, against a reference computed right here in
    arbitrary-precision Python, which has no 64-bit tricks to get wrong.
    Several languages build the 64-bit multiply from 32-bit halves (JS, PHP,
    Ruby, COBOL); quicksort would eventually catch a wrong bit, but this says
    which language and on which output."""
    count = 1_000_000
    state, h = 12345, 0
    mask64 = (1 << 64) - 1
    for _ in range(count):
        state = (state * 6364136223846793005 + 1442695040888963407) & mask64
        h = (h * 31 + (state >> 33)) & 0xFFFFFFFF
    print()
    print("  " + hue(u"▸ PRNG CONFORMANCE", AQUA, bold=True)
          + DIM("  first %s outputs, reference checksum %s"
                % (commas(count), commas(h))))
    bad = 0
    for lang in active:
        secs, checksum, _, _, err, _, _ = run_one(lang, "prng", count, 1)
        if err:
            print("    %s %s  %s" % (hue(u"✘", CORAL, bold=True),
                                     hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                                     hue(err, GOLD)))
            bad += 1
        elif checksum == h:
            print("    %s %s  %s" % (hue(u"✔", LIME, bold=True),
                                     hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                                     DIM("%s in %s" % (commas(checksum), fmt_secs(secs)))))
        else:
            print("    %s %s  %s" % (hue(u"✘", CORAL, bold=True),
                                     hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                                     hue("returned %s, expected %s"
                                         % (commas(checksum), commas(h)), CORAL, bold=True)))
            bad += 1
    print()
    if bad:
        print("  " + hue("%d language(s) failed the PRNG conformance check." % bad,
                         CORAL, bold=True))
        return 1
    print("  " + hue("Every language reproduces the shared PRNG bit-for-bit.",
                     LIME, bold=True))
    return 0


def _git_commit():
    try:
        return subprocess.run(["git", "-C", HERE, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def _cpu_info():
    """CPU model and frequency governor, when the OS will say: numbers from a
    laptop on the powersave governor are a different machine's numbers."""
    info = {}
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as fh:
            info["governor"] = fh.read().strip()
    except Exception:
        pass
    return info


def fmt_secs(s):
    """Keep small timings readable instead of collapsing them to 0.000 s."""
    if s >= 1.0:
        return "%.3f s" % s
    if s >= 0.01:
        return "%.1f ms" % (s * 1e3)
    if s >= 0.001:
        return "%.2f ms" % (s * 1e3)
    return "%.0f us" % (s * 1e6)


def fmt_left(s):
    """A rough time-remaining, rounded hard: pretending to know it to the
    millisecond would be pretending the estimate is better than it is."""
    if s < 1:
        return "<1s left"
    if s < 90:
        return "~%ds left" % int(round(s))
    return "~%dm%02ds left" % divmod(int(round(s)), 60)


def bar(fraction, width=38):
    """A proportional bar. Always shows at least a sliver so nothing looks like zero."""
    filled = fraction * width
    full = int(filled)
    out = u"\u2588" * full
    frac = filled - full
    if full < width:
        out += u" \u258f\u258e\u258d\u258c\u258b\u258a\u2589"[int(frac * 7)] if frac > 0.05 else ""
    return out.ljust(width)[:width] if out.strip() else u"\u258f".ljust(width)


def paint_bar(text, width=38):
    """Light the bar green at the left and red at the right, so a long bar can be
    seen running off into the danger zone. Empty space becomes a dim track."""
    if not USE_COLOR:
        return text
    span = float(max(width - 1, 1))
    out = []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(hue(u"\u2591", TRACK))
        else:
            out.append(hue(ch, heat(i / span)))
    return "".join(out)


# ---------------------------------------------------------------- grand prix

RACING = False              # turned on by --racing

CAR = u"▐█▶"     # rear wing, body, nose cone
CAR_LEN = 3
ROAD = u"╌"                # the track still to cover
DUST = u"·"                # tire dust left behind
SKID = u"≡"                # speed lines right behind a moving car
POST = u"▚"                # the chequered finish post


def car_lane(frac, tint, width=38, moving=True, done=False):
    """One racecar on one lane. The car sits `frac` of the way down the track,
    dust behind it, road ahead, the finish post at the end. Same idea as bar(),
    wearing a firesuit."""
    span = width - CAR_LEN
    pos = int(min(max(frac, 0.0), 1.0) * span)
    out = []
    if pos:
        skid = min(pos, 2) if moving and not done else 0
        out.append(hue(DUST * (pos - skid), STEEL))
        if skid:
            out.append(hue(SKID * skid, STEEL))
    out.append(hue(CAR, tint, bold=True))
    if span - pos:
        out.append(hue(ROAD * (span - pos), TRACK))
    out.append(hue(POST, GOLD if done else SILVER))
    return "".join(out)


def race_replay(lanes, row, width=38):
    """The benchmark, replayed as a race. Every car's speed is its measured
    speed, so the winner crosses the line while the slow cars are exactly as
    far up the track as the numbers say they deserve to be. Because a 100x
    car would then need 100x the animation, the replay switches to a clearly
    labelled fast-forward once the winner is home: the stragglers cross in
    their true order, just not in their true (unwatchable) time."""
    lanes = [l for l in lanes if l.key in row]
    if not lanes:
        return
    fastest = min(row.values())
    order = sorted(lanes, key=lambda l: row[l.key])
    place = dict((l.key, i) for i, l in enumerate(order))
    ratio = dict((l.key, row[l.key] / fastest) for l in lanes)
    rmax = max(ratio.values())

    def lane_line(lang, frac, moving, stats):
        done = frac >= 1.0
        p = place[lang.key]
        if stats and p < len(BADGES):
            badge = hue(BADGES[p][0], BADGES[p][1], bold=True)
        elif stats:
            badge = DIM(u"·")
        else:
            badge = " "
        suffix = ""
        if stats:
            r = ratio[lang.key]
            suffix = " %s  %s" % (hue("%9s" % fmt_secs(row[lang.key]), SILVER),
                                  hue("%6.1fx" % r, heat_ratio(r), bold=True))
        return "    %s %s %s%s" % (
            badge, hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
            car_lane(min(frac, 1.0), lang.tint, width,
                     moving=moving and not done, done=done), suffix)

    if not ANIMATE:
        # A photo finish instead of a replay: where every car was at the
        # moment the winner crossed the line.
        print("    " + DIM(u"⚑ photo finish -- car positions when the "
                           "winner crossed the line"))
        for lang in lanes:
            print(lane_line(lang, 1.0 / ratio[lang.key], moving=False, stats=True))
        return

    # When does each car cross? The winner honestly; everyone else spaced on a
    # log scale through the fast-forward window, which preserves the order.
    T_WIN, T_FF = 2.6, 1.8
    cross = {}
    for lang in lanes:
        r = ratio[lang.key]
        if r <= 1.0 + 1e-9 or rmax <= 1.0 + 1e-9:
            cross[lang.key] = T_WIN
        else:
            cross[lang.key] = T_WIN + T_FF * (math.log(r) / math.log(rmax))
    end = max(cross.values())

    def frac_at(lang, t):
        r = ratio[lang.key]
        if t <= T_WIN:
            return (t / T_WIN) / r
        c = cross[lang.key]
        if c <= T_WIN:
            return 1.0
        base = 1.0 / r
        return min(base + (1.0 - base) * ((t - T_WIN) / (c - T_WIN)), 1.0)

    def draw(header, t, final=False):
        sys.stdout.write("\x1b[%dF" % (len(lanes) + 1))
        sys.stdout.write("    " + header + "\x1b[K\n")
        for lang in lanes:
            f = 1.0 if final else frac_at(lang, t)
            done = f >= 1.0
            sys.stdout.write(lane_line(lang, f, moving=t < cross[lang.key],
                                       stats=done) + "\x1b[K\n")
        sys.stdout.flush()

    sys.stdout.write("\x1b[?25l")
    try:
        print("\n" * len(lanes))     # claim the block, then redraw it in place
        lights = u"●"
        for lit in range(1, 6):              # five red lights...
            draw("  ".join(hue(lights, CORAL, bold=True) if i < lit
                           else DIM(lights) for i in range(5)), 0.0)
            time.sleep(0.22)
        draw(hue("LIGHTS OUT", LIME, bold=True) + DIM("  -- and away they go"), 0.0)
        time.sleep(0.45)
        t0 = time.perf_counter()
        while True:
            t = time.perf_counter() - t0
            if t >= end:
                break
            if t <= T_WIN:
                header = DIM(u"⚑ race replay -- every car at its true "
                             "measured speed")
            else:
                header = DIM(u"▸▸ fast-forwarding the stragglers "
                             "(order preserved, mercy applied)")
            draw(header, t)
            time.sleep(0.033)
        draw(hue(u"⚑ %s takes the chequered flag" % order[0].name,
                 GOLD, bold=True), end, final=True)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def podium_lines(ranked, grow=1.0):
    """The podium, as printable lines. `ranked` is up to three (language,
    average slowdown) pairs, fastest first. `grow` below 1.0 draws the boxes
    part-risen, for the reveal."""
    CW, BW = 20, 12
    HEIGHTS = (6, 4, 3)
    H = max(HEIGHTS) + 1                     # +1 for the name riding on top
    slots = [None, None, None]               # visual order: silver, gold, bronze
    for p, (lang, avg) in enumerate(ranked):
        slots[(1, 0, 2)[p]] = (p, lang, avg)

    def centred(plain, colored, w):
        pad = max(w - len(plain), 0)
        return " " * (pad // 2) + colored + " " * (pad - pad // 2)

    grid = []
    for r in range(H):
        cells = []
        for slot in slots:
            if slot is None:
                cells.append(" " * CW)
                continue
            p, lang, avg = slot
            medal = BADGES[p][1]
            h = max(1, int(round(HEIGHTS[p] * grow)))
            top = H - h                      # row of the box's top border
            if r < top - 1:
                cells.append(" " * CW)
            elif r == top - 1:               # the name, riding on the box
                name = lang.name if p else u"✦ %s ✦" % lang.name
                cells.append(centred(name, hue(name, lang.tint, bold=True), CW))
            elif r == top:
                edge = u"┏" + u"━" * (BW - 2) + u"┓"
                cells.append(centred(edge, hue(edge, medal), CW))
            else:
                inner = r - top - 1
                if inner == 0 and grow >= 1.0:
                    body = BADGES[p][0]
                    body_c = hue(body, medal, bold=True)
                elif inner == 1 and grow >= 1.0:
                    body = "%.1fx" % avg
                    body_c = hue(body, heat_ratio(avg), bold=True)
                else:
                    body, body_c = "", ""
                side = hue(u"┃", medal)
                cells.append(centred(u"┃" + body.center(BW - 2) + u"┃",
                                     side + centred(body, body_c, BW - 2) + side,
                                     CW))
        grid.append("      " + "".join(cells))
    grid.append("      " + gradient(u"━" * (CW * 3), VIOLET, GOLD))
    return grid


def show_podium(ranked):
    print()
    for line in banner_box("THE PODIUM", a=GOLD, b=CORAL, tint=GOLD):
        print("  " + line)
    print()
    if not ANIMATE:
        for line in podium_lines(ranked):
            print(line)
    else:
        block = len(podium_lines(ranked))
        print("\n" * (block - 1))
        sys.stdout.write("\x1b[?25l")
        try:
            steps = 10
            for k in range(1, steps + 1):
                sys.stdout.write("\x1b[%dF" % block)
                for line in podium_lines(ranked, grow=k / float(steps)):
                    sys.stdout.write(line + "\x1b[K\n")
                sys.stdout.flush()
                time.sleep(0.07)
        finally:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
    print("      " + DIM("average slowdown across every benchmark completed; "
                         "lower steps, faster car"))


def reveal(prefix, barstr, suffix, width=38):
    """Draw a bar growing into place. Cheap: about a tenth of a second a row."""
    if not ANIMATE:
        print(prefix + paint_bar(barstr, width) + suffix)
        return
    solid = len(barstr.rstrip()) or 1
    step = max(1, solid // 12)
    for k in range(step, solid, step):
        sys.stdout.write("\r" + prefix + paint_bar(barstr[:k].ljust(width), width) + suffix)
        sys.stdout.flush()
        time.sleep(0.008)
    sys.stdout.write("\r" + prefix + paint_bar(barstr, width) + suffix + "\n")
    sys.stdout.flush()


class Meter(object):
    """A progress bar for work that cannot be asked how far along it is.

    It fills against a duration predicted from results we already have (see
    Estimator), on a thread that does nothing but redraw one line eight times a
    second -- the same thread the spinner used to run on. The process being timed
    is never touched, so a run with the bars takes exactly as long as one without.

    Because the estimate can be wrong, the fill is deliberately honest: linear
    until 90%, then asymptotic. A run that overshoots crawls towards the end of
    the track instead of sitting at 100% pretending to be finished.

    When the work finishes, the live line is replaced by a frozen, filled bar
    that stays on screen, so each language's bar remains visible and the next
    one draws on the line below."""

    FRAMES = u"\u28fe\u28fd\u28fb\u28bf\u287f\u28df\u28ef\u28f7"

    def __init__(self, label, eta=None, width=38, tint=None):
        self.label = label
        self.eta = eta if (eta and eta > 0) else None
        self.width = width
        self.tint = tint or STEEL
        self.ok = True
        self._t0 = 0.0
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._t0 = time.perf_counter()
        if ANIMATE:
            self._thread = threading.Thread(target=self._draw)
            self._thread.daemon = True
            self._thread.start()
        return self

    def _fraction(self, elapsed):
        p = elapsed / self.eta
        if p > 0.9:
            p = 0.9 + 0.1 * (1.0 - math.exp(-(p - 0.9) * 2.0))
        return min(p, 0.995)

    def _draw(self):
        i = 0
        while not self._stop.is_set():
            elapsed = time.perf_counter() - self._t0
            if self.eta is None:
                # nothing to predict against: sweep rather than show a fake fill
                pulse = abs(((i % 24) / 12.0) - 1.0)
                line = "    %s %s  %s" % (
                    hue(self.FRAMES[i % len(self.FRAMES)], heat(pulse)), self.label,
                    DIM("%5.1fs" % elapsed))
            else:
                done = self._fraction(elapsed)
                left = self.eta - elapsed
                track = (car_lane(done, self.tint, self.width) if RACING
                         else paint_bar(bar(done, self.width), self.width))
                line = "    %s %s %s %s  %s" % (
                    hue(self.FRAMES[i % len(self.FRAMES)], heat(done)), self.label,
                    track,
                    hue("%8.1fs" % elapsed, SILVER),
                    DIM("%-12s" % (fmt_left(left) if left > 0.5 else "any moment")))
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.08)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
            sys.stdout.write("\r" + " " * (W + 12) + "\r")
            sys.stdout.flush()
            if exc[0] is None:
                # Leave a frozen, finished bar behind; the next one starts below.
                elapsed = time.perf_counter() - self._t0
                full = bar(1.0, self.width)
                mark = (hue(u"✔", LIME, bold=True) if self.ok
                        else hue(u"✘", CORAL, bold=True))
                if RACING:
                    track = car_lane(1.0 if self.ok else 0.0, self.tint,
                                     self.width, moving=False, done=self.ok)
                elif self.ok:
                    track = paint_bar(full, self.width)
                else:
                    track = hue(full, STEEL)
                sys.stdout.write("    %s %s %s %s  %s\n" % (
                    mark, self.label, track,
                    hue("%8.1fs" % elapsed, SILVER),
                    DIM("%-12s" % ("done" if self.ok else "failed"))))
                sys.stdout.flush()
        return False


# ---------------------------------------------------------------- reports

REPORT_CSS = """
:root { color-scheme: light dark; }
* { margin: 0; box-sizing: border-box; }
body {
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10); --series: #2a78d6;
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: var(--page); color: var(--ink); padding: 32px 20px 48px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) body {
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10); --series: #3987e5;
  }
}
:root[data-theme="dark"] body {
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink2: #c3c2b7;
  --grid: #2c2c2a; --baseline: #383835;
  --border: rgba(255,255,255,0.10); --series: #3987e5;
}
main { max-width: 1060px; margin: 0 auto; }
h1 { font-size: 22px; font-weight: 650; }
.sub { color: var(--ink2); margin: 4px 0 24px; }
.tiles { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }
.tile { flex: 1 1 180px; background: var(--surface); border: 1px solid var(--border);
        border-radius: 10px; padding: 14px 16px; }
.tile .label { font-size: 12px; color: var(--ink2); }
.tile .value { font-size: 26px; font-weight: 600; margin-top: 2px; }
.tile .hint { font-size: 12px; color: var(--muted); margin-top: 2px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
        gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--border);
        border-radius: 10px; padding: 16px 18px; }
.card.wide { grid-column: 1 / -1; }
.card h2 { font-size: 15px; font-weight: 650; }
.card .desc { font-size: 12px; color: var(--ink2); margin: 2px 0 12px; }
.row { display: grid; grid-template-columns: 96px 1fr 84px; align-items: center;
       gap: 10px; padding: 3px 0; border-radius: 6px; }
.row:hover, .row:focus-visible { background: var(--grid); outline: none; }
.row .name { font-size: 13px; color: var(--ink); white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
.row .track { border-left: 1px solid var(--baseline); align-self: stretch;
              display: flex; align-items: center; }
.row .bar { height: 14px; background: var(--series);
            border-radius: 0 4px 4px 0; min-width: 2px; }
.row .val { font-size: 12px; color: var(--ink2); text-align: right;
            font-variant-numeric: tabular-nums; white-space: nowrap; }
.note { font-size: 11px; color: var(--muted); margin-top: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { text-align: right; padding: 5px 10px; border-bottom: 1px solid var(--grid);
         font-variant-numeric: tabular-nums; white-space: nowrap; }
th { color: var(--ink2); font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
.tblwrap { overflow-x: auto; }
.ratio { color: var(--muted); }
footer { margin-top: 28px; text-align: center; font-size: 12px; color: var(--muted); }
footer .credit { display: block; width: fit-content; margin: 0 auto 4px;
                 font-size: 22px; font-weight: 800; letter-spacing: 1px; color: #ff40a0;
                 background: linear-gradient(90deg, #ff40a0, #00e5ff);
                 -webkit-background-clip: text; background-clip: text;
                 -webkit-text-fill-color: transparent; }
#tip { position: fixed; pointer-events: none; opacity: 0; z-index: 10;
       background: var(--surface); color: var(--ink); border: 1px solid var(--border);
       border-radius: 8px; padding: 8px 10px; font-size: 12px;
       box-shadow: 0 4px 14px rgba(0,0,0,0.18); transition: opacity 80ms; }
#tip .t { font-weight: 600; }
#tip .d { color: var(--ink2); }
"""

REPORT_JS = """
var tip = document.getElementById('tip');
function showTip(el, x, y) {
  tip.innerHTML = el.getAttribute('data-tip');
  tip.style.opacity = 1;
  var w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.min(x + 14, window.innerWidth - w - 8) + 'px';
  tip.style.top = Math.min(y + 14, window.innerHeight - h - 8) + 'px';
}
document.querySelectorAll('[data-tip]').forEach(function (el) {
  el.addEventListener('mousemove', function (e) { showTip(el, e.clientX, e.clientY); });
  el.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
  el.addEventListener('focus', function () {
    var r = el.getBoundingClientRect(); showTip(el, r.left + 40, r.bottom);
  });
  el.addEventListener('blur', function () { tip.style.opacity = 0; });
});
"""


def bar_rows(entries, fmt_val, fmt_tip):
    """The one chart shape the report uses: horizontal bars, linear scale,
    sorted fastest first, value at the tip -- honest lengths, so the story
    ('how much does your language cost you') is the picture itself."""
    import html as html_mod
    top = max(v for _, v in entries)
    out = []
    for lang, v in sorted(entries, key=lambda e: e[1]):
        pct = max(v / top * 100.0, 0.4)
        out.append(
            '<div class="row" tabindex="0" data-tip="%s">'
            '<span class="name">%s</span>'
            '<span class="track"><span class="bar" style="width:%.2f%%"></span></span>'
            '<span class="val">%s</span></div>'
            % (html_mod.escape(fmt_tip(lang, v), quote=True),
               html_mod.escape(lang.name), pct, html_mod.escape(fmt_val(v))))
    return "".join(out)


def write_report(active, benches, results, startup, run_meta, total_wall):
    """One self-contained HTML page of charts plus the raw numbers, written
    next to the binaries, and a results.json beside it for anything that would
    rather parse than look."""
    import html as html_mod
    esc = html_mod.escape
    shown = [b for b in benches if b["key"] in results]
    langs_of = lambda row: [l for l in active if l.key in row]

    avg = {}
    for lang in active:
        rs = [row[lang.key] / min(row.values())
              for row in results.values() if lang.key in row]
        if rs:
            avg[lang.key] = gmean(rs)

    # ---- machine-readable twin ----
    rss = run_meta.get("rss_mb") or {}
    medians = run_meta.get("medians") or {}
    spreads = run_meta.get("spreads") or {}
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": _host(), "machine": _cpu_info(),
        "total_wall_seconds": total_wall,
        "workload": run_meta,
        "languages": dict((l.key, {"name": l.name, "version": l.version,
                                   "note": l.note}) for l in active),
        "benchmarks": dict(
            (b["key"], {"title": b["title"], "size": run_meta["sizes"][b["key"]],
                        "checksum": run_meta["checksums"].get(b["key"]),
                        "seconds": results[b["key"]],
                        "seconds_median": medians.get(b["key"], {}),
                        "spread": spreads.get(b["key"], {}),
                        "peak_rss_mb": rss.get(b["key"], {})}) for b in shown),
        "startup_seconds": startup,
        "geomean_slowdown": avg,
    }
    with open(os.path.join(BUILD, "results.json"), "w") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)

    # ---- the page ----
    fastest = min(avg, key=avg.get) if avg else None
    slowest = max(avg, key=avg.get) if avg else None
    name_of = dict((l.key, l.name) for l in active)
    gap, gap_where = 0.0, ""
    for b in shown:
        row = results[b["key"]]
        r = max(row.values()) / min(row.values())
        if r > gap:
            gap, gap_where = r, b["title"].lower()

    tiles = []
    if fastest:
        tiles.append(("Fastest overall", name_of[fastest],
                      "%.1fx geometric-mean slowdown" % avg[fastest]))
        tiles.append(("Slowest overall", name_of[slowest],
                      "%.1fx geometric-mean slowdown" % avg[slowest]))
    tiles.append(("Widest single gap", "%.0fx" % gap, "on %s" % gap_where))
    tiles.append(("This run", "%d × %d" % (len(active), len(shown)),
                  "languages × benchmarks, %gx scale, best of %d" %
                  (run_meta["scale"], run_meta["reps"])))
    tile_html = "".join(
        '<div class="tile"><div class="label">%s</div><div class="value">%s</div>'
        '<div class="hint">%s</div></div>' % tuple(esc(x) for x in t) for t in tiles)

    cards = []
    if len(avg) > 1:
        entries = [(l, avg[l.key]) for l in active if l.key in avg]
        cards.append(
            '<div class="card wide"><h2>Leaderboard</h2>'
            '<div class="desc">Geometric-mean slowdown versus the fastest language, '
            'across every benchmark completed. Bar length is proportional to it.</div>'
            + bar_rows(entries, lambda v: "%.1fx" % v,
                       lambda l, v: "<span class='t'>%s</span><br>"
                       "<span class='d'>%.2fx the fastest language, geometric "
                       "mean over %d benchmarks</span>"
                       % (l.name, v, sum(1 for row in results.values() if l.key in row)))
            + '</div>')

    for b in shown:
        row = results[b["key"]]
        best = min(row.values())
        entries = [(l, row[l.key]) for l in langs_of(row)]
        tip = (lambda best_=best, b_=b:
               lambda l, v: "<span class='t'>%s</span><br><span class='d'>"
               "%s &middot; %.1fx the fastest<br>%s</span>"
               % (l.name, fmt_secs(v), v / best_,
                  b_["describe"](run_meta["sizes"][b_["key"]])))()
        cards.append(
            '<div class="card"><h2>%s</h2><div class="desc">%s</div>%s'
            '<div class="note">%s</div></div>'
            % (esc(b["title"].title()), esc(b["describe"](run_meta["sizes"][b["key"]])),
               bar_rows(entries, fmt_secs, tip),
               esc("measures " + b["what"])))

    if len(startup) > 1:
        entries = [(l, startup[l.key]) for l in active if l.key in startup]
        cards.append(
            '<div class="card wide"><h2>Process startup</h2>'
            '<div class="desc">Launch cost alone: exec, dynamic linking, VM or '
            'interpreter boot. Irrelevant for a batch job, decisive for a tool '
            'invoked a thousand times in a shell loop.</div>'
            + bar_rows(entries, fmt_secs,
                       lambda l, v: "<span class='t'>%s</span><br>"
                       "<span class='d'>%s to start, best of 5</span>" % (l.name, fmt_secs(v)))
            + '</div>')

    head = "<tr><th>Language</th>" + "".join(
        "<th>%s</th>" % esc(b["key"]) for b in shown) + "<th>average</th></tr>"
    body = []
    for lang in active:
        cells = []
        for b in shown:
            row = results[b["key"]]
            if lang.key in row:
                cells.append("<td>%s <span class='ratio'>%.1fx</span></td>"
                             % (esc(fmt_secs(row[lang.key])),
                                row[lang.key] / min(row.values())))
            else:
                cells.append("<td>&mdash;</td>")
        cells.append("<td>%s</td>" % ("%.1fx" % avg[lang.key] if lang.key in avg else "&mdash;"))
        body.append("<tr><td>%s</td>%s</tr>" % (esc(lang.name), "".join(cells)))
    table = ('<div class="card wide"><h2>Every number</h2>'
             '<div class="desc">Compute time (best of %d) and slowdown versus '
             'that benchmark&rsquo;s fastest language.</div>'
             '<div class="tblwrap"><table>%s%s</table></div></div>'
             % (run_meta["reps"], head, "".join(body)))

    uname = platform.uname()
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Speed Test Report</title><style>%s</style></head><body><main>"
            "<h1>How much does your language cost you?</h1>"
            "<div class='sub'>%s &middot; %s %s, %s cores &middot; "
            "%gx standard workload, best of %d run%s &middot; total %s</div>"
            "<div class='tiles'>%s</div><div class='grid'>%s%s</div>"
            "<footer><span class='credit'>&#10022; made by guy5116 &#10022;</span>"
            "generated by run.py &middot; with agentic ai"
            "</footer></main><div id='tip'></div><script>%s</script></body></html>"
            % (REPORT_CSS, esc(payload["generated"]), esc(uname.system),
               esc(uname.machine), os.cpu_count(), run_meta["scale"],
               run_meta["reps"], "" if run_meta["reps"] == 1 else "s",
               esc(human_time(total_wall)), tile_html, "".join(cards), table,
               REPORT_JS))
    path = os.path.join(BUILD, "report.html")
    with open(path, "w") as fh:
        fh.write(page)
    return path


# ---------------------------------------------------------------- main

def main():
    global USE_COLOR, TRUECOLOR, ANIMATE, RACING

    ap = argparse.ArgumentParser(
        description="Compare x86-64 assembly / C / C++ / Rust / Swift / Go / Java / "
                    "C# / JavaScript / Lua / Perl / PHP / Python / NumPy / Ruby / "
                    "COBOL on identical work.")
    ap.add_argument("--scale", type=float, default=None,
                    help="workload multiplier (1.0 = standard)")
    ap.add_argument("--quick", action="store_true", help="tiny workload, for a fast demo")
    ap.add_argument("--heavy", action="store_true", help="the 'massive task' scale")
    ap.add_argument("--reps", type=int, default=None, help="runs per language, best wins")
    ap.add_argument("--min-time", type=float, default=None, metavar="T",
                    help="grow --reps per language until the timed compute "
                         "should last at least T seconds (capped at 50 reps): "
                         "fast languages repeat more instead of reporting "
                         "sub-millisecond timings -- the pyperf / testing.B "
                         "behaviour")
    ap.add_argument("--warmup", type=int, default=0, metavar="N",
                    help="N untimed in-process runs before the timed ones, for "
                         "the JIT runtimes that honour it (Java, C#, JavaScript, "
                         "PHP, Ruby) -- the JMH treatment; everyone else ignores it")
    ap.add_argument("--pin", nargs="?", const=-1, type=int, default=None,
                    metavar="CORE",
                    help="pin every benchmark process to one CPU core with "
                         "taskset (steadier numbers; give a core number, or "
                         "let it pick the last one). Note it also squeezes "
                         "the JIT compiler threads onto that core")
    ap.add_argument("--serial-gc", action="store_true",
                    help="ask the GC runtimes to stay on one thread (Java "
                         "-XX:+UseSerialGC, .NET workstation GC, GOMAXPROCS=1, "
                         "node --single-threaded-gc) so 'single-threaded' "
                         "covers the whole process, not just your code")
    ap.add_argument("--shuffle", action="store_true",
                    help="run the languages in a fresh random order for every "
                         "benchmark, so nobody always runs coldest or hottest")
    ap.add_argument("--seed", type=int, default=None,
                    help="seed for --shuffle (recorded in results.json, so a "
                         "shuffled run can be reproduced)")
    ap.add_argument("--selftest", action="store_true",
                    help="run only the hidden PRNG conformance benchmark in "
                         "every language and verify each one bit-for-bit "
                         "against the reference stream, then exit")
    ap.add_argument("--only", default="", help="comma-separated benchmark names")
    ap.add_argument("--skip", default="",
                    help="comma-separated languages to skip (asm, c, cpp, rust, swift, "
                         "go, java, csharp, js, lua, perl, php, python, numpy, ruby, "
                         "cobol)")
    ap.add_argument("--skip-bench", default="",
                    help="comma-separated benchmarks to skip (mandelbrot, sieve, "
                         "quicksort, wordcount, binarytrees, matmul)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any language fails, times out, or is "
                         "missing from --require (for CI; checksum failures "
                         "always exit non-zero)")
    ap.add_argument("--require", default="",
                    help="comma-separated language keys that must be available; "
                         "with --strict, a missing one fails the run")
    ap.add_argument("--sorted", action="store_true",
                    help="list each benchmark's times quickest to slowest "
                         "instead of in language order")
    ap.add_argument("--racing", action="store_true",
                    help="grand prix mode: every language is a racecar, each "
                         "benchmark replays as a race, and there is a podium")
    ap.add_argument("--plain", action="store_true",
                    help="no colour, no banner, nothing moves -- for logs and pipes")
    ap.add_argument("--no-animation", action="store_true",
                    help="keep the colour, hold still")
    args = ap.parse_args()

    if args.plain:
        USE_COLOR = TRUECOLOR = ANIMATE = False
    if args.no_animation:
        ANIMATE = False
    RACING = args.racing

    scale = args.scale if args.scale is not None else (0.08 if args.quick else 6.0 if args.heavy else 1.0)
    reps = args.reps if args.reps is not None else (1 if args.quick else 3)
    warmup = max(0, args.warmup)

    global PIN
    if args.pin is not None:
        if platform.system() == "Linux" and shutil.which("taskset"):
            core = args.pin if args.pin >= 0 else max(0, (os.cpu_count() or 1) - 1)
            PIN = ["taskset", "-c", str(core)]
        else:
            print(hue("--pin needs Linux and taskset; running unpinned", GOLD))
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    skip = set(x.strip().lower() for x in args.skip.split(",") if x.strip())
    skip_bench = set(x.strip().lower() for x in args.skip_bench.split(",") if x.strip())

    unknown = skip_bench - set(b["key"] for b in BENCHMARKS)
    if unknown:
        print(hue("unknown benchmark(s) in --skip-bench: %s" % ", ".join(sorted(unknown)),
                  CORAL, bold=True))
        print(DIM("  known: " + ", ".join(b["key"] for b in BENCHMARKS)))
        return 1

    benches = [b for b in BENCHMARKS
               if (not only or b["key"] in only) and b["key"] not in skip_bench]
    if not benches:
        print(hue("no benchmarks left after --only %s / --skip-bench %s"
                  % (args.only or "(all)", args.skip_bench or "(none)"), CORAL, bold=True))
        return 1

    # The 32-bit guard runs here, where `benches` is built, not inside the
    # benchmark loop: the header and the "about N minutes ahead" estimate must
    # not promise work the loop would then refuse to run. Skipping is not a
    # failure -- whatever survives the cap still runs and still validates.
    too_big = [b for b in benches if sized(b, scale) > INT32_MAX]
    for b in too_big:
        print("  " + hue("%s skipped: size %s overflows the 32-bit languages "
                         "(largest usable --scale is about %d)"
                         % (b["title"], commas(sized(b, scale)), int(int32_cap(b))),
                         CORAL, bold=True))
    benches = [b for b in benches if b not in too_big]

    # ---- header ----
    print()
    if USE_COLOR:
        for i, row in enumerate(banner()):
            t = i / 4.0
            print("  " + gradient(row, _lerp(PINK, VIOLET, t) + (PINK[3],),
                                  _lerp(AQUA, SKY, t) + (AQUA[3],), bold=True))
        print()
    print("  " + hue("HOW MUCH DOES YOUR LANGUAGE COST YOU?", GOLD, bold=True)
          if USE_COLOR else "  " + BOLD("HOW MUCH DOES YOUR LANGUAGE COST YOU?"))
    print("  " + DIM("Identical algorithms, identical input, fifteen languages "
                     "-- and NumPy."))
    print()
    print("  " + neon_rule())

    est = Estimator()

    print()
    print("  " + hue(u"\u25b8 BUILDING", AQUA, bold=True))
    t0 = time.perf_counter()
    with Meter(hue("compiling".ljust(NAMEW), STEEL), est.build):
        langs = build_all(skip)
    est.record_build(time.perf_counter() - t0)
    tick, cross = hue(u"\u2714", LIME, bold=True), hue(u"\u2718", CORAL, bold=True)
    for lang in langs:
        if lang.available:
            print("    %s %s  %s" % (tick, hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                                     DIM("%s (%s)" % (lang.version[:44], lang.note))))
        else:
            print("    %s %s  %s" % (cross, DIM(lang.name.ljust(NAMEW)),
                                     hue(lang.reason, GOLD)))
    active = [l for l in langs if l.available]
    required = set(x.strip().lower() for x in args.require.split(",") if x.strip())
    missing = required - set(l.key for l in active)
    if missing:
        print("  " + hue("required language(s) missing: %s" % ", ".join(sorted(missing)),
                         CORAL, bold=True))
        if args.strict:
            return 1
    if len(active) < 2:
        print(hue("\n  Need at least two working languages to compare. Nothing to do.",
                  CORAL, bold=True))
        return 1

    if args.serial_gc:
        # Squeeze the runtimes' background GC/JIT helpers onto one thread so
        # "single-threaded benchmark" describes the whole process. Env vars
        # land on every child we spawn; the JVM and V8 need argv flags.
        os.environ["GOMAXPROCS"] = "1"
        os.environ["DOTNET_gcServer"] = "0"
        os.environ["DOTNET_gcConcurrent"] = "0"
        for lang in active:
            if lang.key == "java":
                lang.cmd = lang.cmd[:1] + ["-XX:+UseSerialGC"] + lang.cmd[1:]
                lang.note += ", serial GC"
            elif lang.key == "js":
                lang.cmd = lang.cmd[:1] + ["--single-threaded-gc"] + lang.cmd[1:]
                lang.note += ", single-threaded GC"
            elif lang.key == "csharp":
                lang.note = lang.note.replace("server GC", "workstation GC")

    if args.selftest:
        return prng_selftest(active)

    uname = platform.uname()
    cpuinfo = _cpu_info()
    print()
    print("    " + hue(u"\u2699 machine ", STEEL)
          + hue("%s %s, %s cores" % (uname.system, uname.machine, os.cpu_count()), SKY)
          + (DIM("  (%s)" % cpuinfo["cpu"]) if cpuinfo.get("cpu") else ""))
    gov = cpuinfo.get("governor")
    if gov and gov not in ("performance",):
        print("    " + hue("  \u26a0 cpufreq governor is '%s', not 'performance' -- "
                           "clock speed will wander mid-run" % gov, GOLD))
    try:
        if os.getloadavg()[0] > max(1.0, (os.cpu_count() or 1) * 0.5):
            print("    " + hue("  \u26a0 load average %.1f -- something else is "
                               "competing for this machine" % os.getloadavg()[0], GOLD))
    except OSError:
        pass
    print("    " + hue(u"\u2261 workload", STEEL)
          + hue(" %g x standard, best of %d run%s per language"
                % (scale, reps, "" if reps == 1 else "s"), SKY))
    if reps > 1:
        print("    " + DIM("           (repeats happen inside one process, so the JIT "
                           "gets to warm up)"))
    if args.min_time:
        print("    " + DIM("           (--min-time %g: reps grow per language until "
                           "the estimate reaches it, cap 50)" % args.min_time))
    if warmup:
        print("    " + DIM("           (+%d untimed warm-up run%s first for the JIT "
                           "runtimes: Java, C#, JavaScript, PHP, Ruby)"
                           % (warmup, "" if warmup == 1 else "s")))
    if PIN:
        print("    " + DIM("           (every process pinned to core %s)" % PIN[-1]))
    print("    " + hue(u"\u25f4 estimate", STEEL)
          + hue(" about %s of benchmarking ahead"
                % human_time(est.total(active, benches, scale, reps)), SKY))
    if ANIMATE:
        print("    " + DIM("           (predicted from previous results -- the bars "
                           "fill against it)"))

    # ---- run ----
    # Golden checksums, recorded from cross-verified runs at the stock scales:
    # they catch a bug shared by every implementation in a run, which
    # within-run agreement never can, and they let a single-language run
    # validate itself.
    try:
        with open(os.path.join(HERE, "golden.json")) as fh:
            GOLDEN = json.load(fh)
    except Exception:
        GOLDEN = {}

    results = {}   # bench key -> {lang key: best seconds}
    sizes, checkvals = {}, {}      # per bench key, for the report
    rss_all = {}   # bench key -> {lang key: peak MB}
    med_all, spread_all = {}, {}   # bench key -> {lang key: median s / spread}
    invalid, errors = 0, 0
    # --shuffle used to draw from an unseeded random, so a shuffled run could
    # never be reproduced; results.json now records the seed and the order.
    seed = args.seed if args.seed is not None else int(time.time()) & 0xFFFFFFFF
    shuffler = random.Random(seed)
    order_used = {}          # bench key -> [lang keys in the order they ran]
    total_wall = time.perf_counter()

    for idx, bench in enumerate(benches, 1):
        size = sized(bench, scale)
        print()
        for line in banner_box("%d/%d   %s" % (idx, len(benches), bench["title"])):
            print("  " + line)
        print("  " + hue(bench["describe"](size), VIOLET, bold=True))
        print("  " + DIM("Measures: " + bench["what"]))
        print("  " + DIM(bench["why"]))
        print()

        row, checksums, rss_row, failures, notes = {}, {}, {}, [], []
        med_row, spread_row, reps_row = {}, {}, {}
        run_order = list(active)
        if args.shuffle:
            shuffler.shuffle(run_order)
        order_used[bench["key"]] = [l.key for l in run_order]
        for lang in run_order:
            if bench["key"] in lang.sits_out:
                notes.append("%s %s" % (lang.name.ljust(NAMEW),
                                        lang.sits_out[bench["key"]]))
                continue
            label = hue(lang.name.ljust(NAMEW), lang.tint, bold=True)
            lang_reps = reps
            if args.min_time:
                # Reps run inside the child, so the runner cannot adapt
                # mid-process -- but it can size them up front: one rep's
                # predicted cost is the difference between a 2-rep and a
                # 1-rep estimate (rate times units, bias and all).
                per_rep = max(est.estimate(lang.key, bench, size, 2)
                              - est.estimate(lang.key, bench, size, 1), 1e-9)
                lang_reps = min(max(reps, math.ceil(args.min_time / per_rep)), 50)
            # warm-up runs cost wall-clock like any other rep, so the meter
            # and the timing cache both count them as reps
            eff_reps = lang_reps + (warmup if lang.warmup else 0)
            guess = est.estimate(lang.key, bench, size, eff_reps)
            with Meter(label, guess, tint=lang.tint) as meter:
                secs, checksum, wall, rss, err, median, worst = run_one(
                    lang, bench["key"], size, lang_reps, warmup,
                    timeout=max(600.0, 20.0 * guess))
                meter.ok = err is None
            est.record(lang.key, bench, size, eff_reps, wall)
            if err:
                failures.append(err)
                errors += 1
                continue
            if secs is None:
                continue
            row[lang.key] = secs
            checksums[lang.key] = checksum
            if rss is not None:
                rss_row[lang.key] = round(rss, 1)
            if median is not None and worst is not None:
                # spread: how far the worst rep strayed from the best one --
                # the noise best-of-N would otherwise silently hide
                med_row[lang.key] = median
                spread_row[lang.key] = (worst - secs) / secs
                reps_row[lang.key] = lang_reps
        for msg in notes:
            print("    " + DIM(u"\u25cb ") + hue(msg, GOLD))
        for err in failures:
            print("    " + hue(u"\u2718 " + err, CORAL))

        if not row:
            print("    " + hue("every language failed on this benchmark", CORAL, bold=True))
            continue

        if ANIMATE:
            # separate the finished progress bars above from the timings below
            print("    " + DIM(u"─" * (W - 4)))
        slowest = max(row.values())
        fastest = min(row.values())
        rank = dict((k, i) for i, (k, _) in
                    enumerate(sorted(row.items(), key=lambda kv: kv[1])))
        ordered = [l for l in active if l.key in row]
        if args.sorted:
            ordered.sort(key=lambda l: row[l.key])
        if RACING:
            race_replay(ordered, row)
        else:
            for lang in ordered:
                secs = row[lang.key]
                ratio = secs / fastest
                place = rank[lang.key]
                badge = (hue(BADGES[place][0], BADGES[place][1], bold=True)
                         if place < len(BADGES) else DIM(u"\u00b7"))
                prefix = "    %s %s " % (badge, hue(lang.name.ljust(NAMEW), lang.tint, bold=True))
                spread = spread_row.get(lang.key)
                tail = ""
                if spread is not None and reps_row.get(lang.key, 1) > 1:
                    tail = DIM(" \u00b1%d%%" % round(spread * 100))
                    if spread > 0.15:
                        tail += " " + hue(u"\u26a0 noisy", GOLD, bold=True)
                suffix = " %s  %s%s" % (hue("%9s" % fmt_secs(secs), SILVER),
                                        hue("%6.1fx" % ratio, heat_ratio(ratio), bold=True),
                                        tail)
                reveal(prefix, bar(secs / slowest), suffix)

        ok, value, note = validate_row(checksums,
                                       GOLDEN.get(bench["key"], {}).get(str(size)))
        if ok:
            print("    " + hue(u"\u2714", LIME)
                  + DIM(" checksum %s -- all %d languages agree, %s"
                        % (commas(value), len(checksums), note)))
            if row and min(row.values()) < 0.05:
                print("    " + DIM("  (fastest entry under 50 ms -- ratios this small "
                                   "are mostly noise; raise --scale to mean them)"))
            # an invalid row is dropped entirely: it must not feed the summary,
            # the leaderboard, the human-scale section or results.json
            results[bench["key"]] = row
            sizes[bench["key"]] = size
            checkvals[bench["key"]] = value
            rss_all[bench["key"]] = rss_row
            med_all[bench["key"]] = med_row
            spread_all[bench["key"]] = spread_row
        else:
            print("    " + hue(u"\u2718 " + note + " -- this row is EXCLUDED "
                               "from every summary", CORAL, bold=True))
            invalid += 1

    total_wall = time.perf_counter() - total_wall

    # ---- summary ----
    if len(results) > 1:
        print()
        for line in banner_box("SUMMARY -- slowdown versus the fastest language",
                               a=VIOLET, b=PINK, tint=GOLD):
            print("  " + line)
        print()
        shown = [b for b in benches if b["key"] in results]
        hdr = "".join("%12s" % b["key"][:11] for b in shown) + "%12s" % "geo mean"
        print("      " + " " * NAMEW + hue(hdr, AQUA, bold=True))
        for lang in active:
            cells, ratios = [], []
            for b in shown:
                row = results.get(b["key"])
                if not row or lang.key not in row:
                    cells.append(DIM("%12s" % "-"))
                    continue
                r = row[lang.key] / min(row.values())
                ratios.append(r)
                cells.append(hue("%12s" % ("%.1fx" % r), heat_ratio(r)))
            avg = gmean(ratios) if ratios else 0
            print("      %s%s%s" % (hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                                    "".join(cells),
                                    hue("%12s" % ("%.1fx" % avg), heat_ratio(avg), bold=True)))

    # ---- the leaderboard: the same summary, sorted and drawn ----
    avg_ratio = {}
    for lang in active:
        rs = [row[lang.key] / min(row.values())
              for row in results.values() if lang.key in row]
        if rs:
            avg_ratio[lang.key] = gmean(rs)
    if len(avg_ratio) > 1 and len(results) > 1:
        print()
        for line in banner_box("THE LEADERBOARD", a=LIME, b=SKY, tint=GOLD):
            print("  " + line)
        print("  " + DIM("Geometric-mean slowdown across every benchmark "
                         "completed, fastest first."))
        print()
        worst_avg = max(avg_ratio.values())
        ranked = sorted((l for l in active if l.key in avg_ratio),
                        key=lambda l: avg_ratio[l.key])
        for place, lang in enumerate(ranked):
            r = avg_ratio[lang.key]
            badge = (hue(BADGES[place][0], BADGES[place][1], bold=True)
                     if place < len(BADGES) else DIM(u"·"))
            prefix = "    %s %s " % (badge, hue(lang.name.ljust(NAMEW),
                                                lang.tint, bold=True))
            suffix = " %s" % hue("%6.1fx" % r, heat_ratio(r), bold=True)
            reveal(prefix, bar(r / worst_avg), suffix)

    # ---- the podium ----
    if RACING and results:
        avg = {}
        for lang in active:
            rs = [row[lang.key] / min(row.values())
                  for row in results.values() if lang.key in row]
            if rs:
                avg[lang.key] = gmean(rs)
        ranked = sorted((l for l in active if l.key in avg),
                        key=lambda l: avg[l.key])[:3]
        if ranked:
            show_podium([(l, avg[l.key]) for l in ranked])

    # ---- the part people actually remember ----
    ref = None
    for candidate in ("c", "asm", "cpp", "rust", "swift", "go", "java", "csharp",
                      "js", "lua", "perl", "php", "python", "ruby", "cobol"):
        if any(candidate in row for row in results.values()):
            ref = candidate
            break
    if ref and results:
        ref_name = next(l.name for l in langs if l.key == ref)
        totals = {}
        for lang in active:
            vals = [(row[lang.key], row[ref]) for row in results.values()
                    if lang.key in row and ref in row]
            if vals:
                totals[lang.key] = sum(v[0] for v in vals) / sum(v[1] for v in vals)

        print()
        for line in banner_box("PUT IT ON A HUMAN SCALE", a=AQUA, b=LIME, tint=GOLD):
            print("  " + line)
        for hours, label in ((1 / 60.0, "A one-minute job in %s" % ref_name),
                             (1.0, "A one-hour job in %s" % ref_name),
                             (8.0, "An overnight 8-hour job in %s" % ref_name)):
            print()
            print("  " + hue(label, VIOLET, bold=True) + DIM(" takes:"))
            for lang in active:
                if lang.key not in totals:
                    continue
                secs = hours * 3600 * totals[lang.key]
                txt = human_time(secs)
                marker = ""
                if lang.key == ref:
                    txt, marker = txt.ljust(20), DIM("  (baseline)")
                print("    %s %s%s" % (
                    hue(lang.name.ljust(NAMEW), lang.tint, bold=True),
                    hue(txt, heat_ratio(totals[lang.key])), marker))

    # ---- startup cost, which matters more than people think ----
    startup = {}
    for i, lang in enumerate(active if results else []):
        # The one bar that needs no estimate at all: progress here is exactly
        # "languages done", and it is drawn between measurements, never during one.
        if ANIMATE:
            sys.stdout.write("\r    %s %s %s %s" % (
                hue(u"\u25b8", heat(float(i) / len(active))),
                hue("startup".ljust(NAMEW), STEEL),
                paint_bar(bar(float(i) / len(active)), 38),
                DIM("measuring " + lang.name)))
            sys.stdout.flush()
        secs = measure_startup(lang)
        est.record_startup(lang.key, secs)
        if secs is not None:
            startup[lang.key] = secs
    if ANIMATE:
        sys.stdout.write("\r" + " " * (W + 12) + "\r")
        sys.stdout.flush()
    if len(startup) > 1:
        print()
        for line in banner_box("AND BEFORE ANY WORK HAPPENS AT ALL", a=GOLD, b=CORAL,
                               tint=GOLD):
            print("  " + line)
        print("  " + DIM("Process startup: irrelevant for a big batch job, decisive for a\n"
                         "  command-line tool you invoke a thousand times in a shell loop."))
        print()
        worst = max(startup.values())
        best = min(startup.values())
        for lang in active:
            if lang.key not in startup:
                continue
            s = startup[lang.key]
            prefix = "    %s " % hue(lang.name.ljust(NAMEW), lang.tint, bold=True)
            suffix = " %s" % hue(human_time(s), heat_ratio(s / best), bold=True)
            if RACING:
                # off the grid: how far each car got in the time the quickest
                # launcher took to get rolling
                print(prefix + car_lane(best / s, lang.tint, 24, moving=False,
                                        done=s <= best) + suffix)
            else:
                reveal(prefix, bar(s / worst, 24), suffix, width=24)

    # ---- keep the numbers: an HTML report with charts, and raw JSON ----
    if results:
        try:
            report = write_report(active, benches, results, startup,
                                  {"scale": scale, "reps": reps, "sizes": sizes,
                                   "checksums": checkvals, "rss_mb": rss_all,
                                   "medians": med_all, "spreads": spread_all,
                                   "argv": sys.argv[1:], "seed": seed,
                                   "order": order_used, "commit": _git_commit(),
                                   "pinned": PIN[-1] if PIN else None,
                                   "serial_gc": args.serial_gc},
                                  total_wall)
        except Exception as e:
            print()
            print("    " + DIM("report skipped: %s" % e))
        else:
            print()
            print("    " + hue(u"▤ report  ", STEEL)
                  + hue(os.path.relpath(report, os.getcwd()), SKY, bold=True)
                  + DIM("  -- the same numbers as charts, plus results.json"))

    print()
    print("  " + neon_rule())
    if not results:
        # every benchmark was skipped or failed: no summary, no ceremony
        print("  " + hue("Nothing ran.", GOLD, bold=True)
              + DIM("  Every benchmark was skipped or produced no valid row."))
        print()
    else:
        print("  " + hue("Total time: %s." % human_time(total_wall), AQUA)
              + DIM("  Read the README for what these numbers do"))
        print("  " + DIM("and do not mean -- especially before quoting them at anyone."))
        print()

        # ---- credits ----
        rows = banner("MADE BY GUY5116")
        sub = smallcaps("with agentic ai")
        star = hue(u"✦", GOLD, bold=True)
        wide = max(len(r) for r in rows)
        left = " " * (2 + max((W - wide) // 2, 0))
        if ANIMATE and TRUECOLOR:
            # one slow ripple of the gradient across the letters, then hold
            sys.stdout.write("\n" * len(rows))
            for frame in range(28):
                sys.stdout.write("\033[%dF" % len(rows))
                for i, row in enumerate(rows):
                    sys.stdout.write(left + shimmer(row, frame * 0.4 - i * 0.3) + "\n")
                sys.stdout.flush()
                time.sleep(0.05)
            sys.stdout.write("\033[%dF" % len(rows))
        for row in rows:
            print(left + gradient(row, PINK, AQUA, bold=True))
        print()
        print(" " * (2 + max((W - len(sub) - 6) // 2, 0))
              + star + "  " + hue(sub, STEEL) + "  " + star)
        print()
    est.save()
    if invalid:
        print("  " + hue("%d benchmark(s) had disagreeing or non-golden checksums; "
                         "exit 1." % invalid, CORAL, bold=True))
        return 1
    if args.strict and errors:
        print("  " + hue("--strict: %d language run(s) failed; exit 1." % errors,
                         CORAL, bold=True))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  interrupted")
        sys.exit(130)
