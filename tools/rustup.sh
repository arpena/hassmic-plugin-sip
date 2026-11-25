#!/bin/sh
# rustup into toolchain/ of this tree (git-ignored; nothing outside it, no shell profile touched): its installer from
# rustup's own site, held to the SHA-256 the site publishes beside it; then the toolchain and the two targets that
# rust/rust-toolchain.toml names.  CI's runners have rustup already and skip this.  Rust is optional: README.md "Rust".
set -e
cd "$(dirname "$0")/.."
url=https://static.rust-lang.org/rustup/dist/x86_64-unknown-linux-gnu/rustup-init
mkdir -p toolchain/dl
curl -sSf -o toolchain/dl/rustup-init "$url"
curl -sSf -o toolchain/dl/rustup-init.sha256 "$url.sha256"
want=$(cut -d' ' -f1 toolchain/dl/rustup-init.sha256)
have=$(sha256sum toolchain/dl/rustup-init | cut -d' ' -f1)
[ -n "$want" ] && [ "$want" = "$have" ] || { echo "rustup-init: SHA-256 $have, the site says $want" >&2; exit 1; }
chmod +x toolchain/dl/rustup-init
export RUSTUP_HOME="$PWD/toolchain/rustup" CARGO_HOME="$PWD/toolchain/cargo"
toolchain/dl/rustup-init -y -q --no-modify-path --profile minimal --default-toolchain none
cd rust && "$CARGO_HOME/bin/rustup" toolchain install && "$CARGO_HOME/bin/rustup" show active-toolchain
