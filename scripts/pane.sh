#!/bin/sh
# herdr pane entrypoint. Plugin panes start without your login environment:
# tabs come from ~/.config/herdeck/tabs.toml, and variables they reference can
# live in ${XDG_CONFIG_HOME:-~/.config}/herdeck/env (sourced here with sh).
herdr_dir="$(dirname "${HERDR_BIN_PATH:-/usr/local/bin/herdr}")"
PATH="$herdr_dir:$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH
conf="${XDG_CONFIG_HOME:-$HOME/.config}/herdeck/env"
if [ -f "$conf" ]; then set -a; . "$conf"; set +a; fi
cd "$(dirname "$0")/.." || exit 1
for b in bin/herdeck target/release/herdeck; do
  [ -x "$b" ] && exec "$b" "$@"
done
echo "herdeck: binary not found. Run scripts/install.sh (or cargo build --release) in $(pwd)."
printf '(Enter closes) '
read -r _
