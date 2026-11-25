#!/bin/sh
# Rust as .github/rust.txt pins it: each file downloaded from static.rust-lang.org and checked against its SHA-256
# there before anything of it is unpacked, then installed by its own install.sh into DIR (nothing outside it, no
# shell profile touched, no rustup).  Prints DIR/bin, which goes first on PATH: cargo and rustc from there build the
# Rust program exactly as rust.txt names them, in CI (build.yml's Rust step) and, for a plugin released in Rust, in
# the release job, whose program has to be the build job's byte for byte (README.md "Rust").  Not the runner's rustup,
# which installs rust/rust-toolchain.toml's version from a manifest it fetches and checks against nothing pinned here.
#
#   tools/rustpin.sh DIR
#
# RUSTPIN_BASE: where to download from instead of https://static.rust-lang.org/dist/ (the tests, a file:// address):
# the SHA-256s hold all the same.
set -eu
[ $# = 1 ] || { echo "usage: tools/rustpin.sh DIR" >&2; exit 2; }
cd "$(dirname "$0")/.."
dir=$1
mkdir -p "$dir/dl"
dir=$(cd "$dir" && pwd)
grep -v '^#' .github/rust.txt | grep . | while read -r sha url; do
    case $url in https://static.rust-lang.org/dist/*.tar.xz) ;; *) echo "::error::.github/rust.txt: $url is not one of static.rust-lang.org's" >&2; exit 1 ;; esac
    f=$dir/dl/${url##*/}
    from=$url
    [ -z "${RUSTPIN_BASE:-}" ] || from=$RUSTPIN_BASE${url#https://static.rust-lang.org/dist/}
    curl -sSfL --proto '=https,file' -o "$f" "$from"
    # (sha256sum -c: a file that is not the one named ends it here, before anything of it is unpacked)
    echo "$sha  $f" | sha256sum -c --quiet - >&2 ||
        { echo "::error::$url is not the file .github/rust.txt names (its SHA-256 is not $sha)" >&2; rm -f "$f"; exit 1; }
    tar -xJf "$f" -C "$dir/dl"
    name=${f##*/}
    sh "$dir/dl/${name%.tar.xz}/install.sh" --prefix="$dir" --disable-ldconfig >/dev/null
    rm -rf "$f" "$dir/dl/${name%.tar.xz}"
done
"$dir/bin/rustc" --version >&2
echo "$dir/bin"
