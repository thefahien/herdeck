//! `prometheus`: one row per label value, one column per query.

use super::{cfg_str, Source};
use crate::model::{Row, Table};
use crate::util::{http_json, value_text, ColSpec};
use anyhow::{anyhow, Result};
use serde_json::Value;
use std::collections::HashMap;

pub struct Prometheus {
    url: Option<String>,
    label: String,
    rows: Vec<String>,
    metrics: Vec<(String, ColSpec)>,
}

impl Prometheus {
    pub fn new(cfg: &Value) -> Prometheus {
        let metrics = cfg
            .get("metric")
            .and_then(|m| m.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|m| {
                        let q = cfg_str(m, "query")?;
                        let mut spec = ColSpec::from_value(m);
                        if spec.title.is_empty() {
                            spec.title = q.chars().take(16).collect();
                        }
                        Some((q, spec))
                    })
                    .collect()
            })
            .unwrap_or_default();
        Prometheus {
            url: cfg_str(cfg, "url").map(|u| u.trim_end_matches('/').to_string()),
            label: cfg_str(cfg, "label").unwrap_or_else(|| "instance".into()),
            rows: cfg
                .get("rows")
                .and_then(|r| r.as_array())
                .map(|a| a.iter().map(value_text).collect())
                .unwrap_or_default(),
            metrics,
        }
    }

    fn query(&self, path: &str, params: &[(&str, String)]) -> Result<Vec<Value>> {
        let base = self.url.as_deref().unwrap_or_default();
        let qs: Vec<String> = params.iter().map(|(k, v)| format!("{k}={}", urlencode(v))).collect();
        let d = http_json(&format!("{base}{path}?{}", qs.join("&")), &[], 8.0)?;
        if d.get("status").and_then(|s| s.as_str()) != Some("success") {
            return Err(anyhow!("{}", d.get("error").map(value_text).unwrap_or_else(|| "query failed".into())));
        }
        Ok(d.pointer("/data/result").and_then(|r| r.as_array()).cloned().unwrap_or_default())
    }
}

pub fn urlencode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

impl Source for Prometheus {
    fn configured(&self) -> bool {
        self.url.is_some() && !self.metrics.is_empty()
    }

    fn setup_hint(&self) -> Vec<String> {
        vec![
            "url = \"http://localhost:9090\"".into(),
            "[[tab.metric]] with title and query, one per column".into(),
            "(or PROMETHEUS_URL in ~/.config/herdeck/env)".into(),
        ]
    }

    fn fetch(&mut self) -> Result<Table> {
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)?.as_secs();
        let mut keys: Vec<String> = self.rows.clone();
        let mut table: HashMap<String, HashMap<usize, Value>> = HashMap::new();
        for (j, (q, spec)) in self.metrics.iter().enumerate() {
            let series = if spec.spark {
                self.query(
                    "/api/v1/query_range",
                    &[
                        ("query", q.clone()),
                        ("start", (now - 600).to_string()),
                        ("end", now.to_string()),
                        ("step", "30".into()),
                    ],
                )?
            } else {
                self.query("/api/v1/query", &[("query", q.clone())])?
            };
            for s in series {
                let k = s.pointer(&format!("/metric/{}", self.label)).map(value_text).unwrap_or_default();
                let v = if spec.spark {
                    Value::Array(
                        s.get("values")
                            .and_then(|v| v.as_array())
                            .map(|a| a.iter().filter_map(|p| p.get(1).cloned()).collect())
                            .unwrap_or_default(),
                    )
                } else {
                    s.pointer("/value/1").cloned().unwrap_or(Value::Null)
                };
                if self.rows.is_empty() && !keys.contains(&k) {
                    keys.push(k.clone());
                }
                table.entry(k).or_default().insert(j, v);
            }
        }
        if table.is_empty() {
            return Err(anyhow!("queries returned no series"));
        }
        let mut cols = vec![self.label.to_uppercase()];
        cols.extend(self.metrics.iter().map(|(_, s)| s.title.clone()));
        let rows = keys
            .iter()
            .map(|k| {
                let mut cells = vec![crate::model::Cell::text(k)];
                for (j, (_, spec)) in self.metrics.iter().enumerate() {
                    cells.push(spec.cell(table.get(k).and_then(|m| m.get(&j))));
                }
                Row::new(cells).with_key(k.clone())
            })
            .collect();
        Ok(Table::new(cols, rows))
    }
}
