# Changelog

## Unreleased

- First run: with no deck, herdeck asks which starter tabs you want (only ones that work
  on this machine; Processes and Disk ticked) and writes `~/.config/herdeck/tabs.toml`.
  Other tabs are added by hand. `herdeck init` does the same
  without a UI, for scripts and agents (`--list`, `--with`, `--force`).
- No made-up data by default: a tab that isn't set up says what to add, and an unreachable
  GPU endpoint is an error. Sample data only with `--demo`.
- `docs/setup.md`: step-by-step setup, written so an AI agent can follow it.
- Section titles span the whole row instead of being cut at the first column's width.
- README demo (`docs/demo.svg`, recorded by `scripts/demo-svg.py`).
- CI: macOS tests run on Intel Mac runners.
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
