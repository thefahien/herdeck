//! JSON paths, column specs, colour rules and HTTP shared by the tab types.

use crate::model::{Cell, Row, Tone};
use anyhow::{anyhow, Result};
use regex::Regex;
use serde_json::{Map, Value};
use std::sync::OnceLock;
use std::time::Duration;

/// Follow a dotted path: `a.b.0.c`. An empty path returns the value itself.
pub fn dig<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    if path.is_empty() {
        return Some(v);
    }
    let mut cur = v;
    for part in path.split('.') {
        cur = match cur {
            Value::Object(m) => m.get(part)?,
            Value::Array(a) => {
                let i: i64 = part.parse().ok()?;
                let i = if i < 0 { a.len() as i64 + i } else { i };
                a.get(usize::try_from(i).ok()?)?
            }
            _ => return None,
        };
    }
    Some(cur)
}

/// A number from a JSON value, accepting strings such as "41%" or " 3.5 ".
pub fn num(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().trim_end_matches('%').trim().parse().ok(),
        _ => None,
    }
}

pub fn value_text(v: &Value) -> String {
    match v {
        Value::Null => "-".into(),
        Value::Bool(b) => if *b { "yes" } else { "no" }.into(),
        Value::String(s) => s.clone(),
        Value::Number(n) => n.as_f64().map(crate::model::trim_float).unwrap_or_else(|| n.to_string()),
        other => other.to_string(),
    }
}

/// Python-style number formats used in configs: "{:.2f}", "{:.0f}", "{:g}", "{}".
pub fn fmt_num(fmt: &str, n: f64) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\{:?(?:\.(\d+))?([fgd%])?\}").unwrap());
    if let Some(m) = re.captures(fmt) {
        let whole = m.get(0).unwrap();
        let body = match (m.get(1).map(|p| p.as_str().parse::<usize>().unwrap_or(0)), m.get(2).map(|k| k.as_str())) {
            (Some(p), Some("%")) => format!("{:.*}%", p, n * 100.0),
            (Some(p), _) => format!("{n:.p$}"),
            (None, Some("d")) => format!("{}", n.round() as i64),
            _ => crate::model::trim_float(n),
        };
        format!("{}{}{}", &fmt[..whole.start()], body, &fmt[whole.end()..])
    } else {
        crate::model::trim_float(n)
    }
}

/// How one column turns a raw value into a cell (json, prometheus, command tabs).
#[derive(Clone, Debug, Default)]
pub struct ColSpec {
    pub title: String,
    pub path: String,
    pub bar: Option<f64>,
    pub scale: f64,
    pub unit: Option<String>,
    pub format: Option<String>,
    pub width: usize,
    pub spark: bool,
    pub ok_values: Vec<String>,
    pub warn_values: Vec<String>,
    pub bad_values: Vec<String>,
    pub warn_above: Option<f64>,
    pub bad_above: Option<f64>,
    pub warn_below: Option<f64>,
    pub bad_below: Option<f64>,
}

fn strs(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(a)) => a.iter().map(value_text).collect(),
        Some(x) if !x.is_null() => vec![value_text(x)],
        _ => vec![],
    }
}

impl ColSpec {
    pub fn from_value(v: &Value) -> ColSpec {
        let f = |k: &str| v.get(k).and_then(num);
        let s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(String::from);
        let path = s("path").unwrap_or_default();
        ColSpec {
            title: s("title").unwrap_or_else(|| path.clone()),
            path,
            bar: f("bar"),
            scale: f("scale").unwrap_or(1.0),
            unit: s("unit"),
            format: s("format"),
            width: f("width").map(|w| w as usize).unwrap_or(10),
            spark: v.get("spark").and_then(|x| x.as_bool()).unwrap_or(false),
            ok_values: strs(v.get("ok_values")),
            warn_values: strs(v.get("warn_values")),
            bad_values: strs(v.get("bad_values")),
            warn_above: f("warn_above"),
            bad_above: f("bad_above"),
            warn_below: f("warn_below"),
            bad_below: f("bad_below"),
        }
    }

    pub fn list(v: Option<&Value>) -> Vec<ColSpec> {
        v.and_then(|x| x.as_array()).map(|a| a.iter().map(ColSpec::from_value).collect()).unwrap_or_default()
    }

    pub fn has_thresholds(&self) -> bool {
        self.warn_above.is_some() || self.bad_above.is_some() || self.warn_below.is_some() || self.bad_below.is_some()
    }

    pub fn tone_for(&self, raw: &str, n: Option<f64>) -> Option<Tone> {
        if self.bad_values.iter().any(|x| x == raw) {
            return Some(Tone::Bad);
        }
        if self.warn_values.iter().any(|x| x == raw) {
            return Some(Tone::Warn);
        }
        if self.ok_values.iter().any(|x| x == raw) {
            return Some(Tone::Ok);
        }
        let n = n?;
        let above = |t: Option<f64>| t.is_some_and(|t| n > t);
        let below = |t: Option<f64>| t.is_some_and(|t| n < t);
        if above(self.bad_above) || below(self.bad_below) {
            Some(Tone::Bad)
        } else if above(self.warn_above) || below(self.warn_below) {
            Some(Tone::Warn)
        } else if self.has_thresholds() {
            Some(Tone::Ok)
        } else {
            None
        }
    }

    pub fn cell(&self, v: Option<&Value>) -> Cell {
        let v = match v {
            None | Some(Value::Null) => return Cell::toned("-", Some(Tone::Dim)),
            Some(v) => v,
        };
        if let (true, Value::Array(a)) = (self.spark, v) {
            return Cell::spark(a.iter().map(|x| num(x).unwrap_or(0.0)).collect(), self.bar, self.width);
        }
        let n = num(v).map(|x| x * self.scale);
        if let (Some(max), Some(n)) = (self.bar, n) {
            let unit =
                self.unit
                    .clone()
                    .unwrap_or_else(|| if (max - 100.0).abs() < 1e-9 { "%".into() } else { String::new() });
            let text = format!("{}{}", fmt_num(self.format.as_deref().unwrap_or("{:.0f}"), n), unit);
            return Cell::bar(n, max, Some(text), self.width);
        }
        let raw = value_text(v);
        let shown = match (n, &self.format) {
            (Some(n), Some(f)) => fmt_num(f, n),
            (Some(n), None) if self.scale != 1.0 => crate::model::trim_float(n),
            _ => raw.clone(),
        };
        let text = format!("{}{}", shown, self.unit.clone().unwrap_or_default());
        Cell::toned(text, self.tone_for(&raw, num(v)))
    }
}

/// Map a JSON document to (titles, rows). Without columns, use the first rows' keys.
pub fn map_json(data: &Value, items: &str, cols: &[ColSpec], limit: usize) -> (Vec<String>, Vec<Row>) {
    let src = dig(data, items).cloned().unwrap_or(Value::Null);
    let list: Vec<Value> = match src {
        Value::Array(a) => a,
        Value::Object(m) => {
            if !m.is_empty() && m.values().all(|v| v.is_object()) {
                m.into_iter()
                    .map(|(k, v)| {
                        let mut o = Map::new();
                        o.insert("key".into(), Value::String(k));
                        if let Value::Object(inner) = v {
                            o.extend(inner);
                        }
                        Value::Object(o)
                    })
                    .collect()
            } else {
                m.into_iter().map(|(k, v)| serde_json::json!({"key": k, "value": v})).collect()
            }
        }
        Value::Null => vec![],
        other => vec![other],
    };
    let cols: Vec<ColSpec> = if cols.is_empty() {
        let mut keys: Vec<String> = vec![];
        for r in list.iter().take(20) {
            if let Value::Object(m) = r {
                for k in m.keys() {
                    if !keys.contains(k) && keys.len() < 8 {
                        keys.push(k.clone());
                    }
                }
            }
        }
        if keys.is_empty() {
            vec![ColSpec { title: "value".into(), scale: 1.0, width: 10, ..Default::default() }]
        } else {
            keys.into_iter()
                .map(|k| ColSpec { title: k.clone(), path: k, scale: 1.0, width: 10, ..Default::default() })
                .collect()
        }
    } else {
        cols.to_vec()
    };
    let titles = cols.iter().map(|c| c.title.clone()).collect();
    let rows =
        list.iter().take(limit).map(|r| Row::new(cols.iter().map(|c| c.cell(dig(r, &c.path))).collect())).collect();
    (titles, rows)
}

// ------------------------------------------------------------------ HTTP

/// An HTTP client that never follows redirects (a redirect would carry an
/// Authorization header to another host) and verifies TLS.
pub fn agent(timeout_s: f64) -> ureq::Agent {
    ureq::AgentBuilder::new()
        .redirects(0)
        .timeout(Duration::from_secs_f64(timeout_s.max(0.5)))
        .user_agent(concat!("herdeck/", env!("CARGO_PKG_VERSION")))
        .build()
}

/// (status, body). 4xx/5xx are returned, not errors; transport failures are errors.
pub fn http_get(url: &str, headers: &[(String, String)], timeout_s: f64) -> Result<(u16, String)> {
    let mut req = agent(timeout_s).get(url);
    for (k, v) in headers {
        req = req.set(k, v);
    }
    match req.call() {
        Ok(r) => {
            let code = r.status();
            Ok((code, r.into_string().unwrap_or_default()))
        }
        Err(ureq::Error::Status(code, r)) => Ok((code, r.into_string().unwrap_or_default())),
        Err(e) => Err(anyhow!(short_err(&e.to_string()))),
    }
}

pub fn http_json(url: &str, headers: &[(String, String)], timeout_s: f64) -> Result<Value> {
    let (code, body) = http_get(url, headers, timeout_s)?;
    if !(200..300).contains(&code) {
        return Err(anyhow!("HTTP {code}"));
    }
    serde_json::from_str(&body).map_err(|e| anyhow!("bad JSON: {e}"))
}

pub fn http_post_json(url: &str, headers: &[(String, String)], body: &Value, timeout_s: f64) -> Result<(u16, Value)> {
    let mut req = agent(timeout_s).post(url).set("Content-Type", "application/json");
    for (k, v) in headers {
        req = req.set(k, v);
    }
    let (code, text) = match req.send_string(&body.to_string()) {
        Ok(r) => (r.status(), r.into_string().unwrap_or_default()),
        Err(ureq::Error::Status(c, r)) => (c, r.into_string().unwrap_or_default()),
        Err(e) => return Err(anyhow!(short_err(&e.to_string()))),
    };
    Ok((code, serde_json::from_str(&text).unwrap_or(Value::Null)))
}

pub fn short_err(s: &str) -> String {
    let line = s.lines().next().unwrap_or(s);
    line.chars().take(90).collect()
}

/// Headers from a config table, e.g. `headers = { Authorization = "Bearer ${TOKEN}" }`.
pub fn headers(v: Option<&Value>) -> Vec<(String, String)> {
    v.and_then(|x| x.as_object())
        .map(|m| m.iter().map(|(k, v)| (k.clone(), value_text(v))).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn dig_paths() {
        let v = json!({"a": {"b": [1, {"c": 3}]}});
        assert_eq!(dig(&v, "a.b.1.c"), Some(&json!(3)));
        assert_eq!(dig(&v, "a.b.-1.c"), Some(&json!(3)));
        assert_eq!(dig(&v, "a.x"), None);
    }

    #[test]
    fn formats() {
        assert_eq!(fmt_num("{:.2f}", 1.234), "1.23");
        assert_eq!(fmt_num("{:.0f} ms", 12.6), "13 ms");
        assert_eq!(fmt_num("{:g}", 3.0), "3");
        assert_eq!(num(&json!("41%")), Some(41.0));
    }

    #[test]
    fn columns_and_rules() {
        let c = ColSpec::from_value(&json!({"title": "R", "path": "n", "bad_above": 3, "warn_above": 1}));
        assert_eq!(c.cell(Some(&json!(4))).tone(), Some(Tone::Bad));
        assert_eq!(c.cell(Some(&json!(2))).tone(), Some(Tone::Warn));
        assert_eq!(c.cell(Some(&json!(0))).tone(), Some(Tone::Ok));
        let b = ColSpec::from_value(&json!({"path": "p", "scale": 100, "bar": 100}));
        match b.cell(Some(&json!(0.5))) {
            Cell::Bar { value, text, .. } => {
                assert_eq!(value, 50.0);
                assert_eq!(text, "50%");
            }
            other => panic!("{other:?}"),
        }
        let s = ColSpec::from_value(&json!({"path": "s", "bad_values": ["down"]}));
        assert_eq!(s.cell(Some(&json!("down"))).tone(), Some(Tone::Bad));
    }

    #[test]
    fn auto_columns() {
        let (cols, rows) = map_json(&json!({"x": {"a": 1, "b": 2}}), "x", &[], 100);
        assert_eq!(cols, vec!["key", "value"]);
        assert_eq!(rows.len(), 2);
        let (cols, _) = map_json(&json!([{"n": 1, "m": 2}]), "", &[], 100);
        assert_eq!(cols, vec!["n", "m"]);
    }
}
