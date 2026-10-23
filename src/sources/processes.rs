//! `processes`: local processes, filtered and sorted.

use super::{cfg_f64, cfg_str, Source};
use crate::model::{Cell, Row, Table};
use anyhow::Result;
use regex::Regex;
use serde_json::Value;

pub struct Processes {
    pattern: Option<Regex>,
    pattern_src: Option<String>,
    user: Option<String>,
    sort: String,
    limit: usize,
}

impl Processes {
    pub fn new(cfg: &Value) -> Processes {
        let pattern_src = cfg_str(cfg, "match");
        Processes {
            pattern: pattern_src.as_ref().and_then(|p| Regex::new(&format!("(?i){p}")).ok()),
            pattern_src,
            user: cfg_str(cfg, "user"),
            sort: cfg_str(cfg, "sort").unwrap_or_else(|| "cpu".into()),
            limit: cfg_f64(cfg, "limit").unwrap_or(50.0) as usize,
        }
    }
}

struct P {
    pid: u32,
    user: String,
    cpu: f64,
    mem: f64,
    etime: String,
    args: String,
}

impl Source for Processes {
    fn default_interval(&self) -> f64 {
        3.0
    }

    fn fetch(&mut self) -> Result<Table> {
        let argv: Vec<String> =
            ["ps", "-axo", "pid=,user=,pcpu=,pmem=,etime=,args="].iter().map(|s| s.to_string()).collect();
        let (_, out, _) = super::command::run(&argv, 10.0)?;
        let me = std::process::id();
        let mut procs: Vec<P> = out
            .lines()
            .filter_map(|l| {
                let mut it = l.split_whitespace();
                let pid: u32 = it.next()?.parse().ok()?;
                let user = it.next()?.to_string();
                let cpu = it.next()?.parse().ok()?;
                let mem = it.next()?.parse().ok()?;
                let etime = it.next()?.to_string();
                let args = it.collect::<Vec<_>>().join(" ");
                Some(P { pid, user, cpu, mem, etime, args })
            })
            .filter(|p| p.pid != me)
            .filter(|p| self.pattern.as_ref().is_none_or(|re| re.is_match(&p.args)))
            .filter(|p| self.user.as_ref().is_none_or(|u| &p.user == u))
            .collect();
        match self.sort.as_str() {
            "mem" => procs.sort_by(|a, b| b.mem.total_cmp(&a.mem)),
            "pid" => procs.sort_by_key(|p| p.pid),
            _ => procs.sort_by(|a, b| b.cpu.total_cmp(&a.cpu)),
        }
        procs.truncate(self.limit);
        let rows = procs
            .iter()
            .map(|p| {
                let first = p.args.split_whitespace().next().unwrap_or("");
                // kernel threads look like [kworker/0:1]; keep those whole
                let comm = if first.starts_with('/') { first.rsplit('/').next().unwrap_or(first) } else { first };
                Row::new(vec![
                    Cell::text(p.pid.to_string()),
                    Cell::text(p.user.chars().take(12).collect::<String>()),
                    Cell::bar(p.cpu, 100.0, Some(format!("{:.0}%", p.cpu)), 8),
                    Cell::bar(p.mem, 100.0, Some(format!("{:.1}%", p.mem)), 8),
                    Cell::text(&p.etime),
                    Cell::text(comm),
                ])
                .with_detail(p.args.clone())
                .with_key(p.pid.to_string())
            })
            .collect::<Vec<_>>();
        let what = self.pattern_src.as_ref().map(|p| format!("matching /{p}/")).unwrap_or_else(|| "all".into());
        let mut t = Table::new(["PID", "USER", "CPU", "MEM", "UP", "COMMAND"].map(String::from).to_vec(), rows);
        t.header = Some(format!("{} processes ({what}), sorted by {}", t.rows.len(), self.sort));
        Ok(t)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn lists_processes() {
        let t = Processes::new(&json!({"limit": 5})).fetch().unwrap();
        assert!(!t.rows.is_empty() && t.rows.len() <= 5);
    }
}
