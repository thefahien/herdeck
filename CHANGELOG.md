# Changelog

## Unreleased

- Section titles span the whole row instead of being cut at the first column's width.
- README demo (`docs/demo.svg`, recorded by `scripts/demo-svg.py`).
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
