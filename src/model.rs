//! What a tab produces: a table of cells, plus status metadata.

use serde_json::{json, Value};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Ok,
    Warn,
    Bad,
    Accent,
    Dim,
}

impl Tone {
    /// The lowercase name `herdeck --json` uses.
    pub fn as_str(self) -> &'static str {
        match self {
            Tone::Ok => "ok",
            Tone::Warn => "warn",
            Tone::Bad => "bad",
            Tone::Accent => "accent",
            Tone::Dim => "dim",
        }
    }

    pub fn parse(s: &str) -> Option<Tone> {
        match s.to_ascii_lowercase().as_str() {
            "ok" | "good" | "green" => Some(Tone::Ok),
            "warn" | "warning" | "yellow" => Some(Tone::Warn),
            "bad" | "error" | "red" => Some(Tone::Bad),
            "accent" | "info" | "cyan" => Some(Tone::Accent),
            "dim" | "muted" => Some(Tone::Dim),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Cell {
    Text { text: String, tone: Option<Tone> },
    Bar { value: f64, max: f64, text: String, width: usize },
    Spark { values: Vec<f64>, max: Option<f64>, width: usize },
}

impl Cell {
    pub fn text(s: impl Into<String>) -> Cell {
        Cell::Text { text: s.into(), tone: None }
    }
    pub fn toned(s: impl Into<String>, tone: Option<Tone>) -> Cell {
        Cell::Text { text: s.into(), tone }
    }
    pub fn bar(value: f64, max: f64, text: Option<String>, width: usize) -> Cell {
        let text = text.unwrap_or_else(|| {
            if (max - 100.0).abs() < f64::EPSILON {
                format!("{value:.0}%")
            } else {
                trim_float(value)
            }
        });
        Cell::Bar { value, max, text, width }
    }
    pub fn spark(values: Vec<f64>, max: Option<f64>, width: usize) -> Cell {
        Cell::Spark { values, max, width }
    }

    /// The characters drawn for this cell.
    pub fn render(&self) -> String {
        match self {
            Cell::Text { text, .. } => text.clone(),
            Cell::Bar { value, max, text, width } => format!("{} {:>5}", pbar(*value, *max, *width), text),
            Cell::Spark { values, max, width } => sparkline(values, *max, *width),
        }
    }

    /// Colour: explicit for text, by fill ratio for bars.
    pub fn tone(&self) -> Option<Tone> {
        match self {
            Cell::Text { tone, .. } => *tone,
            Cell::Bar { value, max, .. } => {
                let r = if *max > 0.0 { (value / max).clamp(0.0, 1.0) } else { 0.0 };
                Some(if r >= 0.9 {
                    Tone::Bad
                } else if r >= 0.7 {
                    Tone::Warn
                } else {
                    Tone::Ok
                })
            }
            Cell::Spark { .. } => Some(Tone::Accent),
        }
    }
}

/// A JSON number; NaN and infinities (which JSON cannot hold) become 0.
fn num(v: f64) -> Value {
    json!(if v.is_finite() { v } else { 0.0 })
}

impl Cell {
    /// The cell as `herdeck --json` prints it (docs/json.md): numbers, never glyphs.
    pub fn to_json(&self) -> Value {
        match self {
            Cell::Text { text, tone } => json!({"kind": "text", "text": text, "tone": tone.map(Tone::as_str)}),
            Cell::Bar { value, max, text, width } => json!({
                "kind": "bar",
                "value": num(*value),
                "max": num(*max),
                "text": text,
                "tone": self.tone().unwrap_or(Tone::Ok).as_str(),
                "width": width,
            }),
            Cell::Spark { values, max, width } => {
                let tail = &values[values.len().saturating_sub(*width)..];
                json!({
                    "kind": "spark",
                    "values": tail.iter().map(|v| num(*v)).collect::<Vec<_>>(),
                    "max": max.map(num),
                    "tone": "accent",
                    "width": width,
                })
            }
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct Row {
    pub cells: Vec<Cell>,
    /// Shown under the table while this row is selected.
    pub detail: Option<String>,
    /// A section title row (cells are ignored).
    pub section: Option<String>,
    /// Stable id the source can use to find the row again (e.g. a GPU id).
    pub key: Option<String>,
}

impl Row {
    pub fn new(cells: Vec<Cell>) -> Row {
        Row { cells, ..Default::default() }
    }
    pub fn section(title: impl Into<String>) -> Row {
        Row { section: Some(title.into()), ..Default::default() }
    }
    pub fn with_detail(mut self, d: impl Into<String>) -> Row {
        let d = d.into();
        if !d.is_empty() {
            self.detail = Some(d);
        }
        self
    }
    pub fn with_key(mut self, k: impl Into<String>) -> Row {
        self.key = Some(k.into());
        self
    }

    /// A data row `{cells, detail?, key?}` or a section row `{section}`.
    pub fn to_json(&self) -> Value {
        if let Some(title) = &self.section {
            return json!({ "section": title });
        }
        let mut m = serde_json::Map::new();
        m.insert("cells".into(), self.cells.iter().map(Cell::to_json).collect());
        if let Some(d) = &self.detail {
            m.insert("detail".into(), json!(d));
        }
        if let Some(k) = &self.key {
            m.insert("key".into(), json!(k));
        }
        Value::Object(m)
    }
}

#[derive(Clone, Debug, Default)]
pub struct Table {
    pub header: Option<String>,
    pub columns: Vec<String>,
    pub rows: Vec<Row>,
    /// Set when the rows are sample data; the reason is shown as DEMO (reason).
    pub demo: Option<String>,
    /// Shown in the status line, e.g. "partial: codex failed".
    pub note: Option<String>,
}

impl Table {
    pub fn new(columns: Vec<String>, rows: Vec<Row>) -> Table {
        Table { columns, rows, ..Default::default() }
    }

    /// `{header, columns, rows}`; demo and note are reported by the tab's state instead.
    pub fn to_json(&self) -> Value {
        json!({
            "header": self.header,
            "columns": self.columns,
            "rows": self.rows.iter().map(Row::to_json).collect::<Vec<_>>(),
        })
    }
}

pub fn trim_float(v: f64) -> String {
    if v.fract() == 0.0 && v.abs() < 1e15 {
        format!("{}", v as i64)
    } else {
        let s = format!("{v:.3}");
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

pub fn pbar(value: f64, max: f64, width: usize) -> String {
    if max <= 0.0 {
        return "░".repeat(width);
    }
    let r = (value / max).clamp(0.0, 1.0);
    let filled = (r * width as f64).round() as usize;
    format!("{}{}", "█".repeat(filled), "░".repeat(width - filled.min(width)))
}

const SPARKS: [char; 8] = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

pub fn sparkline(values: &[f64], max: Option<f64>, width: usize) -> String {
    if values.is_empty() {
        return "░".repeat(width);
    }
    let tail = &values[values.len().saturating_sub(width)..];
    let top = max.filter(|m| *m > 0.0).unwrap_or_else(|| tail.iter().cloned().fold(0.0, f64::max));
    let top = if top > 0.0 { top } else { 1.0 };
    let mut s: String =
        tail.iter().map(|v| SPARKS[(((v / top).clamp(0.0, 1.0)) * (SPARKS.len() - 1) as f64) as usize]).collect();
    while s.chars().count() < width {
        s.push(' ');
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bars_and_sparks() {
        assert_eq!(pbar(50.0, 100.0, 6), "███░░░");
        assert_eq!(pbar(1.0, 0.0, 3), "░░░");
        assert_eq!(sparkline(&[0.0, 50.0, 100.0], Some(100.0), 3), " ▄█");
        assert_eq!(Cell::bar(41.0, 100.0, None, 4).render(), "██░░   41%");
        assert_eq!(Cell::bar(95.0, 100.0, None, 4).tone(), Some(Tone::Bad));
        assert_eq!(trim_float(2.50), "2.5");
    }

    #[test]
    fn text_cell_json() {
        assert_eq!(Cell::text("hi").to_json(), json!({"kind": "text", "text": "hi", "tone": null}));
        assert_eq!(Cell::toned("up", Some(Tone::Ok)).to_json(), json!({"kind": "text", "text": "up", "tone": "ok"}));
    }

    #[test]
    fn bar_cell_json_tone_thresholds() {
        let tone = |v: f64, max: f64| Cell::bar(v, max, None, 8).to_json()["tone"].clone();
        assert_eq!(tone(69.0, 100.0), "ok");
        assert_eq!(tone(70.0, 100.0), "warn");
        assert_eq!(tone(89.0, 100.0), "warn");
        assert_eq!(tone(90.0, 100.0), "bad");
        assert_eq!(tone(250.0, 100.0), "bad");
        assert_eq!(tone(5.0, 0.0), "ok");
        assert_eq!(
            Cell::bar(15.8, 24.0, Some("15.8/24G".into()), 8).to_json(),
            json!({"kind": "bar", "value": 15.8, "max": 24.0, "text": "15.8/24G", "tone": "ok", "width": 8})
        );
        assert_eq!(Cell::bar(41.0, 100.0, None, 8).to_json()["text"], "41%");
        // JSON has no NaN or infinity
        assert_eq!(Cell::bar(f64::NAN, f64::INFINITY, None, 8).to_json()["value"], 0.0);
    }

    #[test]
    fn spark_cell_json_keeps_the_tail_the_tui_draws() {
        let values: Vec<f64> = (1..=12).map(f64::from).collect();
        let j = Cell::spark(values, None, 10).to_json();
        assert_eq!(j["values"].as_array().unwrap().len(), 10);
        assert_eq!(j["values"][0], 3.0);
        assert_eq!(j["values"][9], 12.0);
        assert_eq!(j["max"], Value::Null);
        assert_eq!(j["tone"], "accent");
        assert_eq!(j["width"], 10);
        let empty = Cell::spark(vec![], Some(8.0), 10).to_json();
        assert_eq!(empty["values"], json!([]));
        assert_eq!(empty["max"], 8.0);
    }

    #[test]
    fn row_and_table_json() {
        let plain = Row::new(vec![Cell::text("a")]);
        assert_eq!(plain.to_json(), json!({"cells": [{"kind": "text", "text": "a", "tone": null}]}));
        let full = Row::new(vec![]).with_detail("more").with_key("k1");
        assert_eq!(full.to_json(), json!({"cells": [], "detail": "more", "key": "k1"}));
        assert_eq!(Row::new(vec![]).with_detail("").to_json(), json!({"cells": []}));
        assert_eq!(Row::section("Title").to_json(), json!({"section": "Title"}));
        let keys: Vec<String> = full.to_json().as_object().unwrap().keys().cloned().collect();
        assert_eq!(keys, ["cells", "detail", "key"]);
        let t = Table { header: Some("h".into()), columns: vec!["A".into()], rows: vec![plain], ..Default::default() };
        assert_eq!(t.to_json()["header"], "h");
        assert_eq!(t.to_json()["rows"].as_array().unwrap().len(), 1);
        assert_eq!(Table::default().to_json()["header"], Value::Null);
    }
}
