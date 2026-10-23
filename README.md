# herdeck

Tabbed terminal dashboards for [herdr](https://herdr.dev). One hotkey opens a popup;
switch tabs with `Tab`, the number keys, or a mouse click.

| Tab | Shows | Source |
| --- | --- | --- |
| 1 GPUs | utilization, VRAM, temperature, power, 10-min sparkline, processes, optional lane switching | any HTTP endpoint returning the JSON below |
| 2 Fleet CPU | CPU %, load 1/5/15, RAM, cores, 10-min sparkline | Prometheus + node_exporter |
| 3 CLI Proxy | plan quotas for Claude, Codex and Antigravity accounts, 24 h requests per model | a [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) container reached over SSH |

Every tab falls back to synthetic **demo data** (clearly marked `DEMO`) when its source is not
configured, so it runs out of the box. Once a source has answered, a later failure shows the last
real data as `Stale (Ns)` instead of demo data.

Pure Python standard library (curses, urllib). Python 3.9+.

## Install as a herdr plugin

```bash
git clone https://github.com/thefahien/herdeck ~/src/herdeck
herdr plugin link ~/src/herdeck
```

Bind one key in `~/.config/herdr/config.toml` (on the machine you attach from), then
`herdr config check && herdr server reload-config`:

```toml
[[keys.command]]
key = "alt+v"
type = "plugin_action"
command = "herdeck.open"
description = "herdeck: dashboards"
```

`herdeck.split` opens the same dashboards in a persistent split pane instead of a popup.

## Configure

Plugin panes do not inherit your shell environment. Put settings in
`~/.config/herdeck/env` (outside the repo; it is sourced by `scripts/pane.sh`):

```sh
# Tab 1: GPU endpoint (GET <url>/api/state); token only needed for lane switching
HERDECK_GPU_URL=http://localhost:8000
HERDECK_GPU_TOKEN=

# Tab 2: Prometheus; node names are matched against the `job` label
HERDECK_PROMETHEUS_URL=http://localhost:9090
HERDECK_NODES=worker-1,worker-2

# Tab 3: SSH target of the host running the CLIProxyAPI container
HERDECK_CLIPROXY_SSH=user@proxy-host
HERDECK_CLIPROXY_CONTAINER=cli-proxy-api
HERDECK_CLIPROXY_AUTH_DIR=/root/.cli-proxy-api

# Set to 1 only for self-signed certs on your own GPU/Prometheus endpoints.
# Provider quota calls (Anthropic, OpenAI, Google) always verify TLS.
HERDECK_INSECURE_TLS=0
```

Tab 3 reads the first `claude-*.json`, `codex-*.json` and `antigravity-*.json` auth file inside
the container and calls each provider's usage endpoint with that token. The tokens are never
written to disk or shown. These usage endpoints are undocumented and may change.

### GPU endpoint format

`GET <HERDECK_GPU_URL>/api/state` returns:

```json
{"gpus": [{
  "id": "gpu0", "label": "GPU 0: RTX 4090", "active": "render",
  "card": {"util": 45, "vram_used_mb": 12288, "vram_total_mb": 24576, "temp_c": 58, "power_w": 240},
  "util_hist": [10, 20, 45],
  "procs": [{"pid": "1042", "name": "python worker.py", "mem": "11.8 GiB"}],
  "lanes": [{"id": "render", "label": "Render"}, {"id": "llm", "label": "LLM"}]
}]}
```

`lanes` and lane switching are optional. The token is only sent over https (or to localhost), and redirects are never followed. Switching sends
`POST <url>/api/switch {"gpu": id, "lane": id, "force": bool}` with
`Authorization: Bearer $HERDECK_GPU_TOKEN`, always after a y/N confirmation.

## Keys

| Key | Action |
| --- | --- |
| `Tab` / `]`, `Shift+Tab` / `[` | next / previous tab |
| `1`-`9` | jump to tab |
| click a tab / row | switch tab / select row |
| `j` `k`, arrows, mouse wheel | move selection |
| `s` | lane switch prompt for the selected GPU (`s` again cycles lanes, `y` confirms) |
| `Enter` | toggle process list (GPU tab) |
| `r` | refresh now |
| `?` | help |
| `q` / `Esc` | close |

## CLI

```bash
bin/herdeck            # interactive
bin/herdeck --once     # print every tab once (also used when stdout is not a TTY)
bin/herdeck --tab 3    # start on a tab
```

## Add a dashboard

Create `herdeck/views/<name>.py` with a `BaseView` subclass implementing `update()`, `draw()` and
`handle_key()` (optionally `handle_mouse_click()` and `print_once()`), then add it to the view list
in `herdeck/cli.py`. Keep fetches off the UI thread or under ~2 s.

## Test

```bash
python3 -m unittest discover -s tests
```

## License

MIT
