//! `json`: GET a URL returning JSON and map it to columns.

use super::{cfg_f64, cfg_str, Source};
use crate::model::Table;
use crate::util::{headers, http_json, map_json, ColSpec};
use anyhow::Result;
use serde_json::Value;

pub struct Json {
    url: Option<String>,
    items: String,
    columns: Vec<ColSpec>,
    headers: Vec<(String, String)>,
    timeout: f64,
}

impl Json {
    pub fn new(cfg: &Value) -> Json {
        Json {
            url: cfg_str(cfg, "url"),
            items: cfg_str(cfg, "items").unwrap_or_default(),
            columns: ColSpec::list(cfg.get("column")),
            headers: headers(cfg.get("headers")),
            timeout: cfg_f64(cfg, "timeout").unwrap_or(8.0),
        }
    }
}

impl Source for Json {
    fn configured(&self) -> bool {
        self.url.is_some()
    }

    fn default_interval(&self) -> f64 {
        10.0
    }

    fn fetch(&mut self) -> Result<Table> {
        let mut h = vec![("Accept".to_string(), "application/json".to_string())];
        h.extend(self.headers.clone());
        let data = http_json(self.url.as_deref().unwrap_or_default(), &h, self.timeout)?;
        let (cols, rows) = map_json(&data, &self.items, &self.columns, 2000);
        Ok(Table::new(cols, rows))
    }
}
