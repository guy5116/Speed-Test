let
  pkgs = import <nixpkgs> {};
  # Swift is pinned to a release channel: on some <nixpkgs> revisions the
  # swift attribute is an uncached from-source build of the compiler
  # (hours), and swiftc only works inside its own clang stdenv anyway.
  swiftPkgs = import (fetchTarball "https://channels.nixos.org/nixos-25.05/nixexprs.tar.xz") {};
in
# The stdenv override gives the shell Swift's clang environment, which is
# what lets a bare `swiftc` compile and link. gcc/g++ for the C, C++ and
# assembly entries still resolve from your system profile.
(swiftPkgs.mkShell.override { stdenv = swiftPkgs.swiftPackages.stdenv; }) {
  packages = [
    pkgs.python314
    pkgs.rustc
    pkgs.lua5_4
    pkgs.perl
    pkgs.ruby
    pkgs.nasm        # x86-64 assembly (linked against libc by the C compiler)
    pkgs.php         # PHP CLI
    pkgs.dotnet-sdk  # C#
    swiftPkgs.swift  # Swift
    swiftPkgs.swiftPackages.Dispatch
    pkgs.gnucobol.bin # COBOL (GnuCOBOL; the package's default output lacks cobc)
  ];
  # the Swift runtime needs libdispatch at process start
  LD_LIBRARY_PATH = swiftPkgs.lib.makeLibraryPath [ swiftPkgs.swiftPackages.Dispatch ];
}
