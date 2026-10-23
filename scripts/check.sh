#!/bin/sh
# Everything CI runs, locally: format, lint, unit tests, release build, pty e2e,
# and a scan for private data. Usage: scripts/check.sh
set -eu
cd "$(dirname "$0")/.."
echo "== fmt";    cargo fmt --check
echo "== clippy"; cargo clippy --release --all-targets --locked -- -D warnings
echo "== test";   cargo test --release --locked
echo "== build";  cargo build --release --locked
echo "== e2e";    python3 tests/e2e.py target/release/herdeck
echo "== privacy"; scripts/privacy-scan.sh
echo "all checks passed"
