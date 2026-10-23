#!/usr/bin/env bash
# herdr runs plugin actions without your login PATH, so use the binary it injects.
set -euo pipefail
herdr_bin="${HERDR_BIN_PATH:-herdr}"
case "${1:-popup}" in
  popup) exec "$herdr_bin" plugin pane open --plugin herdeck --entrypoint popup --placement popup --width 105 --height 24 --focus ;;
  split) exec "$herdr_bin" plugin pane open --plugin herdeck --entrypoint split --placement split --focus ;;
  *) echo "usage: open-pane.sh popup|split" >&2; exit 2 ;;
esac
