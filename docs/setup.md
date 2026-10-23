# Setting up herdeck (for people and AI agents)

herdeck reads one file, `~/.config/herdeck/tabs.toml` (or `$HERDECK_CONFIG`). Each `[[tab]]`
is one tab. Values written as `${NAME}` come from the environment; inside herdr, plugin panes
also load `~/.config/herdeck/env` (`NAME=value` lines), which is the place for tokens and
URLs you don't want in the deck.

## Steps

1. **Start small.** `herdeck init --list` shows the ready-to-use starter tabs
   (`missing` = its program isn't installed). Ask the user what they want to watch, then
   `herdeck init --with processes,containers` (plain `herdeck init` = Processes + Disk).
   It refuses to replace an existing deck unless you pass `--force`; when a deck exists,
   edit it instead.
2. **Add the rest by hand**, one `[[tab]]` per thing the user asked for. `herdeck --types`
   lists the types; `examples/tabs.toml` in the repo has a commented example of each.
3. **Fill in settings** for those tabs. Ask the user for the real endpoints;
   don't guess hosts or invent URLs.
   - `http`: one `[[tab.check]]` per endpoint with `name` and `url` (or `tcp = "host:port"`).
   - `prometheus`: `url`, plus `[[tab.metric]]` entries with `title` and `query`.
   - `json`: `url`, `items` (dotted path to the list), `[[tab.column]]` `title`/`path`.
   - `gpu`, `ai-quotas`: see the README sections for their endpoints.
   - Anything else: a `command` tab (shell command, `format = "json" | "jsonl" | "table"`)
     or an `exec` tab running a script that prints herdeck's JSON row format.
   Secrets go in `~/.config/herdeck/env` and are referenced as `${NAME}` in the deck.
4. **Check it.** `herdeck --once` prints every tab as text. A tab that still needs settings
   prints `Not set up yet` and what is missing; a failing source prints `error: ...`.
   Repeat until every tab shows real rows.
5. **Open it** in herdr (the user's `herdeck.open` key) or with `herdeck` in a terminal.

## Rules for agents

- Never put made-up rows into a deck. `--demo` is only for looking around.
- Don't remove tabs the user added. Keep their comments when editing.
- Every tab type and option: `herdeck --types` and the README's "Tab types" and "Configure".
