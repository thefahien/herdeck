# Contributing

```bash
cargo build --release          # target/release/herdeck
target/release/herdeck         # demo deck, or your ~/.config/herdeck/tabs.toml
sh scripts/check.sh            # fmt, clippy, unit tests, release build, pty e2e, privacy scan
```

`scripts/check.sh` is exactly what CI runs; keep it green. `tests/e2e.py` drives the
real binary in a pseudo-terminal (keys, mouse, small sizes) and needs only python3.

## Layout

- `src/sources/` one file per tab type, each implementing `Source` (`fetch()` returns a
  `Table`). Add a type there and register it in `sources::build` and `TYPES`.
- `src/model.rs` cells (text, bar, sparkline), rows, tables.
- `src/util.rs` JSON paths, column specs and colour rules, HTTP (no redirects, TLS verified).
- `src/tab.rs` background fetch loop per tab; `src/app.rs` the terminal UI.
- `scripts/install.sh` run by `herdr plugin install`; `scripts/pane.sh` the pane entrypoint.

## Privacy

Examples and tests use `example.com`, `localhost` and made-up names only.
`scripts/privacy-scan.sh` fails on private IPs, credentials and home paths; put your own
hostnames in `.privacy-patterns` (git-ignored) to have them caught too.

## Releasing

1. Add a `## X.Y.Z` section to `CHANGELOG.md` and commit it.
2. `scripts/release.sh X.Y.Z` bumps the versions, runs the checks, tags and pushes.
3. The `release` workflow builds Linux (x86_64, arm64, static musl) and macOS (arm64,
   x86_64) binaries, writes `SHA256SUMS` and publishes the GitHub release.
4. The herdr marketplace picks up the new manifest version within 30 minutes.
