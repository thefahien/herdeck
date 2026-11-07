#!/bin/sh
# Checks the Herdr desktop-app mod without a display: the real mod once, then shim mods that
# inject the sample documents (and run model.js selftest()) at several widths and tabs,
# then static greps. Needs `herdr-app` on PATH (or HERDR_APP=/path/to/herdr-app).
set -u
root=$(cd "$(dirname "$0")/.." && pwd)
mod=$root/app/mods/herdeck
app=${HERDR_APP:-herdr-app}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HERDR_APP_HOME=$tmp/home
mkdir -p "$HERDR_APP_HOME"
fail=0 n=0

run() {  # run <label> <folder>
  n=$((n + 1))
  if out=$("$app" check "$2" 2>&1); then echo "ok    $1"; else echo "FAIL  $1"; echo "$out" | sed 's/^/      /'; fail=$((fail + 1)); fi
}

shim() {  # shim <label> <fixture-json-file> <width> <active-index>
  d=$tmp/herdeck-shim-$((n + 1))
  mkdir -p "$d"
  cp "$mod/mod.json" "$mod/model.js" "$d/"
  cp "$mod/main.js" "$d/real.js"
  { printf 'export default '; cat "$2"; printf ';\n'; } > "$d/fixture.js"
  cat > "$d/main.js" <<EOF
import Real from "./real.js";
import F from "./fixture.js";
import { selftest } from "./model.js";
export default class Shim extends Real {
  init(p, cx) { selftest(); super.init({ fixture: F, width: $3, active: $4 }, cx); }
}
EOF
  run "$1" "$d"
}

tabs() { grep -o '"index"' "$1" | wc -l | tr -d ' '; }

# a copy: herdr-app writes jsconfig.json and .d.ts files into the folder it checks
mkdir -p "$tmp/herdeck" && cp "$mod"/main.js "$mod"/model.js "$mod"/mod.json "$tmp/herdeck/"
run "real mod (loading state)" "$tmp/herdeck"

for f in sample-demo sample-multi; do
  j=$root/docs/json/$f.json
  for w in 260 420 900; do shim "$f width $w tab 1" "$j" "$w" 1; done
  i=1; c=$(tabs "$j")
  while [ "$i" -le "$c" ]; do shim "$f width 420 tab $i" "$j" 420 "$i"; i=$((i + 1)); done
done

printf '%s' '{"schema":1,"herdeck":"0.4.0","bin":null,"error":{"kind":"no_deck","message":"no deck","path":"~/.config/herdeck/tabs.toml"}}' > "$tmp/no_deck.json"
printf '%s' '{"schema":1,"herdeck":"0.4.0","bin":null,"error":{"kind":"config","message":"bad toml\nline 3","path":"tabs.toml"}}' > "$tmp/config.json"
printf '%s' '{"schema":2,"herdeck":"9.0.0","tabs":[]}' > "$tmp/schema.json"
for f in no_deck config schema; do shim "fatal $f" "$tmp/$f.json" 420 1; done

# static greps: each must find nothing
g() {  # g <label> <pattern>
  n=$((n + 1))
  if hits=$(grep -nE "$2" "$mod"/*.js); then echo "FAIL  grep: $1"; echo "$hits" | sed 's/^/      /'; fail=$((fail + 1)); else echo "ok    grep: $1"; fi
}
g "no timers/fetch/process/paint_path" 'setTimeout|setInterval|process\.|fetch\(|paint_path'
g "no colour literals" '#[0-9a-fA-F]{3,8}'
n=$((n + 1))
if bad=$(grep -hE '^[[:space:]]*(import|export)[^"]* from "' "$mod"/*.js | grep -vE 'from "(gpui-kit|gpui-base|herdr|\./model\.js)"'); then
  echo "FAIL  grep: imports"; echo "$bad" | sed 's/^/      /'; fail=$((fail + 1))
else echo "ok    grep: imports only gpui-kit, gpui-base, herdr, ./model.js"; fi
g "no owner data" '/home/|/Users/|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:'

echo "$n checks, $fail failed"
[ "$fail" -eq 0 ]
