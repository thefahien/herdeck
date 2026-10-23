//! A tab at runtime: its source, the last table, and a background fetch loop.

use crate::model::Table;
use crate::sources::{self, Prompt, Source};
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct State {
    pub table: Table,
    pub status: String,
    pub demo: bool,
    pub last_ok: Option<Instant>,
    pub last_fetch: Option<Instant>,
    pub fetching: bool,
}

pub struct Tab {
    pub name: String,
    pub interval: f64,
    pub state: Arc<Mutex<State>>,
    pub source: Arc<Mutex<Box<dyn Source>>>,
    pub force: Arc<AtomicBool>,
    pub active: Arc<AtomicBool>,
    pub keys_help: &'static str,
}

impl Tab {
    pub fn from_config(raw: &Value) -> Tab {
        let cfg = crate::config::expand(raw);
        let kind = cfg.get("type").and_then(|t| t.as_str()).unwrap_or("").to_string();
        let name = cfg.get("name").and_then(|n| n.as_str()).map(String::from).unwrap_or_else(|| {
            if kind.is_empty() {
                "tab".into()
            } else {
                kind.clone()
            }
        });
        let source: Box<dyn Source> = match sources::build(&kind, &cfg) {
            Ok(s) => s,
            Err(e) => Box::new(sources::Broken(e.to_string())),
        };
        let interval =
            cfg.get("interval").and_then(crate::util::num).unwrap_or_else(|| source.default_interval()).max(0.5);
        let keys_help = source.keys_help();
        Tab {
            name,
            interval,
            state: Arc::new(Mutex::new(State { status: "loading".into(), ..Default::default() })),
            source: Arc::new(Mutex::new(source)),
            force: Arc::new(AtomicBool::new(true)),
            active: Arc::new(AtomicBool::new(false)),
            keys_help,
        }
    }

    /// Fetch now on this thread (used by --once and by the background loop).
    pub fn refresh(source: &Mutex<Box<dyn Source>>, state: &Mutex<State>) {
        state.lock().unwrap().fetching = true;
        let mut src = source.lock().unwrap();
        let now = Instant::now();
        let (result, configured) = if src.configured() { (Some(src.fetch()), true) } else { (None, false) };
        let demo = if configured || !sources::demo_enabled() { None } else { src.demo() };
        let hint = if configured { vec![] } else { src.setup_hint() };
        drop(src);
        let mut st = state.lock().unwrap();
        st.fetching = false;
        st.last_fetch = Some(now);
        match result {
            None => match demo {
                Some(t) => {
                    st.table = t;
                    st.demo = true;
                    st.status = "DEMO (not configured)".into();
                }
                None => {
                    st.table = Table {
                        header: Some(format!(
                            "Not set up yet. Add this to the tab in {}, then reopen herdeck:",
                            crate::config::pretty(&crate::config::config_path(None))
                        )),
                        columns: vec!["TO DO".into()],
                        rows: hint
                            .into_iter()
                            .map(|l| crate::model::Row::new(vec![crate::model::Cell::text(l)]))
                            .collect(),
                        ..Default::default()
                    };
                    st.status = "not set up".into();
                }
            },
            Some(Ok(t)) => {
                st.demo = t.demo.is_some();
                st.status = match (&t.demo, &t.note) {
                    (Some(why), _) => format!("DEMO ({why})"),
                    (None, Some(n)) => n.clone(),
                    _ => "OK".into(),
                };
                if t.demo.is_none() {
                    st.last_ok = Some(now);
                }
                st.table = t;
            }
            Some(Err(e)) => {
                let msg = crate::util::short_err(&e.to_string());
                st.status = match st.last_ok {
                    Some(ok) => format!("Stale {}s: {msg}", ok.elapsed().as_secs()),
                    None => format!("error: {msg}"),
                };
            }
        }
    }

    /// Fetch whenever forced, at start, or every `interval` while the tab is shown.
    pub fn spawn_loop(&self, stop: Arc<AtomicBool>) {
        let (source, state, force, active, interval) =
            (self.source.clone(), self.state.clone(), self.force.clone(), self.active.clone(), self.interval);
        std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                let due = {
                    let st = state.lock().unwrap();
                    match st.last_fetch {
                        None => true,
                        Some(t) => active.load(Ordering::Relaxed) && t.elapsed().as_secs_f64() >= interval,
                    }
                };
                if due || force.swap(false, Ordering::Relaxed) {
                    Tab::refresh(&source, &state);
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        });
    }

    pub fn key(&self, c: char, row: Option<&crate::model::Row>) -> Option<Prompt> {
        self.source.lock().unwrap().on_key(c, row)
    }

    /// Run a confirmed prompt off the UI thread; the result lands in the status.
    pub fn confirm(&self, p: Prompt) {
        let (source, state, force) = (self.source.clone(), self.state.clone(), self.force.clone());
        state.lock().unwrap().status = "working…".into();
        std::thread::spawn(move || {
            let r = source.lock().unwrap().confirm(&p);
            {
                let src = source.lock().unwrap();
                if !src.configured() && sources::demo_enabled() {
                    if let Some(t) = src.demo() {
                        state.lock().unwrap().table = t;
                    }
                }
            }
            state.lock().unwrap().status = match r {
                Ok(m) => m,
                Err(e) => format!("failed: {}", crate::util::short_err(&e.to_string())),
            };
            std::thread::sleep(Duration::from_millis(800));
            force.store(true, Ordering::Relaxed);
        });
    }
}
