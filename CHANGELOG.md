# Changelog

## 0.4.0

- Lane switch dialog: each option gets its own line instead of running off the dialog or hiding the other options. A long lane name is cut with an ellipsis in the terminal and wraps onto a second line in the desktop app. The app dialog also takes ↑/↓ to pick, ignores other keys while open, and remembers the machine it was opened for.
- `herdeck --act` only accepts keys the tab offers and says why a row is missing when the tab is unreachable.
- `herdeck --json`: every tab fetched once and printed as one JSON document (tab states,
  tables with typed cells, `--tab`, `--timeout`), for programs that draw the deck
  themselves. Format and samples: `docs/json.md`, `docs/json/`.
- Native mod for the Herdr desktop app in `app/`: the deck as a live panel, pane, tab or overlay
  (`ctrl-shift-h`). Needs `herdeck --json`. See the README and `app/RECIPE.md`.
- Row actions in `--json` (`actions` on a row; the `gpu` tab offers its lane switch) and
  `herdeck --act TAB ROW KEY CHOICE` to run one, so the desktop app panel can switch a GPU lane.

## 0.3.0

- First run: with no deck, herdeck asks which starter tabs you want (only ones that work
  on this machine; Processes and Disk ticked) and writes `~/.config/herdeck/tabs.toml`.
  Other tabs are added by hand. `herdeck init` does the same
  without a UI, for scripts and agents (`--list`, `--with`, `--force`).
- No made-up data by default: a tab that isn't set up says what to add, and an unreachable
  GPU endpoint is an error. Sample data only with `--demo`.
- `docs/setup.md`: step-by-step setup, written so an AI agent can follow it.
- Section titles span the whole row instead of being cut at the first column's width.
- README demo (`docs/demo.svg`, recorded by `scripts/demo-svg.py`).
- CI and release: macOS jobs run on Intel Mac runners (the Apple-silicon binary is
  cross-compiled there).
- Piping plain-text output (`--once`, `init --list`, `--types`) into `head` no longer panics.
- Release binaries no longer contain the build machine's paths.

## 0.2.0

- Rewritten in Rust: one static binary (ratatui), no Python needed. `herdr plugin install`
  downloads the release binary for your OS/CPU (sha256-checked) or builds with cargo.
- Same `tabs.toml` format and tab types: command, processes, http, json, prometheus,
  gpu, ai-quotas.
- New `exec` tab type: any program that prints herdeck's JSON row format. It replaces the
  `python` tab type.
- Section rows, per-row detail lines, and tab-specific prompts (GPU lane switching).

## 0.1.0

- First release (Python prototype).
