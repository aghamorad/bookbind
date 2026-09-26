#!/bin/zsh
# Compile Bookbind's window and place it in the .app.
#
# The result is committed, because one of the three released builds is assembled on
# a Linux runner that has no Swift compiler — so all three get the same binary from
# the repo rather than two of them building one.
#
#   ./build/make_window.sh
#
# Needs Xcode's command line tools (`xcode-select --install`, which is already there
# wherever `swiftc` is).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="Bookbind.app/Contents/MacOS/Bookbind"
TMP="build/tmp"
mkdir -p "$TMP"
SDK=$(xcrun --sdk macosx --show-sdk-path)

for arch in arm64 x86_64; do
  echo "compiling $arch"
  swiftc -parse-as-library -swift-version 5 -O \
    -sdk "$SDK" -target "${arch}-apple-macos11.0" \
    -o "$TMP/Bookbind-$arch" build/window.swift
done

lipo -create -output "$OUT" "$TMP/Bookbind-arm64" "$TMP/Bookbind-x86_64"
chmod +x "$OUT"
rm -rf "$TMP"

echo
file "$OUT"
ls -l "$OUT"
