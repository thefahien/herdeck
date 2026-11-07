# `herdeck --json`

```
herdeck --json [--tab N|NAME] [--config PATH] [--demo] [--timeout SECS]
```

Fetches every tab once, prints one JSON document on stdout and exits. It is meant for
programs that draw the deck themselves (a desktop panel, a status bar, a script). The
interactive screen and `--once` are unchanged. Samples: [`json/sample-demo.json`](json/sample-demo.json)
(the demo deck) and [`json/sample-multi.json`](json/sample-multi.json) (every state, cell kind and tone).

## Command line

- `--json` wins over `--once`. It never opens the picker or the interactive screen, TTY or not.
- No `--tab`: every tab is fetched. `--tab N|NAME` (N is 1-based, NAME ignores case) fetches only
  that tab; all others are listed with `"state": "skipped"` and `"table": null`. An unknown tab
  is an error document (`no_such_tab`), unlike the interactive screen, which falls back to tab 1.
- Tabs are fetched in parallel. `--timeout SECS` (default 15, clamped to 1 to 120) is the deadline
  for all of them. A tab still running at the deadline gets `"state": "timeout"`, the document is
  printed and herdeck exits, so the call takes at most the timeout plus a moment. Programs a
  timed-out tab started (a `command` tab's shell, for example) are not killed by herdeck.
- With no deck, `--demo` gives the demo deck (as it does everywhere); without `--demo` the result
  is the `no_deck` error.
- Each call is a new process, so nothing is remembered between calls. The `http` tab's latency
  trend, which the interactive screen builds up over time, therefore has at most one point.

Exit codes: `0` when the document has `tabs` (per-tab errors are in the tabs, not in the exit
code), `1` for an error document, `2` for an unknown or malformed argument (text on stderr, no
JSON). A herdeck older than the one that added `--json` exits 2 with `unknown argument "--json"`.

## Document

Keys appear in the order shown and are always present unless marked "omitted when absent".

```json
{
  "schema": 1,
  "herdeck": "0.4.0",
  "bin": "/usr/local/bin/herdeck",
  "deck": "~/.config/herdeck/tabs.toml",
  "demo": false,
  "tabs": []
}
```

| key | type | meaning |
| --- | --- | --- |
| `schema` | int | Data format version. Bumped only on a breaking change; added fields do not bump it. |
| `herdeck` | string | Version of the herdeck that printed this. |
| `bin` | string or null | Path of that herdeck executable. |
| `deck` | string | The deck file (home directory shown as `~`), or `demo deck (sample data)`. |
| `demo` | bool | `--demo` was given. |
| `tabs` | array | One entry per tab, in deck order. Never empty. |

### Tab

| key | type | meaning |
| --- | --- | --- |
| `index` | int | Position in the deck, starting at 1. What `--tab N` takes. |
| `name` | string | The tab's name. |
| `type` | string | The `type` from the deck (`""` if missing). |
| `interval` | number | Refresh interval in seconds that the interactive screen uses for this tab. |
| `state` | string | `ok`, `demo`, `not_set_up`, `error`, `timeout` or `skipped` (see below). |
| `status` | string | The text of the interactive screen's footer: `OK`, a note such as `partial: 1 runner offline`, `DEMO (why)`, `not set up`, `error: message`, `timeout`, `skipped`. |
| `message` | string or null | The reason, without prefix: the error text, the demo reason, the setup instructions' heading, `no answer within 15s`. `null` for `ok` and `skipped`. |
| `table` | table or null | `null` for `error`, `timeout` and `skipped`; present otherwise. |

States:

- `ok`: the tab answered. A note from the tab (`partial: ...`) is in `status`, the state stays `ok`.
- `demo`: the tab is not set up (or its source is unreachable) and `--demo` replaced it with sample data.
- `not_set_up`: the tab lacks required settings. `table` lists what to add.
- `error`: fetching failed. No table: herdeck keeps no earlier data between calls.
- `timeout`: no answer before `--timeout`.
- `skipped`: not fetched because of `--tab`.

### Table

```json
{ "header": "2/3 up", "columns": ["SERVICE", "STATE"], "rows": [] }
```

`header` is a string or null. `columns` are the column titles. `rows` hold rows of two kinds.

### Row

- Data row: `{ "cells": [Cell, ...], "detail": "...", "key": "..." }`. `detail` (the line the
  interactive screen shows under the table for the selected row) and `key` (an id the tab uses
  internally) are omitted when absent.
- A data row with a `key` can also have `"actions"`: what a person may do to that row, offered by the
  tab (the `gpu` tab lists `s`, switch lane). Each action is
  `{ "key": "s", "title": "Switch lane: GPU 0", "warning": "2 process(es) may be stopped" | null,
  "options": [ { "id": "llm", "label": "LLM" }, ... ] }`: show the title and the options, then run the chosen
  one with `herdeck --act` (below). Tabs without actions never have the field.
- Section row: `{ "section": "Title" }`, a heading that spans the row. It has no cells.

A row can have fewer cells than there are columns.

### Cell

Every cell has a `kind`. Numbers are JSON numbers, never drawn characters.

| kind | fields |
| --- | --- |
| `text` | `text` (string), `tone` (tone or null) |
| `bar` | `value` and `max` (numbers), `text` (the label, such as `41%` or `15.8/24G`), `tone`, `width` (int) |
| `spark` | `values` (numbers, may be empty), `max` (number or null), `tone` (always `accent`), `width` (int) |

- A `bar` fills `value / max`. Its `tone` follows the fill like the interactive screen: `bad` from
  90%, `warn` from 70%, otherwise `ok`; `ok` when `max` is 0 or less. `width` is how many
  characters the terminal uses, a hint for sizing.
- A `spark` holds only the newest points the terminal would draw (at most `width`). With
  `max: null`, scale to the largest value; otherwise scale to `max`.
- `tone` is one of `ok`, `warn`, `bad`, `accent`, `dim`. A text cell without a tone is `null`.

## Error document (exit 1)

```json
{
  "schema": 1,
  "herdeck": "0.4.0",
  "bin": "/usr/local/bin/herdeck",
  "error": { "kind": "no_deck", "message": "no deck at ~/.config/herdeck/tabs.toml", "path": "~/.config/herdeck/tabs.toml" }
}
```

There is no `tabs` key. `error.kind` is one of:

| kind | when | `path` |
| --- | --- | --- |
| `no_deck` | the deck file does not exist (and `--demo` was not given) | where herdeck looked |
| `config` | the deck cannot be read or parsed, or has no `[[tab]]` | the deck file |
| `no_such_tab` | `--tab` matches no tab | `null` |

## Reading it safely

Check `schema` first and refuse versions you do not know. Treat unknown keys and unknown `kind`
or `tone` values as ignorable, so that fields added later do not break you. `tests/json_contract.py`
in the repository is a reference validator for everything above.


## Doing an action: `herdeck --act`

`herdeck [--config PATH] [--demo] --act TAB ROW KEY CHOICE` fetches the tab (`TAB` is a number or name),
finds the row whose `key` is `ROW`, presses `KEY` on it and confirms the option whose `id` is `CHOICE`.
It prints one line, `{ "schema": 1, "herdeck": "...", "bin": "...", "ok": true, "message": "..." }`, and exits 1
with `"ok": false` and the reason when it fails (unknown tab or row, a key the tab does not offer, a choice that
is not offered, or the tab's own error). A problem with the deck file itself (missing, invalid TOML) prints the
same `error` document as `--json` instead, also with exit 1: read `error.message` when `ok` is missing.
With `--demo` an action on sample data only changes the sample.
A GPU lane switch is accepted by the GPU service and finishes in the background: look at the tab again a few
seconds later.
