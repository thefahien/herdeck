#!/bin/sh
# Fail if tracked files contain things that should never be published: private
# network addresses, credentials, home paths. Add personal patterns (hostnames,
# account names) in .privacy-patterns, which is never committed.
set -eu
cd "$(dirname "$0")/.."
pat='(^|[^0-9.])(10\.[0-9]+\.[0-9]+\.[0-9]+|192\.168\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+)([^0-9]|$)|BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20}|github_pat_|sk-ant-|sk-proj-|ya29\.|xox[bp]-|AKIA[0-9A-Z]{16}|/home/[a-z]|/Users/[A-Za-z]|\.internal\b'
if [ -f .privacy-patterns ]; then
  extra=$(grep -v '^#' .privacy-patterns | grep -v '^$' | paste -sd'|' -)
  [ -n "$extra" ] && pat="$pat|$extra"
fi
files=$(git ls-files 2>/dev/null || find . -type f -not -path './target/*' -not -path './.git/*')
hits=$(printf '%s\n' "$files" | grep -vE '(scripts/privacy-scan.sh|\.privacy-patterns)$' | xargs grep -nIE "$pat" 2>/dev/null || true)
# compiled files embed build paths (home directories, user names): never commit them
bins=$(printf '%s\n' "$files" | while read -r f; do
  [ -f "$f" ] || continue
  case "$(head -c 4 "$f" | od -An -c | tr -d ' ')" in
    177ELF*|317372376355*|317372355376*|376355372317*|312376272276*) echo "$f: compiled binary" ;;
  esac
done)
[ -n "$bins" ] && hits=$(printf '%s\n%s' "$hits" "$bins" | sed '/^$/d')
if [ -n "$hits" ]; then
  echo "$hits"
  echo "privacy scan: the lines above look private; fix them or rephrase" >&2
  exit 1
fi
echo "privacy scan: clean"
