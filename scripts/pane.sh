#!/usr/bin/env bash
# Plugin panes start without your login environment. Tabs are read from
# ~/.config/herdeck/tabs.toml; variables they reference can live in
# ${XDG_CONFIG_HOME:-~/.config}/herdeck/env (see README).
set -euo pipefail
herdr_dir="$(dirname "${HERDR_BIN_PATH:-$(command -v herdr || echo /usr/local/bin/herdr)}")"
export PATH="$herdr_dir:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export ESCDELAY=25
conf="${XDG_CONFIG_HOME:-$HOME/.config}/herdeck/env"
if [ -f "$conf" ]; then set -a; . "$conf"; set +a; fi
cd "$(dirname "$0")/.."
# tabs.toml needs Python 3.11+ (tomllib); prefer a new enough interpreter
for py in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    exec "$py" bin/herdeck "$@"
  fi
done
exec python3 bin/herdeck "$@"
