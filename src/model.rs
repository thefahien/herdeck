//! What a tab produces: a table of cells, plus status metadata.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tone {
    Ok,
    Warn,
    Bad,
    Accent,
    Dim,
}

impl Tone {
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
}
