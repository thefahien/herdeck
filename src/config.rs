//! Load tabs.toml. Lookup: --config, $HERDECK_CONFIG,
//! ${XDG_CONFIG_HOME:-~/.config}/herdeck/tabs.toml. With none, main runs first-run setup.

use anyhow::{anyhow, Context, Result};
use regex::Regex;
use serde_json::{json, Value};
use std::path::PathBuf;

pub struct Deck {
    pub tabs: Vec<Value>,
    pub refresh: f64,
    pub source: String,
}

/// Substitute ${NAME} and ${NAME:-default} in every string except `command`
/// values: the shell expands those itself, so a variable's content is never
/// pasted into a command line where it could run as code.
pub fn expand(v: &Value) -> Value {
    fn walk(v: &Value, re: &Regex) -> Value {
        match v {
            Value::String(s) => Value::String(
                re.replace_all(s, |c: &regex::Captures| {
                    std::env::var(&c[1])
                        .ok()
                        .filter(|x| !x.is_empty())
                        .unwrap_or_else(|| c.get(2).map(|d| d.as_str().to_string()).unwrap_or_default())
                })
                .into_owned(),
            ),
            Value::Array(a) => Value::Array(a.iter().map(|x| walk(x, re)).collect()),
            Value::Object(m) => Value::Object(
                m.iter().map(|(k, x)| (k.clone(), if k == "command" { x.clone() } else { walk(x, re) })).collect(),
            ),
            other => other.clone(),
        }
    }
    let re = Regex::new(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}").unwrap();
    walk(v, &re)
}

pub fn config_path(explicit: Option<&str>) -> PathBuf {
    if let Some(p) = explicit {
        return expand_home(p);
    }
    if let Ok(p) = std::env::var("HERDECK_CONFIG") {
        if !p.is_empty() {
            return expand_home(&p);
        }
    }
    let base = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| home().join(".config"));
    base.join("herdeck").join("tabs.toml")
}

/// A path for messages, with the home directory shown as ~.
pub fn pretty(p: &std::path::Path) -> String {
    let h = home();
    match p.strip_prefix(&h) {
        Ok(rest) if h.as_os_str() != "/" => format!("~/{}", rest.display()),
        _ => p.display().to_string(),
    }
}

pub fn home() -> PathBuf {
    std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("/"))
}

pub fn expand_home(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => home().join(rest),
        None => PathBuf::from(p),
    }
}

/// Error text when no tabs.toml exists; main turns it into the first-run setup.
pub const NO_DECK: &str = "no deck yet";

pub fn demo_deck() -> Deck {
    Deck { tabs: default_tabs(), refresh: 1.0, source: "demo deck (sample data)".into() }
}

pub fn default_tabs() -> Vec<Value> {
    vec![
        json!({"name": "Processes", "type": "processes", "sort": "cpu", "limit": 40}),
        json!({"name": "Disk", "type": "command",
               "command": "df -hP | grep -vE '^(tmpfs|devtmpfs|overlay|squashfs|udev|shm|map )'",
               "format": "table", "headers": ["Filesystem", "Size", "Used", "Avail", "Use%", "Mounted on"],
               "interval": 30, "column": [{"title": "Use%", "bar": 100, "width": 8}]}),
        json!({"name": "Services", "type": "http"}),
        json!({"name": "GPUs", "type": "gpu"}),
        json!({"name": "AI quotas", "type": "ai-quotas"}),
    ]
}

pub fn load(explicit: Option<&str>) -> Result<Deck> {
    let path = config_path(explicit);
    if !path.exists() {
        if explicit.is_some() {
            return Err(anyhow!("config not found: {}", path.display()));
        }
        return Err(anyhow!(NO_DECK));
    }
    let text = std::fs::read_to_string(&path).with_context(|| format!("reading {}", path.display()))?;
    parse(&text, &path.display().to_string())
}

pub fn parse(text: &str, source: &str) -> Result<Deck> {
    let t: toml::Table = toml::from_str(text).map_err(|e| anyhow!("{source}: {e}"))?;
    let v = serde_json::to_value(t)?;
    let tabs: Vec<Value> = v.get("tab").and_then(|t| t.as_array()).cloned().unwrap_or_default();
    if tabs.is_empty() {
        return Err(anyhow!("{source} has no [[tab]] entries"));
    }
    Ok(Deck {
        tabs,
        refresh: v.get("refresh").and_then(crate::util::num).unwrap_or(1.0).clamp(0.1, 5.0),
        source: source.into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_expansion_skips_commands() {
        std::env::set_var("HD_X", "v");
        let v = expand(&json!({"u": "${HD_X}/${HD_NOPE:-d}", "command": "echo ${HD_X}", "list": ["${HD_X}"]}));
        assert_eq!(v["u"], "v/d");
        assert_eq!(v["command"], "echo ${HD_X}");
        assert_eq!(v["list"][0], "v");
    }

    #[test]
    fn example_deck_parses() {
        let d = parse(include_str!("../examples/tabs.toml"), "examples/tabs.toml").unwrap();
        assert!(d.tabs.len() >= 10);
        for t in &d.tabs {
            let kind = t.get("type").and_then(|k| k.as_str()).unwrap();
            crate::sources::build(kind, &expand(t)).unwrap();
        }
    }
}
