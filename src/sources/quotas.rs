//! `ai-quotas` (example of a richer tab): plan usage of the Claude, Codex and
//! Antigravity accounts logged into a CLIProxyAPI container, read over SSH.
//! The provider usage endpoints are undocumented and may change.

use super::{cfg_str, Source};
use crate::model::{Cell, Row, Table};
use crate::util::{http_get, num, value_text};
use anyhow::{anyhow, bail, Result};
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;

pub struct Quotas {
    ssh: Option<String>,
    container: String,
    auth_dir: String,
}

impl Quotas {
    pub fn new(cfg: &Value) -> Quotas {
        Quotas {
            ssh: cfg_str(cfg, "ssh"),
            container: cfg_str(cfg, "container").unwrap_or_else(|| "cli-proxy-api".into()),
            auth_dir: cfg_str(cfg, "auth_dir").unwrap_or_else(|| "/root/.cli-proxy-api".into()),
        }
    }

    fn ssh(&self, cmd: &str, timeout: f64) -> Result<String> {
        let argv = vec![
            "ssh".into(),
            "-o".into(),
            "BatchMode=yes".into(),
            "-o".into(),
            "ConnectTimeout=5".into(),
            "--".into(),
            self.ssh.clone().unwrap_or_default(),
            cmd.to_string(),
        ];
        let (code, out, err) = super::command::run(&argv, timeout)?;
        if code != 0 {
            bail!("ssh: {}", err.trim().lines().last().unwrap_or("failed"));
        }
        Ok(out)
    }

    fn auth(&self, prefix: &str) -> Result<Value> {
        let c = shq(&self.container);
        let inner = format!("ls -1 {}/{prefix}-*.json 2>/dev/null | head -n1", shq(&self.auth_dir));
        let cmd = format!("f=$(docker exec {c} sh -c {}); [ -n \"$f\" ] && docker exec {c} cat \"$f\"", shq(&inner));
        let v: Value = serde_json::from_str(&self.ssh(&cmd, 10.0)?)?;
        if v.get("access_token").and_then(|t| t.as_str()).unwrap_or("").is_empty() {
            bail!("no access_token");
        }
        Ok(v)
    }
}

fn shq(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

fn get(url: &str, h: Vec<(String, String)>, post: bool) -> Result<Value> {
    let (code, body) = if post {
        let (c, v) = crate::util::http_post_json(url, &h, &serde_json::json!({}), 8.0)?;
        (c, v.to_string())
    } else {
        http_get(url, &h, 8.0)?
    };
    if !(200..300).contains(&code) {
        return Err(anyhow!("HTTP {code}"));
    }
    Ok(serde_json::from_str(&body)?)
}

fn reset(s: Option<&Value>) -> String {
    let s = s.map(value_text).unwrap_or_default();
    if s.len() >= 16 && s != "-" {
        format!("resets {} {} UTC", &s[5..10], &s[11..16])
    } else {
        String::new()
    }
}

fn meter(provider: &str, pool: &str, used: Option<f64>, note: String) -> Row {
    match used {
        Some(u) => Row::new(vec![
            Cell::text(provider),
            Cell::text(pool),
            Cell::bar(u, 100.0, None, 16),
            Cell::text(if u >= 99.0 { format!("EXHAUSTED  {note}") } else { note }),
        ]),
        None => Row::new(vec![
            Cell::text(provider),
            Cell::text(pool),
            Cell::toned("unavailable", Some(crate::model::Tone::Dim)),
            Cell::text(note),
        ]),
    }
}

impl Source for Quotas {
    fn configured(&self) -> bool {
        self.ssh.is_some()
    }

    fn setup_hint(&self) -> Vec<String> {
        vec![
            "ssh = \"user@host\"   # where the CLIProxyAPI container runs".into(),
            "(or HERDECK_CLIPROXY_SSH in ~/.config/herdeck/env)".into(),
        ]
    }

    fn default_interval(&self) -> f64 {
        60.0
    }

    fn demo(&self) -> Option<Table> {
        let rows = vec![
            Row::section("Plan quotas"),
            meter("Claude", "5-hour", Some(12.0), "resets 10-23 04:00 UTC".into()),
            meter("Claude", "weekly", Some(45.0), "resets 10-26 05:00 UTC".into()),
            meter("Codex (plus)", "5-hour", Some(5.0), String::new()),
            meter("Codex (plus)", "weekly", Some(10.0), String::new()),
            meter("Antigravity", "Gemini", Some(25.0), "resets 10-23 04:30 UTC".into()),
            meter("Antigravity", "Claude/GPT", Some(80.0), "resets 10-26 18:00 UTC".into()),
            Row::section("Requests, last 24 h"),
            Row::new(vec![
                Cell::text("claude-sonnet"),
                Cell::text("420"),
                Cell::bar(47.0, 100.0, None, 16),
                Cell::text(""),
            ]),
            Row::new(vec![
                Cell::text("gemini-flash"),
                Cell::text("280"),
                Cell::bar(31.0, 100.0, None, 16),
                Cell::text(""),
            ]),
            Row::new(vec![
                Cell::text("gpt-codex"),
                Cell::text("115"),
                Cell::bar(13.0, 100.0, None, 16),
                Cell::text(""),
            ]),
        ];
        let mut t =
            Table::new(["ACCOUNT / MODEL", "WINDOW / REQS", "USED / SHARE", ""].map(String::from).to_vec(), rows);
        t.header = Some("200 OK: 870 · 429: 2 · requests: 815".into());
        Some(t)
    }

    fn fetch(&mut self) -> Result<Table> {
        let mut rows = vec![Row::section("Plan quotas")];
        let mut failed = vec![];
        let ua = || ("User-Agent".to_string(), "herdeck".to_string());

        match self.auth("claude").and_then(|a| {
            let tok = value_text(&a["access_token"]);
            get(
                "https://api.anthropic.com/oauth/usage",
                vec![("Authorization".into(), format!("Bearer {tok}")), ua()],
                false,
            )
        }) {
            Ok(d) => {
                rows.push(meter(
                    "Claude",
                    "5-hour",
                    d.pointer("/five_hour/utilization").and_then(num),
                    reset(d.pointer("/five_hour/resets_at")),
                ));
                rows.push(meter(
                    "Claude",
                    "weekly",
                    d.pointer("/seven_day/utilization").and_then(num),
                    reset(d.pointer("/seven_day/resets_at")),
                ));
            }
            Err(_) => failed.push("claude"),
        }

        match self.auth("codex").and_then(|a| {
            let mut h = vec![("Authorization".into(), format!("Bearer {}", value_text(&a["access_token"]))), ua()];
            if let Some(acct) = a.get("account_id").and_then(|x| x.as_str()) {
                h.push(("chatgpt-account-id".into(), acct.into()));
            }
            get("https://chatgpt.com/backend-api/wham/usage", h, false)
        }) {
            Ok(d) => {
                let plan = d.get("plan_type").map(value_text).unwrap_or_else(|| "-".into());
                let name = format!("Codex ({plan})");
                let limited = d.pointer("/rate_limit/allowed").and_then(|a| a.as_bool()) == Some(false);
                rows.push(meter(
                    &name,
                    "5-hour",
                    d.pointer("/rate_limit/primary_window/used_percent").and_then(num),
                    if limited { "limited".into() } else { String::new() },
                ));
                rows.push(meter(
                    &name,
                    "weekly",
                    d.pointer("/rate_limit/secondary_window/used_percent").and_then(num),
                    String::new(),
                ));
            }
            Err(_) => failed.push("codex"),
        }

        match self.auth("antigravity").and_then(|a| {
            let h = vec![
                ("Authorization".into(), format!("Bearer {}", value_text(&a["access_token"]))),
                ("User-Agent".into(), "antigravity".into()),
            ];
            let base = "https://cloudcode-pa.googleapis.com/v1internal:";
            Ok((
                get(&format!("{base}retrieveUserQuotaSummary"), h.clone(), true)?,
                get(&format!("{base}retrieveUserQuota"), h, true)?,
            ))
        }) {
            Ok((summary, per_model)) => {
                let mut lowest: Option<(f64, Value)> = None;
                for b in per_model.get("buckets").and_then(|b| b.as_array()).cloned().unwrap_or_default() {
                    let is_gemini = b.get("modelId").map(value_text).unwrap_or_default().starts_with("gemini");
                    if let (true, Some(rem)) = (is_gemini, b.get("remainingFraction").and_then(num)) {
                        if lowest.as_ref().is_none_or(|(l, _)| rem < *l) {
                            lowest = Some((rem, b.get("resetTime").cloned().unwrap_or(Value::Null)));
                        }
                    }
                }
                let (rem, rst) = lowest.unwrap_or((1.0, Value::Null));
                rows.push(meter("Antigravity", "Gemini", Some((1.0 - rem) * 100.0), reset(Some(&rst))));
                let mut other = None;
                for g in summary.get("groups").and_then(|g| g.as_array()).cloned().unwrap_or_default() {
                    for b in g.get("buckets").and_then(|b| b.as_array()).cloned().unwrap_or_default() {
                        if b.get("bucketId").and_then(|x| x.as_str()) == Some("3p-weekly") {
                            other = b
                                .get("remainingFraction")
                                .and_then(num)
                                .map(|r| ((1.0 - r) * 100.0, b.get("resetTime").cloned()));
                        }
                    }
                }
                if let Some((u, r)) = other {
                    rows.push(meter("Antigravity", "Claude/GPT", Some(u), reset(r.as_ref())));
                }
            }
            Err(_) => failed.push("antigravity"),
        }

        let mut header = String::new();
        match self.ssh(&format!("docker logs --since 24h {} 2>&1", shq(&self.container)), 20.0) {
            Ok(logs) => {
                let model_re = Regex::new(r"model=(\S+)").unwrap();
                let code_re = Regex::new(r"\] (\d{3}) \|").unwrap();
                let mut models: HashMap<String, u64> = HashMap::new();
                let mut codes: HashMap<String, u64> = HashMap::new();
                for l in logs.lines() {
                    if l.contains("selector.go") && l.contains("session-affinity") {
                        if let Some(m) = model_re.captures(l) {
                            *models.entry(m[1].to_string()).or_default() += 1;
                        }
                    } else if l.contains("gin_logger") && l.contains("POST") {
                        if let Some(c) = code_re.captures(l) {
                            *codes.entry(c[1].to_string()).or_default() += 1;
                        }
                    }
                }
                let total: u64 = models.values().sum();
                let mut ms: Vec<_> = models.into_iter().collect();
                ms.sort_by_key(|m| std::cmp::Reverse(m.1));
                rows.push(Row::section("Requests, last 24 h"));
                for (m, n) in ms.into_iter().take(15) {
                    let share = if total > 0 { n as f64 * 100.0 / total as f64 } else { 0.0 };
                    rows.push(Row::new(vec![
                        Cell::text(m),
                        Cell::text(n.to_string()),
                        Cell::bar(share, 100.0, None, 16),
                        Cell::text(""),
                    ]));
                }
                header = format!(
                    "200 OK: {} · 429: {} · requests: {total}",
                    codes.get("200").copied().unwrap_or(0),
                    codes.get("429").copied().unwrap_or(0)
                );
            }
            Err(_) => failed.push("logs"),
        }
        if failed.len() == 4 {
            bail!("every source failed (ssh to the proxy host?)");
        }
        let mut t =
            Table::new(["ACCOUNT / MODEL", "WINDOW / REQS", "USED / SHARE", ""].map(String::from).to_vec(), rows);
        t.header = Some(header);
        if !failed.is_empty() {
            t.note = Some(format!("partial: {} failed", failed.join(", ")));
        }
        Ok(t)
    }
}
