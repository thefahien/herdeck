//! `gpu` (example of a richer tab): cards from `GET <url>/api/state`, lane switching.

use super::{cfg_str, Prompt, Source};
use crate::model::{Cell, Row, Table, Tone};
use crate::util::{http_json, http_post_json, num, value_text};
use anyhow::{anyhow, bail, Result};
use serde_json::{json, Value};

pub struct Gpu {
    url: Option<String>,
    token: Option<String>,
    state: Value,
    ever_ok: bool,
    demo_mode: bool,
}

impl Gpu {
    pub fn new(cfg: &Value) -> Gpu {
        Gpu {
            url: cfg_str(cfg, "url").map(|u| u.trim_end_matches('/').to_string()),
            token: cfg_str(cfg, "token"),
            state: Value::Null,
            ever_ok: false,
            demo_mode: false,
        }
    }

    fn gpus(&self) -> Vec<Value> {
        self.state.get("gpus").and_then(|g| g.as_array()).cloned().unwrap_or_default()
    }

    fn render(&self) -> Table {
        let rows = self
            .gpus()
            .iter()
            .map(|g| {
                let card = g.get("card").cloned().unwrap_or(Value::Null);
                let f = |k: &str| card.get(k).and_then(num).unwrap_or(0.0);
                let (used, total) = (f("vram_used_mb") / 1024.0, f("vram_total_mb") / 1024.0);
                let temp = f("temp_c");
                let active = g.get("active").map(value_text).filter(|s| s != "-").unwrap_or_else(|| "none".into());
                let hist: Vec<f64> = g
                    .get("util_hist")
                    .and_then(|h| h.as_array())
                    .map(|a| a.iter().filter_map(num).collect())
                    .unwrap_or_default();
                let lanes: Vec<String> = g
                    .get("lanes")
                    .and_then(|l| l.as_array())
                    .map(|a| {
                        a.iter()
                            .map(|l| {
                                let id = l.get("id").map(value_text).unwrap_or_default();
                                let label = l.get("label").map(value_text).unwrap_or_else(|| id.clone());
                                if id == active {
                                    format!("{label} ●")
                                } else {
                                    label
                                }
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                let procs: Vec<String> = g
                    .get("procs")
                    .and_then(|p| p.as_array())
                    .map(|a| {
                        a.iter()
                            .map(|p| {
                                let name = p.get("name").map(value_text).unwrap_or_default();
                                let name = name.rsplit('/').next().unwrap_or(&name).to_string();
                                format!("{name} ({})", p.get("mem").map(value_text).unwrap_or_default())
                            })
                            .collect()
                    })
                    .unwrap_or_default();
                let detail = format!(
                    "lanes: {}   procs: {}",
                    if lanes.is_empty() { "-".into() } else { lanes.join(" · ") },
                    if procs.is_empty() { "idle".into() } else { procs.join(" · ") }
                );
                Row::new(vec![
                    Cell::text(g.get("label").or(g.get("id")).map(value_text).unwrap_or_default()),
                    Cell::bar(f("util"), 100.0, None, 8),
                    Cell::bar(used, total, Some(format!("{used:.1}/{total:.0}G")), 8),
                    Cell::toned(
                        format!("{temp:.0}°C"),
                        Some(if temp >= 85.0 {
                            Tone::Bad
                        } else if temp >= 75.0 {
                            Tone::Warn
                        } else {
                            Tone::Ok
                        }),
                    ),
                    Cell::text(format!("{:.0}W", f("power_w"))),
                    Cell::spark(hist, Some(100.0), 10),
                    Cell::toned(active.clone(), Some(if active == "none" { Tone::Dim } else { Tone::Ok })),
                ])
                .with_detail(detail)
                .with_key(g.get("id").map(value_text).unwrap_or_default())
            })
            .collect();
        let mut t =
            Table::new(["CARD", "UTIL", "VRAM", "TEMP", "POWER", "10 MIN", "LANE"].map(String::from).to_vec(), rows);
        t.header = Some("s: switch lane of the selected card".into());
        t
    }

    fn demo_state() -> Value {
        json!({"gpus": [
            {"id": "gpu0", "label": "GPU 0: RTX 4090", "active": "render",
             "card": {"util": 45, "vram_used_mb": 12288, "vram_total_mb": 24576, "temp_c": 58, "power_w": 240},
             "util_hist": [10, 15, 20, 45, 60, 45, 50, 45],
             "procs": [{"pid": "1042", "name": "python render_worker.py", "mem": "11.8 GiB"}],
             "lanes": [{"id": "render", "label": "Render"}, {"id": "llm", "label": "LLM"}, {"id": "asr", "label": "ASR"}]},
            {"id": "gpu1", "label": "GPU 1: RTX 3080", "active": "llm",
             "card": {"util": 12, "vram_used_mb": 4096, "vram_total_mb": 10240, "temp_c": 46, "power_w": 85},
             "util_hist": [5, 5, 12, 12, 12, 10, 12, 12],
             "procs": [{"pid": "2088", "name": "llama-server", "mem": "3.8 GiB"}],
             "lanes": [{"id": "llm", "label": "LLM"}, {"id": "asr", "label": "ASR"}]}
        ]})
    }
}

impl Source for Gpu {
    fn configured(&self) -> bool {
        self.url.is_some()
    }

    fn default_interval(&self) -> f64 {
        2.0
    }

    fn demo(&self) -> Option<Table> {
        let mut g = Gpu {
            state: if self.demo_mode { self.state.clone() } else { Gpu::demo_state() },
            ..Gpu::new(&Value::Null)
        };
        g.demo_mode = true;
        Some(g.render())
    }

    fn fetch(&mut self) -> Result<Table> {
        let url = format!("{}/api/state", self.url.as_deref().unwrap_or_default());
        match http_json(&url, &[], 3.0) {
            Ok(v) => {
                self.state = v;
                self.ever_ok = true;
                self.demo_mode = false;
                Ok(self.render())
            }
            Err(e) if !self.ever_ok => {
                if !self.demo_mode {
                    self.state = Gpu::demo_state();
                    self.demo_mode = true;
                }
                let mut t = self.render();
                t.demo = Some(format!("unreachable: {e}"));
                Ok(t)
            }
            Err(e) => Err(e),
        }
    }

    fn on_key(&mut self, key: char, row: Option<&Row>) -> Option<Prompt> {
        if key != 's' {
            return None;
        }
        if self.state.is_null() {
            self.state = Gpu::demo_state();
            self.demo_mode = true;
        }
        let id = row?.key.clone()?;
        let g = self.gpus().into_iter().find(|g| g.get("id").map(value_text).as_deref() == Some(id.as_str()))?;
        let active = g.get("active").map(value_text).unwrap_or_default();
        let lanes: Vec<(String, String)> = g
            .get("lanes")?
            .as_array()?
            .iter()
            .map(|l| {
                let lid = l.get("id").map(value_text).unwrap_or_default();
                (lid.clone(), l.get("label").map(value_text).unwrap_or(lid))
            })
            .filter(|(lid, _)| *lid != active)
            .collect();
        if lanes.is_empty() {
            return None;
        }
        let procs = g.get("procs").and_then(|p| p.as_array()).map(|a| a.len()).unwrap_or(0);
        Some(Prompt {
            title: format!("Switch lane: {}", g.get("label").map(value_text).unwrap_or(id.clone())),
            options: lanes.iter().map(|l| l.1.clone()).collect(),
            ids: lanes.iter().map(|l| l.0.clone()).collect(),
            target: id,
            warning: (procs > 0).then(|| format!("{procs} process(es) on this card may be stopped")),
            choice: 0,
        })
    }

    fn confirm(&mut self, p: &Prompt) -> Result<String> {
        let lane = p.ids.get(p.choice).cloned().ok_or_else(|| anyhow!("no lane"))?;
        if self.demo_mode || self.url.is_none() {
            if let Some(gs) = self.state.get_mut("gpus").and_then(|g| g.as_array_mut()) {
                for g in gs {
                    if g.get("id").map(value_text).as_deref() == Some(p.target.as_str()) {
                        g["active"] = json!(lane);
                    }
                }
            }
            return Ok(format!("demo: switched {} to {}", p.target, p.options[p.choice]));
        }
        let base = self.url.clone().unwrap_or_default();
        let token = self.token.clone().ok_or_else(|| anyhow!("lane switching needs token = ... in the tab"))?;
        let local = ["http://localhost", "http://127.0.0.1", "http://[::1]"].iter().any(|p| base.starts_with(p));
        if !base.starts_with("https://") && !local {
            bail!("refusing to send the token over plain http to a remote host");
        }
        let (code, body) = http_post_json(
            &format!("{base}/api/switch"),
            &[("Authorization".into(), format!("Bearer {token}"))],
            &json!({"gpu": p.target, "lane": lane, "force": false}),
            8.0,
        )?;
        if !(200..300).contains(&code) {
            bail!("switch failed: HTTP {code} {}", body.get("error").map(value_text).unwrap_or_default());
        }
        Ok(format!(
            "switching {} to {} (op {})",
            p.target,
            p.options[p.choice],
            body.get("op").map(value_text).unwrap_or_default()
        ))
    }

    fn keys_help(&self) -> &'static str {
        "s  switch the selected card's lane (s/←→ pick, y confirm)"
    }
}
