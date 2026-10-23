//! Tab types. Each `[[tab]]` in tabs.toml becomes one `Source`.

use crate::model::{Row, Table};
use anyhow::Result;
use serde_json::Value;

pub mod command;
pub mod gpu;
pub mod http;
pub mod json;
pub mod processes;
pub mod prometheus;
pub mod quotas;

/// A y/N prompt a tab can raise from a key press (e.g. switching a GPU lane).
#[derive(Clone, Debug)]
pub struct Prompt {
    pub title: String,
    pub options: Vec<String>,
    pub ids: Vec<String>,
    pub target: String,
    pub warning: Option<String>,
    pub choice: usize,
}

pub trait Source: Send {
    /// False while required settings (url, command, ...) are missing.
    fn configured(&self) -> bool {
        true
    }
    /// Fetch fresh data. Runs on a background thread; may block or fail.
    fn fetch(&mut self) -> Result<Table>;
    /// Sample rows shown, marked DEMO, while not configured.
    fn demo(&self) -> Option<Table> {
        None
    }
    fn default_interval(&self) -> f64 {
        5.0
    }
    /// A key the app did not use, with the selected row. May open a prompt.
    fn on_key(&mut self, _key: char, _row: Option<&Row>) -> Option<Prompt> {
        None
    }
    /// The user confirmed a prompt. Returns a status message.
    fn confirm(&mut self, _prompt: &Prompt) -> Result<String> {
        Ok("nothing to do".into())
    }
    /// Extra key help for this tab, shown in the ? overlay.
    fn keys_help(&self) -> &'static str {
        ""
    }
}

/// Stands in for a tab whose config is broken, so the rest of the deck works.
pub struct Broken(pub String);

impl Source for Broken {
    fn fetch(&mut self) -> Result<Table> {
        Err(anyhow::anyhow!("config error: {}", self.0))
    }
}

pub const TYPES: &[(&str, &str)] = &[
    ("command", "run a shell command; show lines, a table, JSON, or herdeck's row format"),
    ("exec", "run a program that prints herdeck's JSON row format (custom tabs in any language)"),
    ("processes", "local processes with CPU and memory bars"),
    ("http", "HTTP and TCP health checks with latency trend"),
    ("json", "any HTTP API returning JSON, mapped to columns"),
    ("prometheus", "PromQL queries as columns, one row per label value"),
    ("gpu", "example: GPU cards from a small JSON endpoint, with lane switching"),
    ("ai-quotas", "example: Claude / Codex / Antigravity plan usage via a CLIProxyAPI container"),
];

pub fn build(kind: &str, cfg: &Value) -> Result<Box<dyn Source>> {
    Ok(match kind {
        "command" => Box::new(command::Command::new(cfg, "lines")),
        "exec" => Box::new(command::Command::new(cfg, "herdeck")),
        "processes" => Box::new(processes::Processes::new(cfg)),
        "http" => Box::new(http::Checks::new(cfg)),
        "json" => Box::new(json::Json::new(cfg)),
        "prometheus" => Box::new(prometheus::Prometheus::new(cfg)),
        "gpu" => Box::new(gpu::Gpu::new(cfg)),
        "ai-quotas" => Box::new(quotas::Quotas::new(cfg)),
        "python" => {
            anyhow::bail!("the python tab type was replaced by exec: print herdeck JSON from any script (see README)")
        }
        other => {
            anyhow::bail!("unknown type {other:?}; known: {}", TYPES.iter().map(|t| t.0).collect::<Vec<_>>().join(", "))
        }
    })
}

pub fn cfg_str(cfg: &Value, key: &str) -> Option<String> {
    cfg.get(key).and_then(|v| v.as_str()).map(String::from).filter(|s| !s.is_empty())
}

pub fn cfg_f64(cfg: &Value, key: &str) -> Option<f64> {
    cfg.get(key).and_then(crate::util::num)
}
