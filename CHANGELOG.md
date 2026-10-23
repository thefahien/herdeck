# Changelog

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
