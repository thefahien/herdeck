# herdeck

**A small framework for putting live service views into a [herdr](https://herdr.dev) pane.**
One hotkey opens a tabbed popup; every tab is declared in a TOML file. Point a tab at a
command, your processes, a set of health checks, any JSON API or a Prometheus query and it
becomes a scrollable, colour-coded table that refreshes in the background. Anything those
can't express is a small script in any language that prints JSON.

```
●1:Processes   2:Disk   3:Services   4:Containers   5:Nodes   6:GPUs   7:AI quotas   12:04:51
──────────────────────────────────────────────────────────────────────────────────────────────
 3/4 up
 SERVICE    STATE  CODE  LATENCY  TREND       TARGET
 website    UP     200   38 ms    ▃▄▃▅▃▄      https://example.com/
 api        UP     200   12 ms    ▂▂▃▂▂▂      http://localhost:8080/health
 worker     DOWN   503   120 ms   ▅▆█▇▆█      http://localhost:9000/ready
 postgres   UP     tcp   1 ms     ▁▁▁▁▁▁      localhost:5432
──────────────────────────────────────────────────────────────────────────────────────────────
 expected 200
 Tab/1-9/click:tabs · j/k/wheel:rows · r:refresh · ?:help · q:close                   [OK]
```

- One static Rust binary (ratatui). Linux and macOS, x86_64 and arm64. Nothing else to install.
- Keyboard and mouse: `Tab`/`1`-`9`/click to switch tabs, `j` `k`/wheel/click for rows.
- Fetching never blocks the UI. A source that fails after working shows its last data as
  `Stale`; a tab that isn't configured yet shows sample data marked `DEMO`.
- Runs on its own too: `herdeck` in any terminal, `herdeck --once` for plain text.

## Tab types

| type | shows | typical use |
| --- | --- | --- |
| `command` | output of any shell command as lines (with regex highlighting), a whitespace table, or JSON / JSON lines mapped to columns | logs, `df`, `docker ps`, `kubectl get pods -o json`, `systemctl list-units`, `gh run list --json` |
| `processes` | local processes with CPU / memory bars, filtered by regex or user | "is my worker still running and how hot is it" |
| `http` | HTTP checks (status, body text, latency trend) and TCP port checks | service health board |
| `json` | any HTTP API returning JSON, with dotted paths to the rows and fields | job queues, internal status endpoints, CI APIs |
| `prometheus` | PromQL queries as columns, one row per label value, optional trend sparklines | node CPU / RAM / load, request rates, queue depth |
| `exec` | any program (any language) that prints herdeck's JSON row format | anything else |
| `gpu`, `ai-quotas` | two richer built-in examples: GPU cards with lane switching, and Claude / Codex / Antigravity plan quotas through a [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) container | see [examples/tabs.toml](examples/tabs.toml) |

Columns for `json`, `command` (json / jsonl / table) and `prometheus` take the same options:
`bar = 100` (draw a meter against that maximum), `scale`, `unit`, `format = "{:.2f}"`,
`spark = true`, and colour rules `ok_values`, `warn_values`, `bad_values`,
`warn_above`, `bad_above`, `warn_below`, `bad_below`.

## Install as a herdr plugin

```bash
herdr plugin install thefahien/herdeck
```

The install step downloads the release binary for your OS and CPU and checks it against
the release's `SHA256SUMS`; with no matching release it builds from source with `cargo`.
Updates: run the same command again (add `--ref vX.Y.Z` to pin a version).

Bind one key in `~/.config/herdr/config.toml` on the machine you attach from, then run
`herdr config check && herdr server reload-config`:

```toml
[[keys.command]]
key = "alt+v"
type = "plugin_action"
command = "herdeck.open"
description = "herdeck"
```

`herdeck.split` opens the same deck in a persistent split pane.

## Configure

With no config you get a demo deck: your processes and disks (real), plus sample Services,
GPUs and AI quotas tabs. To build your own:

```bash
mkdir -p ~/.config/herdeck
cp ~/src/herdeck/examples/tabs.toml ~/.config/herdeck/tabs.toml
```

A tab is a `[[tab]]` table with `name`, `type`, an optional `interval` (seconds) and the
type's own keys:

```toml
[[tab]]
name = "Containers"
type = "command"
command = "docker ps -a --format '{{json .}}'"
format = "jsonl"
interval = 10
[[tab.column]]
title = "Name"
path = "Names"
[[tab.column]]
title = "State"
path = "State"
ok_values = ["running"]
bad_values = ["exited", "dead"]

[[tab]]
name = "Nodes"
type = "prometheus"
url = "${PROMETHEUS_URL:-http://localhost:9090}"
label = "instance"
[[tab.metric]]
title = "CPU"
query = '100 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100'
bar = 100
[[tab.metric]]
title = "Load"
query = "node_load1"
format = "{:.2f}"
bad_above = 8
```

[examples/tabs.toml](examples/tabs.toml) has one of every type. Config lookup:
`--config PATH`, then `$HERDECK_CONFIG`, then `~/.config/herdeck/tabs.toml`.

**Trust:** a deck runs shell commands (`command` tabs) and programs (`exec` tabs).
Treat `tabs.toml` like a shell script and don't load decks you haven't read.

**Secrets:** any string may use `${NAME}` or `${NAME:-default}`. In `command` strings the
shell expands variables itself (values are never pasted into the command line). Plugin panes
don't inherit your shell, so put variables in `~/.config/herdeck/env`; the pane script
sources it with `sh` (`NAME=value` lines, quote values that contain spaces or `$`). Tokens are only sent where the tab sends them, redirects are never
followed, and TLS is always verified (use plain http for a local endpoint with a
self-signed certificate).

### The two richer examples

**`gpu`** reads `GET <url>/api/state` and expects:

```json
{"gpus": [{
  "id": "gpu0", "label": "GPU 0: RTX 4090", "active": "render",
  "card": {"util": 45, "vram_used_mb": 12288, "vram_total_mb": 24576, "temp_c": 58, "power_w": 240},
  "util_hist": [10, 20, 45],
  "procs": [{"pid": "1042", "name": "python worker.py", "mem": "11.8 GiB"}],
  "lanes": [{"id": "render", "label": "Render"}, {"id": "llm", "label": "LLM"}]
}]}
```

`lanes` are optional. Switching (after a y/N prompt) sends
`POST <url>/api/switch {"gpu": id, "lane": id, "force": bool}` with
`Authorization: Bearer <token>`, only over https or to localhost.

**`ai-quotas`** shows plan usage for the Claude, Codex and Antigravity accounts logged into
a [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) container. Over SSH it reads
the first `claude-*`, `codex-*` and `antigravity-*` auth file in the container
(`auth_dir`, default `/root/.cli-proxy-api`) and calls each provider's usage endpoint with
that token; it also counts requests per model from the container log. Tokens are never
written to disk or shown. Those usage endpoints are undocumented, may change without notice,
and using them may fall under each provider's terms; it's an example of what a custom view
can do, not a supported API client.

## Write your own tab

An `exec` tab runs any program and renders the JSON it prints. Use any language:

```toml
[[tab]]
name = "Queue"
type = "exec"
command = "python3 ~/.config/herdeck/queue.py"   # or a list, run without a shell
interval = 5
```

The program prints one JSON object:

```json
{
  "header": "3 jobs",
  "columns": ["JOB", "PROGRESS", "STATE"],
  "rows": [
    ["encode", {"bar": 50, "max": 100}, {"text": "running", "tone": "accent"}],
    {"section": "finished"},
    ["upload", {"bar": 100}, {"text": "failed", "tone": "bad"}]
  ],
  "details": ["shown under the table while row 1 is selected"]
}
```

A cell is a string or number, `{"text": .., "tone": "ok|warn|bad|accent|dim"}`,
`{"bar": value, "max": 100, "text": ".."}` or `{"spark": [1, 2, 3]}`. A row is a list of
cells, an object keyed by column title, or `{"section": "Title"}`. Exit non-zero with a
message on stderr to report an error; the tab keeps showing the last good output as
`Stale`. [examples/weather.py](examples/weather.py) is a complete example.

If your program already prints plain JSON, use a `command` tab with `format = "json"`
and `[[tab.column]]` mappings instead. To add a built-in tab type, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Keys

| key | action |
| --- | --- |
| `Tab` `]` / `Shift+Tab` `[` | next / previous tab |
| `1`-`9`, click | jump to a tab |
| `j` `k`, arrows, wheel, click | move / select a row |
| `PgUp` `PgDn` `g` `G` | page, top, bottom |
| `r` | refresh the tab now |
| `?` | help |
| `q` / `Esc` | close |

GPU tab only: `s` opens a lane-switch prompt for the selected card (`s` or arrows pick
the lane, `y` confirms); the line under the table shows its lanes and processes.

## CLI

The plugin keeps its binary in `bin/herdeck` inside the plugin directory; you can also
`cargo install --git https://github.com/thefahien/herdeck` and run it in any terminal.

```bash
herdeck                      # interactive
herdeck --once               # every tab once, as text (also when stdout isn't a TTY)
herdeck --tab Services       # start on a tab (number or name)
herdeck --config deck.toml   # another deck
herdeck --types              # list tab types
```

## Develop

```bash
cargo build --release && target/release/herdeck
sh scripts/check.sh          # what CI runs: fmt, clippy, tests, build, pty e2e, privacy scan
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the layout and the release process.

## License

MIT
