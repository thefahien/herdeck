//! herdeck: live tabs of commands, processes, health checks, JSON APIs and
//! Prometheus queries, in a herdr pane or any terminal.

mod app;
mod config;
mod model;
mod sources;
mod tab;
mod util;

use model::Table;
use std::process::ExitCode;

const USAGE: &str = "herdeck: tabbed terminal dashboards for herdr

usage: herdeck [--config PATH] [--tab N|NAME] [--once] [--types] [--version]

  --config PATH   deck to load (default: $HERDECK_CONFIG, ~/.config/herdeck/tabs.toml,
                  else a built-in demo deck)
  --tab N|NAME    tab to start on
  --once          print every tab once as text and exit (also when stdout is not a TTY)
  --types         list tab types
  --version       print the version";

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let (mut config, mut tab, mut once) = (None, None, false);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--config" | "-c" => config = args.next(),
            "--tab" | "-t" => tab = args.next(),
            "--once" => once = true,
            "--types" => {
                for (n, d) in sources::TYPES {
                    println!("{n:<11} {d}");
                }
                return ExitCode::SUCCESS;
            }
            "--version" | "-V" => {
                println!("herdeck {}", env!("CARGO_PKG_VERSION"));
                return ExitCode::SUCCESS;
            }
            "--help" | "-h" => {
                println!("{USAGE}");
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("herdeck: unknown argument {other:?}\n\n{USAGE}");
                return ExitCode::from(2);
            }
        }
    }
    let deck = match config::load(config.as_deref()) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("herdeck: {e}");
            return ExitCode::from(1);
        }
    };
    let tabs: Vec<tab::Tab> = deck.tabs.iter().map(tab::Tab::from_config).collect();
    if once || !std::io::IsTerminal::is_terminal(&std::io::stdout()) {
        println!("# herdeck {}: {}", env!("CARGO_PKG_VERSION"), deck.source);
        for t in &tabs {
            tab::Tab::refresh(&t.source, &t.state);
            let st = t.state.lock().unwrap();
            println!("\n## {}{}", t.name, if st.demo { "  [DEMO]" } else { "" });
            if st.status != "OK" && !st.demo {
                println!("  ({})", st.status);
            }
            print!("{}", render_text(&st.table));
        }
        return ExitCode::SUCCESS;
    }
    let start = match tab.as_deref() {
        None => 0,
        Some(s) => s
            .parse::<usize>()
            .ok()
            .map(|n| n.saturating_sub(1))
            .or_else(|| tabs.iter().position(|t| t.name.eq_ignore_ascii_case(s)))
            .unwrap_or(0)
            .min(tabs.len().saturating_sub(1)),
    };
    match app::App::new(tabs, start, deck.refresh).run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("herdeck: {e}");
            ExitCode::from(1)
        }
    }
}

/// The table as plain text, for --once.
pub fn render_text(t: &Table) -> String {
    let mut out = String::new();
    if let Some(h) = &t.header {
        out.push_str(&format!("  {h}\n"));
    }
    if t.rows.is_empty() {
        return out;
    }
    let w = app::col_widths(&t.columns, &t.rows, 160);
    let fmt = |cells: Vec<String>| -> String {
        let s: Vec<String> = cells
            .iter()
            .zip(&w)
            .map(|(c, w)| format!("{:<width$}", c.chars().take(*w as usize).collect::<String>(), width = *w as usize))
            .collect();
        format!("  {}\n", s.join("  ").trim_end())
    };
    out.push_str(&fmt(t.columns.clone()));
    for r in &t.rows {
        match &r.section {
            Some(s) => out.push_str(&format!("  -- {s}\n")),
            None => out.push_str(&fmt(r.cells.iter().map(|c| c.render()).collect())),
        }
    }
    out
}
