//! `command` / `exec`: run something, render its output.

use super::{cfg_f64, cfg_str, Source};
use crate::model::{Cell, Row, Table, Tone};
use crate::util::{map_json, value_text, ColSpec};
use anyhow::{anyhow, bail, Result};
use regex::Regex;
use serde_json::Value;
use std::io::Read;
use std::process::{Command as Proc, Stdio};
use std::time::{Duration, Instant};

pub struct Command {
    argv: Vec<String>,
    shown: String,
    format: String,
    items: String,
    columns: Vec<ColSpec>,
    headers: Option<Vec<String>>,
    skip_header: bool,
    highlight: Vec<(Regex, Option<Tone>)>,
    timeout: f64,
    title: String,
    bad: Option<String>,
}

impl Command {
    pub fn new(cfg: &Value, default_format: &str) -> Command {
        let (argv, shown) = match cfg.get("command") {
            Some(Value::String(s)) => (vec!["sh".into(), "-c".into(), s.clone()], s.clone()),
            Some(Value::Array(a)) => {
                let v: Vec<String> = a.iter().map(value_text).collect();
                let shown = v.join(" ");
                (v, shown)
            }
            _ => (vec![], String::new()),
        };
        let mut bad = None;
        let highlight = cfg
            .get("highlight")
            .and_then(|h| h.as_object())
            .map(|m| {
                m.iter()
                    .filter_map(|(pat, tone)| match Regex::new(&format!("(?i){pat}")) {
                        Ok(re) => Some((re, tone.as_str().and_then(Tone::parse))),
                        Err(e) => {
                            bad = Some(format!("bad highlight regex {pat:?}: {e}"));
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();
        let mut columns = ColSpec::list(cfg.get("column"));
        if columns.is_empty() {
            columns = ColSpec::list(cfg.get("columns"));
        }
        Command {
            argv,
            shown,
            format: cfg_str(cfg, "format").unwrap_or_else(|| default_format.into()),
            items: cfg_str(cfg, "items").unwrap_or_default(),
            columns,
            headers: cfg.get("headers").and_then(|h| h.as_array()).map(|a| a.iter().map(value_text).collect()),
            skip_header: cfg.get("skip_header").and_then(|v| v.as_bool()).unwrap_or(true),
            highlight,
            timeout: cfg_f64(cfg, "timeout").unwrap_or(20.0),
            title: cfg_str(cfg, "title").unwrap_or_else(|| "output".into()),
            bad,
        }
    }

    fn tone(&self, line: &str) -> Cell {
        for (re, tone) in &self.highlight {
            if re.is_match(line) {
                return Cell::toned(line, *tone);
            }
        }
        Cell::text(line)
    }
}

/// Run argv with a timeout; returns (exit code, stdout, stderr).
pub fn run(argv: &[String], timeout_s: f64) -> Result<(i32, String, String)> {
    let mut child = Proc::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| anyhow!("{}: {e}", argv[0]))?;
    let mut out = child.stdout.take().unwrap();
    let mut err = child.stderr.take().unwrap();
    let t_out = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = out.read_to_string(&mut s);
        s
    });
    let t_err = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = err.read_to_string(&mut s);
        s
    });
    let start = Instant::now();
    let status = loop {
        if let Some(st) = child.try_wait()? {
            break st;
        }
        if start.elapsed() > Duration::from_secs_f64(timeout_s) {
            let _ = child.kill();
            let _ = child.wait();
            bail!("timed out after {timeout_s}s");
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    Ok((status.code().unwrap_or(-1), t_out.join().unwrap_or_default(), t_err.join().unwrap_or_default()))
}

/// herdeck's row format, printed by `exec` tabs:
/// {"header": "...", "columns": ["A", "B"], "rows": [[cell, ...], ...], "details": ["...", ...]}
/// A cell is a string/number, or {"text": "..", "tone": "ok|warn|bad|accent|dim"},
/// {"bar": 41, "max": 100, "text": "41%"}, or {"spark": [1, 2, 3]}.
/// A row may also be {"section": "Title"}.
pub fn parse_herdeck(v: &Value) -> Result<Table> {
    let cols: Vec<String> =
        v.get("columns").and_then(|c| c.as_array()).map(|a| a.iter().map(value_text).collect()).unwrap_or_default();
    let details: Vec<String> =
        v.get("details").and_then(|c| c.as_array()).map(|a| a.iter().map(value_text).collect()).unwrap_or_default();
    let rows = v.get("rows").and_then(|r| r.as_array()).ok_or_else(|| anyhow!("output has no \"rows\" array"))?;
    let mut out = vec![];
    for (i, r) in rows.iter().enumerate() {
        if let Some(t) = r.get("section").and_then(|s| s.as_str()) {
            out.push(Row::section(t));
            continue;
        }
        let cells = match r {
            Value::Array(a) => a.iter().map(parse_cell).collect(),
            Value::Object(_) => cols.iter().map(|c| parse_cell(r.get(c).unwrap_or(&Value::Null))).collect(),
            other => vec![parse_cell(other)],
        };
        let mut row = Row::new(cells);
        if let Some(d) = details.get(i) {
            row = row.with_detail(d.clone());
        }
        out.push(row);
    }
    let mut t = Table::new(cols, out);
    t.header = v.get("header").and_then(|h| h.as_str()).map(String::from);
    Ok(t)
}

fn parse_cell(v: &Value) -> Cell {
    if let Some(o) = v.as_object() {
        if let Some(b) = o.get("bar").and_then(crate::util::num) {
            let max = o.get("max").and_then(crate::util::num).unwrap_or(100.0);
            let text = o.get("text").map(value_text);
            let w = o.get("width").and_then(crate::util::num).unwrap_or(10.0) as usize;
            return Cell::bar(b, max, text, w);
        }
        if let Some(a) = o.get("spark").and_then(|s| s.as_array()) {
            let vals = a.iter().map(|x| crate::util::num(x).unwrap_or(0.0)).collect();
            return Cell::spark(vals, o.get("max").and_then(crate::util::num), 10);
        }
        let text = o.get("text").map(value_text).unwrap_or_default();
        return Cell::toned(text, o.get("tone").and_then(|t| t.as_str()).and_then(Tone::parse));
    }
    if v.is_null() {
        return Cell::toned("-", Some(Tone::Dim));
    }
    Cell::text(value_text(v))
}

impl Source for Command {
    fn configured(&self) -> bool {
        !self.argv.is_empty()
    }

    fn setup_hint(&self) -> Vec<String> {
        vec!["command = \"...\"   # a string runs in sh, a list runs directly".into()]
    }

    fn default_interval(&self) -> f64 {
        10.0
    }

    fn fetch(&mut self) -> Result<Table> {
        if let Some(b) = &self.bad {
            bail!("{b}");
        }
        let (code, out, err) = run(&self.argv, self.timeout)?;
        let last_err = err.trim().lines().last().map(String::from).unwrap_or_else(|| format!("exit {code}"));
        if code != 0 && (self.format != "lines" || out.trim().is_empty()) {
            if self.format == "lines" && !err.trim().is_empty() {
                let rows = err.lines().map(|l| Row::new(vec![Cell::toned(l, Some(Tone::Bad))])).collect();
                let mut t = Table::new(vec![self.title.clone()], rows);
                t.header = Some(format!("$ {}   (exit {code})", self.shown));
                return Ok(t);
            }
            bail!("{last_err}");
        }
        let mut t = match self.format.as_str() {
            "herdeck" => parse_herdeck(&serde_json::from_str(&out).map_err(|e| anyhow!("output is not JSON: {e}"))?)?,
            "json" | "jsonl" => {
                let data: Value = if self.format == "jsonl" {
                    Value::Array(
                        out.lines()
                            .filter(|l| !l.trim().is_empty())
                            .map(serde_json::from_str)
                            .collect::<Result<_, _>>()?,
                    )
                } else {
                    serde_json::from_str(&out)?
                };
                let (cols, rows) = map_json(&data, &self.items, &self.columns, 2000);
                Table::new(cols, rows)
            }
            "table" => {
                let mut lines = out.lines().filter(|l| !l.trim().is_empty());
                let titles: Vec<String> = match &self.headers {
                    Some(h) => {
                        if self.skip_header {
                            lines.next();
                        }
                        h.clone()
                    }
                    None => lines.next().map(|l| l.split_whitespace().map(String::from).collect()).unwrap_or_default(),
                };
                let n = titles.len().max(1);
                let rows = lines
                    .map(|l| {
                        let parts = resplit(l.trim(), n);
                        Row::new(
                            titles
                                .iter()
                                .zip(parts.iter())
                                .map(|(h, x)| match self.columns.iter().find(|c| &c.title == h) {
                                    Some(spec) => spec.cell(Some(&Value::String(x.clone()))),
                                    None => self.tone(x),
                                })
                                .collect(),
                        )
                    })
                    .collect();
                Table::new(titles, rows)
            }
            _ => {
                let rows = out.trim_end_matches('\n').lines().map(|l| Row::new(vec![self.tone(l)])).collect();
                Table::new(vec![self.title.clone()], rows)
            }
        };
        if t.header.is_none() && self.format != "herdeck" {
            t.header =
                Some(if code != 0 { format!("$ {}   (exit {code})", self.shown) } else { format!("$ {}", self.shown) });
        }
        Ok(t)
    }
}

/// Split on runs of whitespace into at most n fields; the last takes the rest.
fn resplit(line: &str, n: usize) -> Vec<String> {
    let mut out = vec![];
    let mut rest = line.trim_start();
    while out.len() + 1 < n && !rest.is_empty() {
        match rest.find(char::is_whitespace) {
            Some(end) => {
                out.push(rest[..end].to_string());
                rest = rest[end..].trim_start();
            }
            None => break,
        }
    }
    if !rest.is_empty() {
        out.push(rest.to_string());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fetch(cfg: Value, fmt: &str) -> Table {
        Command::new(&cfg, fmt).fetch().unwrap()
    }

    #[test]
    fn lines_with_highlight() {
        let t = fetch(json!({"command": "printf 'ok line\\nERROR here\\n'", "highlight": {"error": "bad"}}), "lines");
        assert_eq!(t.rows.len(), 2);
        assert_eq!(t.rows[1].cells[0].tone(), Some(Tone::Bad));
    }

    #[test]
    fn table_with_bar_and_rest_column() {
        let t = fetch(
            json!({"command": "printf 'fs use mount\\n/dev/a 41%% /my disk\\n'", "format": "table",
                   "column": [{"title": "use", "bar": 100}]}),
            "lines",
        );
        assert_eq!(t.columns, vec!["fs", "use", "mount"]);
        assert!(matches!(t.rows[0].cells[1], Cell::Bar { value, .. } if value == 41.0));
        assert_eq!(t.rows[0].cells[2].render(), "/my disk");
    }

    #[test]
    fn jsonl_with_tab_columns() {
        let t = fetch(
            json!({"command": "printf '{\"n\":\"a\",\"s\":\"up\"}\\n{\"n\":\"b\",\"s\":\"down\"}\\n'", "format": "jsonl",
                   "column": [{"title": "N", "path": "n"}, {"title": "S", "path": "s", "bad_values": ["down"]}]}),
            "lines",
        );
        assert_eq!(t.rows[1].cells[1].tone(), Some(Tone::Bad));
    }

    #[test]
    fn exec_format() {
        let t = fetch(
            json!({"command": r#"echo '{"header":"h","columns":["A","B"],"rows":[["x",{"bar":50}],{"section":"S"},[{"text":"no","tone":"bad"},{"spark":[1,2]}]],"details":["d0"]}'"#}),
            "herdeck",
        );
        assert_eq!(t.header.as_deref(), Some("h"));
        assert_eq!(t.rows[0].detail.as_deref(), Some("d0"));
        assert_eq!(t.rows[1].section.as_deref(), Some("S"));
        assert_eq!(t.rows[2].cells[0].tone(), Some(Tone::Bad));
    }

    #[test]
    fn env_is_left_to_the_shell() {
        std::env::set_var("HD_TEST_INJECT", "x; echo INJECTED");
        let t = fetch(json!({"command": "printf '%s\\n' \"$HD_TEST_INJECT\""}), "lines");
        assert_eq!(t.rows.len(), 1);
        assert_eq!(t.rows[0].cells[0].render(), "x; echo INJECTED");
    }

    #[test]
    fn failure_is_an_error() {
        let r = Command::new(&json!({"command": "echo boom >&2; exit 3", "format": "table"}), "lines").fetch();
        assert!(r.unwrap_err().to_string().contains("boom"));
    }

    #[test]
    fn timeout() {
        let r = Command::new(&json!({"command": "sleep 5", "timeout": 0.3}), "lines").fetch();
        assert!(r.unwrap_err().to_string().contains("timed out"));
    }
}
