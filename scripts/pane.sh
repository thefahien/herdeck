#!/usr/bin/env bash
# Plugin panes start without your login environment. Settings live in an env file
# outside the repo: ${XDG_CONFIG_HOME:-~/.config}/herdeck/env (see README).
set -euo pipefail
herdr_dir="$(dirname "${HERDR_BIN_PATH:-$(command -v herdr || echo /usr/local/bin/herdr)}")"
export PATH="$herdr_dir:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export ESCDELAY=25
conf="${XDG_CONFIG_HOME:-$HOME/.config}/herdeck/env"
if [ -f "$conf" ]; then set -a; . "$conf"; set +a; fi
cd "$(dirname "$0")/.."
exec python3 bin/herdeck "$@"
