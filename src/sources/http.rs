//! `http`: HTTP and TCP health checks.

use super::{cfg_f64, cfg_str, Source};
use crate::model::{Cell, Row, Table, Tone};
use crate::util::{headers, http_get, num};
use anyhow::Result;
use serde_json::Value;
use std::collections::{HashMap, VecDeque};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

#[derive(Clone)]
struct Check {
    name: String,
    url: Option<String>,
    tcp: Option<String>,
    expect: Vec<u16>,
    contains: Option<String>,
    headers: Vec<(String, String)>,
    timeout: f64,
}

pub struct Checks {
    checks: Vec<Check>,
    hist: HashMap<String, VecDeque<f64>>,
}

impl Checks {
    pub fn new(cfg: &Value) -> Checks {
        let list = cfg.get("check").and_then(|c| c.as_array()).cloned().unwrap_or_default();
        let checks = list
            .iter()
            .map(|c| {
                let url = cfg_str(c, "url");
                let tcp = cfg_str(c, "tcp");
                let expect = match c.get("expect") {
                    Some(Value::Array(a)) => a.iter().filter_map(num).map(|x| x as u16).collect(),
                    Some(v) => num(v).map(|x| vec![x as u16]).unwrap_or_else(|| vec![200]),
                    None => vec![200],
                };
                Check {
                    name: cfg_str(c, "name")
                        .or_else(|| url.clone())
                        .or_else(|| tcp.clone())
                        .unwrap_or_else(|| "?".into()),
                    url,
                    tcp,
                    expect,
                    contains: cfg_str(c, "contains"),
                    headers: headers(c.get("headers")),
                    timeout: cfg_f64(c, "timeout").unwrap_or(5.0),
                }
            })
            .collect();
        Checks { checks, hist: HashMap::new() }
    }
}

/// (up, code, seconds, why)
fn probe(c: &Check) -> (bool, String, f64, String) {
    let t0 = Instant::now();
    if let Some(addr) = &c.tcp {
        let res = addr
            .to_socket_addrs()
            .map_err(|e| e.to_string())
            .and_then(|mut a| a.next().ok_or_else(|| "no address".to_string()))
            .and_then(|a| {
                TcpStream::connect_timeout(&a, Duration::from_secs_f64(c.timeout)).map_err(|e| e.to_string())
            });
        let dt = t0.elapsed().as_secs_f64();
        return match res {
            Ok(_) => (true, "tcp".into(), dt, String::new()),
            Err(e) => (false, "-".into(), dt, e),
        };
    }
    let Some(url) = &c.url else {
        return (false, "-".into(), 0.0, "check needs url or tcp".into());
    };
    match http_get(url, &c.headers, c.timeout) {
        Ok((code, body)) => {
            let dt = t0.elapsed().as_secs_f64();
            if !c.expect.contains(&code) {
                return (false, code.to_string(), dt, format!("expected {:?}", c.expect));
            }
            if let Some(want) = &c.contains {
                if !body.contains(want.as_str()) {
                    return (false, code.to_string(), dt, format!("body lacks {want:?}"));
                }
            }
            (true, code.to_string(), dt, String::new())
        }
        Err(e) => (false, "-".into(), t0.elapsed().as_secs_f64(), e.to_string()),
    }
}

impl Source for Checks {
    fn configured(&self) -> bool {
        !self.checks.is_empty()
    }

    fn setup_hint(&self) -> Vec<String> {
        vec![
            "one [[tab.check]] per endpoint, for example:".into(),
            "[[tab.check]]".into(),
            "name = \"website\"".into(),
            "url = \"https://example.com/\"".into(),
            "[[tab.check]]".into(),
            "name = \"database\"".into(),
            "tcp = \"localhost:5432\"".into(),
        ]
    }

    fn default_interval(&self) -> f64 {
        15.0
    }

    fn demo(&self) -> Option<Table> {
        let row = |n: &str, up: bool, code: &str, ms: f64, trend: &[f64], target: &str| {
            Row::new(vec![
                Cell::text(n),
                Cell::toned(if up { "UP" } else { "DOWN" }, Some(if up { Tone::Ok } else { Tone::Bad })),
                Cell::text(code),
                Cell::text(format!("{ms:.0} ms")),
                Cell::spark(trend.to_vec(), None, 10),
                Cell::text(target),
            ])
        };
        let mut t = Table::new(
            ["SERVICE", "STATE", "CODE", "LATENCY", "TREND", "TARGET"].map(String::from).to_vec(),
            vec![
                row("api", true, "200", 42.0, &[30.0, 41.0, 38.0, 42.0], "https://api.example.com/health"),
                row("database", true, "tcp", 3.0, &[3.0, 2.0, 3.0, 3.0], "db.example.com:5432"),
                row("worker", false, "503", 120.0, &[80.0, 90.0, 120.0], "http://worker.example.com/ready")
                    .with_detail("expected [200]"),
            ],
        );
        t.header = Some("2/3 up".into());
        Some(t)
    }

    fn fetch(&mut self) -> Result<Table> {
        let results: Vec<(bool, String, f64, String)> = std::thread::scope(|s| {
            let hs: Vec<_> = self.checks.iter().map(|c| s.spawn(move || probe(c))).collect();
            hs.into_iter().map(|h| h.join().unwrap_or((false, "-".into(), 0.0, "panicked".into()))).collect()
        });
        let mut up = 0;
        let mut rows = vec![];
        for (c, (ok, code, dt, why)) in self.checks.iter().zip(results) {
            up += ok as usize;
            let h = self.hist.entry(c.name.clone()).or_default();
            h.push_back(dt * 1000.0);
            if h.len() > 20 {
                h.pop_front();
            }
            rows.push(
                Row::new(vec![
                    Cell::text(&c.name),
                    Cell::toned(if ok { "UP" } else { "DOWN" }, Some(if ok { Tone::Ok } else { Tone::Bad })),
                    Cell::text(code),
                    Cell::text(format!("{:.0} ms", dt * 1000.0)),
                    Cell::spark(h.iter().cloned().collect(), None, 10),
                    Cell::text(c.url.clone().or_else(|| c.tcp.clone()).unwrap_or_default()),
                ])
                .with_detail(why),
            );
        }
        let mut t =
            Table::new(["SERVICE", "STATE", "CODE", "LATENCY", "TREND", "TARGET"].map(String::from).to_vec(), rows);
        t.header = Some(format!("{up}/{} up", self.checks.len()));
        Ok(t)
    }
}
