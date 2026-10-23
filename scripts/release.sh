#!/bin/sh
# Cut a release: scripts/release.sh 0.3.0
# Bumps Cargo.toml, Cargo.lock and herdr-plugin.toml, checks CHANGELOG.md has a
# section for the version, runs scripts/check.sh when cargo is available, commits,
# tags vX.Y.Z and pushes. The release workflow then builds the binaries and publishes them.
set -eu
cd "$(dirname "$0")/.."
v="${1:?usage: scripts/release.sh X.Y.Z}"
echo "$v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || { echo "version must look like 1.2.3" >&2; exit 2; }
[ -z "$(git status --porcelain)" ] || { echo "working tree is not clean" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "release from main" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/v$v" >/dev/null && { echo "tag v$v exists" >&2; exit 1; }
grep -Eq "^## \[?$v\]?" CHANGELOG.md || { echo "add a '## $v' section to CHANGELOG.md first" >&2; exit 1; }

bump() {  # first `version = "..."` line of a file
  awk -v v="$v" '!done && /^version = "/ { sub(/"[^"]*"/, "\"" v "\""); done=1 } { print }' "$1" > "$1.tmp" && mv "$1.tmp" "$1"
}
bump Cargo.toml
bump herdr-plugin.toml
# the herdeck entry in Cargo.lock: the version line right after name = "herdeck"
awk -v v="$v" 'prev ~ /^name = "herdeck"$/ && /^version = / { $0 = "version = \"" v "\"" } { print; prev = $0 }' Cargo.lock > Cargo.lock.tmp && mv Cargo.lock.tmp Cargo.lock
if command -v cargo >/dev/null 2>&1 && [ -z "${SKIP_CHECK:-}" ]; then
  sh scripts/check.sh
else
  echo "cargo not here (or SKIP_CHECK set): skipping local checks; the release workflow runs them before building"
fi
git add Cargo.toml Cargo.lock herdr-plugin.toml CHANGELOG.md
git commit -m "release v$v"
git tag -a "v$v" -m "herdeck v$v"
git push origin main "v$v"
echo "pushed v$v; the release workflow builds and publishes the binaries"
