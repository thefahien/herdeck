//! First run: pick a few ready-to-use tabs and write tabs.toml (interactive picker, or
//! `herdeck init` for scripts and agents). Nothing shows sample data unless
//! asked for with --demo.

use anyhow::{bail, Context, Result};
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind};
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use std::path::Path;

pub struct Template {
    pub id: &'static str,
    pub title: &'static str,
    pub about: &'static str,
    /// Program that must be on PATH for the template to be useful (preselection).
    pub needs: Option<&'static str>,
    pub toml: &'static str,
}

pub const TEMPLATES: &[Template] = &[
    Template {
        id: "processes",
        title: "Processes",
        about: "top processes by CPU, with CPU and memory bars",
        needs: Some("ps"),
        toml: r#"[[tab]]
name = "Processes"
type = "processes"
sort = "cpu"         # cpu | mem | pid
limit = 30
# match = "python|node|postgres"   # regex on the full command line
"#,
    },
    Template {
        id: "disk",
        title: "Disk",
        about: "mounted filesystems with a usage bar (df)",
        needs: Some("df"),
        toml: r#"[[tab]]
name = "Disk"
type = "command"
command = "df -hP | grep -vE '^(tmpfs|devtmpfs|overlay|squashfs|udev|shm|map )'"
format = "table"
headers = ["Filesystem", "Size", "Used", "Avail", "Use%", "Mounted on"]
interval = 30
[[tab.column]]
title = "Use%"
bar = 100
width = 8
"#,
    },
    Template {
        id: "containers",
        title: "Containers",
        about: "docker containers and their state",
        needs: Some("docker"),
        toml: r#"[[tab]]
name = "Containers"
type = "command"
command = "docker ps -a --format '{{json .}}'"
format = "jsonl"
interval = 10
[[tab.column]]
title = "Name"
path = "Names"
[[tab.column]]
title = "State"
path = "State"
ok_values = ["running"]
bad_values = ["exited", "dead"]
warn_values = ["restarting", "paused"]
[[tab.column]]
title = "Status"
path = "Status"
[[tab.column]]
title = "Image"
path = "Image"
"#,
    },
    Template {
        id: "units",
        title: "Services (systemd)",
        about: "your systemd user services and whether they run",
        needs: Some("systemctl"),
        toml: r#"[[tab]]
name = "Units"
type = "command"
command = "systemctl --user list-units --type=service --all --no-pager --plain --no-legend"
format = "table"
headers = ["Unit", "Load", "Active", "Sub", "Description"]
skip_header = false
[[tab.column]]
title = "Active"
ok_values = ["active"]
bad_values = ["failed"]
warn_values = ["activating", "deactivating"]
"#,
    },
    Template {
        id: "logs",
        title: "Logs",
        about: "recent journal lines, errors highlighted",
        needs: Some("journalctl"),
        toml: r#"[[tab]]
name = "Logs"
type = "command"
command = "journalctl --user -n 200 --no-pager -o short-iso"
interval = 5
highlight = { "error|fail|denied" = "bad", "warn" = "warn", "started|listening" = "ok" }
"#,
    },
];

pub fn on_path(prog: &str) -> bool {
    std::env::var_os("PATH").map(|p| std::env::split_paths(&p).any(|d| d.join(prog).is_file())).unwrap_or(false)
}

/// Templates whose program is installed here; only these are offered.
pub fn available() -> Vec<&'static Template> {
    TEMPLATES.iter().filter(|t| t.needs.map(on_path).unwrap_or(true)).collect()
}

/// Ticked by default: the minimum most people want. Everything else is one
/// keypress away, or a custom tab later.
pub const DEFAULT: &[&str] = &["processes", "disk"];

pub fn suggested() -> Vec<&'static str> {
    available().iter().map(|t| t.id).filter(|id| DEFAULT.contains(id)).collect()
}

pub fn render(ids: &[&str]) -> Result<String> {
    let mut out = String::from(
        "# herdeck deck: each [[tab]] is one tab, in this order (keys 1-9).\n\
         # ${NAME} reads an environment variable; keep secrets in ~/.config/herdeck/env.\n\
         # Every tab type and option: https://github.com/thefahien/herdeck#tab-types\n\n\
         refresh = 1\n",
    );
    for id in ids {
        let t = TEMPLATES.iter().find(|t| t.id == *id).with_context(|| {
            format!("unknown template {id:?}; known: {}", TEMPLATES.iter().map(|t| t.id).collect::<Vec<_>>().join(", "))
        })?;
        out.push_str(&format!("\n# --- {}: {}\n", t.title, t.about));
        out.push_str(t.toml);
    }
    Ok(out)
}

pub fn write(path: &Path, ids: &[&str], force: bool) -> Result<()> {
    if ids.is_empty() {
        bail!("pick at least one template");
    }
    if path.exists() && !force {
        bail!("{} already exists (use --force to replace it)", path.display());
    }
    let text = render(ids)?;
    crate::config::parse(&text, "template").context("template does not parse")?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    }
    std::fs::write(path, text).with_context(|| format!("writing {}", path.display()))?;
    Ok(())
}

pub enum Choice {
    Written,
    Demo,
    Quit,
}

/// Full-screen template picker. Returns what the user chose.
pub fn picker(path: &Path) -> Result<Choice> {
    let list = available();
    let suggested = suggested();
    let mut on: Vec<bool> = list.iter().map(|t| suggested.contains(&t.id)).collect();
    let mut sel = 0usize;
    let mut msg = String::new();
    let mut term = ratatui::init();
    let res = (|| -> Result<Choice> {
        loop {
            term.draw(|f| {
                let a = f.area();
                let mut lines = vec![
                    Line::from(Span::styled(" herdeck: set up your deck", Style::new().add_modifier(Modifier::BOLD))),
                    Line::from(Span::styled(
                        format!(
                            " No deck yet. Pick the tabs to start with; they are saved to {}",
                            crate::config::pretty(path)
                        ),
                        Style::new().fg(Color::DarkGray),
                    )),
                    Line::from(""),
                ];
                for (i, t) in list.iter().enumerate() {
                    let mark = if on[i] { "[x]" } else { "[ ]" };
                    let style = if i == sel { Style::new().add_modifier(Modifier::REVERSED) } else { Style::new() };
                    lines.push(Line::from(vec![
                        Span::styled(format!(" {mark} {:<20}", t.title), style),
                        Span::styled(format!(" {}", t.about), Style::new().fg(Color::Gray)),
                    ]));
                }
                lines.push(Line::from(""));
                lines.push(Line::from(Span::styled(
                    " space: toggle · j/k: move · enter: write deck and open · d: look at sample data · q: quit",
                    Style::new().fg(Color::Cyan),
                )));
                lines.push(Line::from(Span::styled(
                    " Add your own tabs to that file later: health checks, Prometheus, JSON APIs, any command.",
                    Style::new().fg(Color::DarkGray),
                )));
                lines.push(Line::from(Span::styled(
                    " Every tab type, with examples: herdeck --types and examples/tabs.toml in the repo.",
                    Style::new().fg(Color::DarkGray),
                )));
                if !msg.is_empty() {
                    lines.push(Line::from(Span::styled(format!(" {msg}"), Style::new().fg(Color::Red))));
                }
                f.render_widget(Paragraph::new(lines), Rect { height: a.height, ..a });
            })?;
            if let Event::Key(k) = event::read()? {
                if k.kind == KeyEventKind::Release {
                    continue;
                }
                match k.code {
                    KeyCode::Char('q') | KeyCode::Esc => return Ok(Choice::Quit),
                    KeyCode::Char('d') => return Ok(Choice::Demo),
                    KeyCode::Char('j') | KeyCode::Down => sel = (sel + 1).min(list.len().saturating_sub(1)),
                    KeyCode::Char('k') | KeyCode::Up => sel = sel.saturating_sub(1),
                    KeyCode::Char(' ') | KeyCode::Char('x') if !on.is_empty() => on[sel] = !on[sel],
                    KeyCode::Enter => {
                        let ids: Vec<&str> = list.iter().zip(&on).filter(|(_, o)| **o).map(|(t, _)| t.id).collect();
                        match write(path, &ids, false) {
                            Ok(()) => return Ok(Choice::Written),
                            Err(e) => msg = e.to_string(),
                        }
                    }
                    _ => {}
                }
            }
        }
    })();
    ratatui::restore();
    res
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_template_parses_and_builds() {
        let ids: Vec<&str> = TEMPLATES.iter().map(|t| t.id).collect();
        let d = crate::config::parse(&render(&ids).unwrap(), "t").unwrap();
        assert_eq!(d.tabs.len(), TEMPLATES.len());
        for t in &d.tabs {
            crate::sources::build(t["type"].as_str().unwrap(), &crate::config::expand(t)).unwrap();
        }
    }

    #[test]
    fn write_refuses_to_overwrite() {
        let p = std::env::temp_dir().join(format!("herdeck-init-{}", std::process::id())).join("tabs.toml");
        write(&p, &["processes"], false).unwrap();
        assert!(write(&p, &["disk"], false).is_err());
        write(&p, &["disk"], true).unwrap();
        assert!(std::fs::read_to_string(&p).unwrap().contains("name = \"Disk\""));
        std::fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }

    #[test]
    fn unknown_template_is_an_error() {
        assert!(render(&["nope"]).is_err());
    }
}
