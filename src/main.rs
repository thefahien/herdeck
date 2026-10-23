//! herdeck: live tabs of commands, processes, health checks, JSON APIs and
//! Prometheus queries, in a herdr pane or any terminal.

mod app;
mod config;
mod model;
mod setup;
mod sources;
mod tab;
mod util;

use model::Table;
use std::process::ExitCode;

const USAGE: &str = "herdeck: tabbed terminal dashboards for herdr

usage: herdeck [--config PATH] [--tab N|NAME] [--once] [--demo] [--types] [--version]
       herdeck init [--with ID,ID,...] [--force] [--list]

  --config PATH   deck to load (default: $HERDECK_CONFIG, else ~/.config/herdeck/tabs.toml).
                  With no deck, herdeck opens a picker to create one.
  --tab N|NAME    tab to start on
  --once          print every tab once as text and exit (also when stdout is not a TTY)
  --demo          fill tabs that aren't set up with sample data (with no deck: a demo deck)
  --types         list tab types
  --version       print the version

  init            write a starter deck without the picker (for scripts and agents):
                  --list shows the templates, --with picks them (default: processes,disk),
                  --force replaces an existing deck";

/// Plain-text output (--once, init, --types, ...) piped into `head` should just
/// stop, like any other CLI, instead of panicking on a closed pipe. Not used for
/// the interactive UI.
#[cfg(unix)]
fn quiet_on_closed_pipe() {
    extern "C" {
        fn signal(sig: i32, handler: usize) -> usize;
    }
    // SIGPIPE is 13 on Linux and macOS; handler 0 = SIG_DFL
    unsafe {
        signal(13, 0);
    }
}

#[cfg(not(unix))]
fn quiet_on_closed_pipe() {}

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1).peekable();
    if args.peek().map(|a| a == "init").unwrap_or(false) {
        args.next();
        quiet_on_closed_pipe();
        return init(args.collect());
    }
    let (mut config, mut tab, mut once, mut demo) = (None, None, false, false);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--demo" => demo = true,
            "--config" | "-c" => config = args.next(),
            "--tab" | "-t" => tab = args.next(),
            "--once" => once = true,
            "--types" => {
                quiet_on_closed_pipe();
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
    sources::set_demo(demo);
    let interactive = !once && std::io::IsTerminal::is_terminal(&std::io::stdout());
    let deck = match config::load(config.as_deref()) {
        Ok(d) => d,
        Err(e) if e.to_string() == config::NO_DECK => {
            let path = config::config_path(None);
            if demo {
                config::demo_deck()
            } else if !interactive {
                eprintln!(
                    "herdeck: no deck at {}.\nCreate one with `herdeck init` (or run herdeck in a terminal to pick tabs); `herdeck --demo` shows sample data.",
                    path.display()
                );
                return ExitCode::from(1);
            } else {
                match setup::picker(&path) {
                    Ok(setup::Choice::Written) => match config::load(None) {
                        Ok(d) => d,
                        Err(e) => {
                            eprintln!("herdeck: {e}");
                            return ExitCode::from(1);
                        }
                    },
                    Ok(setup::Choice::Demo) => {
                        sources::set_demo(true);
                        config::demo_deck()
                    }
                    Ok(setup::Choice::Quit) => {
                        println!("No deck written. Run herdeck again to pick tabs, or `herdeck init --list`.");
                        return ExitCode::SUCCESS;
                    }
                    Err(e) => {
                        eprintln!("herdeck: {e}");
                        return ExitCode::from(1);
                    }
                }
            }
        }
        Err(e) => {
            eprintln!("herdeck: {e}");
            return ExitCode::from(1);
        }
    };
    let tabs: Vec<tab::Tab> = deck.tabs.iter().map(tab::Tab::from_config).collect();
    if !interactive {
        quiet_on_closed_pipe();
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

/// `herdeck init`: write a starter deck from templates, no UI.
fn init(args: Vec<String>) -> ExitCode {
    let (mut with, mut force, mut list) = (None, false, false);
    let mut it = args.into_iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--with" => with = it.next(),
            "--force" => force = true,
            "--list" => list = true,
            "--config" | "-c" => {
                if let Some(p) = it.next() {
                    std::env::set_var("HERDECK_CONFIG", p);
                }
            }
            other => {
                eprintln!("herdeck init: unknown argument {other:?}\n\n{USAGE}");
                return ExitCode::from(2);
            }
        }
    }
    if list {
        let suggested = setup::suggested();
        println!("{:<11} {:<8} ABOUT", "TEMPLATE", "");
        for t in setup::TEMPLATES {
            let tag = if suggested.contains(&t.id) {
                "default"
            } else if t.needs.map(setup::on_path).unwrap_or(true) {
                ""
            } else {
                "missing"
            };
            println!("{:<11} {:<8} {}", t.id, tag, t.about);
        }
        println!("\ndefault = written by plain `herdeck init` · missing = its program is not installed here");
        println!("Anything else (health checks, Prometheus, JSON APIs, your own scripts): add a [[tab]] yourself, see `herdeck --types`.");
        return ExitCode::SUCCESS;
    }
    let ids: Vec<String> = match with {
        Some(w) => w.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect(),
        None => setup::suggested().iter().map(|s| s.to_string()).collect(),
    };
    let ids: Vec<&str> = ids.iter().map(|s| s.as_str()).collect();
    let path = config::config_path(None);
    match setup::write(&path, &ids, force) {
        Ok(()) => {
            println!("wrote {} with: {}", config::pretty(&path), ids.join(", "));
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("herdeck init: {e}");
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
