#!/bin/sh
# Run by `herdr plugin install` ([[build]] in herdr-plugin.toml), or by hand.
# Puts the herdeck binary in bin/: the release build for this version when one
# exists for this OS/CPU (checked against SHA256SUMS), otherwise `cargo build`.
set -eu
cd "$(dirname "$0")/.."
repo="thefahien/herdeck"
ver=$(sed -n 's/^version *= *"\(.*\)"/\1/p' herdr-plugin.toml | head -n1)
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) target=x86_64-unknown-linux-musl ;;
  Linux-aarch64 | Linux-arm64) target=aarch64-unknown-linux-musl ;;
  Darwin-x86_64) target=x86_64-apple-darwin ;;
  Darwin-arm64) target=aarch64-apple-darwin ;;
  *) target="" ;;
esac
mkdir -p bin

sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

if [ -z "${HERDECK_FROM_SOURCE:-}" ] && [ -n "$target" ] && command -v curl >/dev/null 2>&1; then
  asset="herdeck-v$ver-$target.tar.gz"
  base="https://github.com/$repo/releases/download/v$ver"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  if curl -fsSL "$base/$asset" -o "$tmp/$asset" && curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS"; then
    want=$(grep " $asset\$" "$tmp/SHA256SUMS" | cut -d' ' -f1)
    got=$(sha256 "$tmp/$asset")
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
      tar xzf "$tmp/$asset" -C "$tmp"
      cp "$tmp/herdeck" bin/herdeck
      chmod +x bin/herdeck
      echo "herdeck: installed release v$ver for $target"
      exit 0
    fi
    echo "herdeck: checksum mismatch for $asset, building from source" >&2
  else
    echo "herdeck: no release v$ver for $target yet, building from source" >&2
  fi
fi

if command -v cargo >/dev/null 2>&1; then
  cargo build --release --locked
  cp target/release/herdeck bin/herdeck
  echo "herdeck: built from source"
  exit 0
fi
echo "herdeck: no release binary for $(uname -s)/$(uname -m) and no cargo; install Rust (https://rustup.rs) and retry" >&2
exit 1
