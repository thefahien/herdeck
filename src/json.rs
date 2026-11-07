//! `herdeck --json`: every tab fetched once and printed as one JSON document,
//! for programs that draw the deck themselves (see docs/json.md).

use crate::config;
use crate::tab::Tab;
use serde_json::{json, Map, Value};
use std::io::Write;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::process::ExitCode;
use std::sync::mpsc;
use std::time::{Duration, Instant};

/// Bumped only on a breaking change to the document. Additive fields don't bump it.
pub const SCHEMA: u64 = 1;
pub const DEFAULT_TIMEOUT: f64 = 15.0;

/// `--timeout` is clamped to 1..=120 seconds.
pub fn clamp_timeout(secs: f64) -> f64 {
    if secs.is_nan() {
        DEFAULT_TIMEOUT
    } else {
        secs.clamp(1.0, 120.0)
    }
}

/// What happened to one tab during this call.
#[derive(Clone, Copy, PartialEq, Debug)]
enum Fetch {
    Skipped,
    Done,
    Crashed,
    TimedOut,
}

fn header() -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("schema".into(), json!(SCHEMA));
    m.insert("herdeck".into(), json!(env!("CARGO_PKG_VERSION")));
    m.insert("bin".into(), json!(std::env::current_exe().ok().map(|p| p.display().to_string())));
    m
}

fn emit(doc: Map<String, Value>) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{}", Value::Object(doc));
    let _ = out.flush();
}

fn fatal(kind: &str, message: String, path: Option<String>) -> ExitCode {
    let mut doc = header();
    doc.insert("error".into(), json!({"kind": kind, "message": message, "path": path}));
    emit(doc);
    ExitCode::from(1)
}

/// `--tab N|NAME`: N is 1-based, NAME ignores case (the TUI's lookup, but unknown is an error).
fn find_tab(tabs: &[Tab], want: &str) -> Option<usize> {
    match want.parse::<usize>() {
        Ok(n) => (1..=tabs.len()).contains(&n).then(|| n - 1),
        Err(_) => tabs.iter().position(|t| t.name.eq_ignore_ascii_case(want)),
    }
}

/// Fetch the wanted tabs in parallel, give up on stragglers at `timeout` seconds.
fn fetch(tabs: &[Tab], only: Option<usize>, timeout: f64) -> Vec<Fetch> {
    let mut result: Vec<Fetch> =
        (0..tabs.len()).map(|i| if only.is_none_or(|o| o == i) { Fetch::TimedOut } else { Fetch::Skipped }).collect();
    let (tx, rx) = mpsc::channel::<(usize, bool)>();
    let mut pending = 0;
    for (i, t) in tabs.iter().enumerate() {
        if result[i] == Fetch::Skipped {
            continue;
        }
        pending += 1;
        let (source, state, tx) = (t.source.clone(), t.state.clone(), tx.clone());
        std::thread::spawn(move || {
            let ok = catch_unwind(AssertUnwindSafe(|| Tab::refresh(&source, &state))).is_ok();
            let _ = tx.send((i, ok));
        });
    }
    drop(tx);
    let deadline = Instant::now() + Duration::from_secs_f64(timeout);
    while pending > 0 {
        match rx.recv_timeout(deadline.saturating_duration_since(Instant::now())) {
            Ok((i, ok)) => {
                result[i] = if ok { Fetch::Done } else { Fetch::Crashed };
                pending -= 1;
            }
            Err(_) => break,
        }
    }
    result
}

/// Rows a tab can act on (`s` switches a GPU lane) get `actions`, so a program that draws the deck
/// can offer them; `herdeck --act` performs one.
fn add_actions(t: &Tab, rows: &[crate::model::Row], table: &mut Value) {
    let mut src = t.source.lock().unwrap();
    let keys = src.action_keys();
    if keys.is_empty() {
        return;
    }
    let Some(list) = table.get_mut("rows").and_then(|r| r.as_array_mut()) else { return };
    for (row, out) in rows.iter().zip(list.iter_mut()) {
        if row.key.is_none() {
            continue;
        }
        let acts: Vec<Value> = keys
            .iter()
            .filter_map(|k| src.on_key(*k, Some(row)).map(|p| (*k, p)))
            .map(|(k, p)| {
                json!({
                    "key": k.to_string(),
                    "title": p.title,
                    "warning": p.warning,
                    "options": p.ids.iter().zip(&p.options).map(|(id, label)| json!({"id": id, "label": label})).collect::<Vec<_>>(),
                })
            })
            .collect();
        if !acts.is_empty() {
            out["actions"] = Value::Array(acts);
        }
    }
}

fn tab_json(index: usize, t: &Tab, fetched: Fetch, timeout: f64) -> Value {
    let (state, status, message, table) = match fetched {
        Fetch::Skipped => ("skipped", "skipped".to_string(), None, None),
        Fetch::TimedOut => (
            "timeout",
            "timeout".to_string(),
            Some(format!("no answer within {}s", crate::model::trim_float(timeout))),
            None,
        ),
        Fetch::Crashed => ("error", "error: tab crashed".to_string(), Some("tab crashed".to_string()), None),
        Fetch::Done => {
            let st = t.state.lock().unwrap();
            let table = (st.outcome != "error").then(|| {
                let mut v = st.table.to_json();
                add_actions(t, &st.table.rows, &mut v);
                v
            });
            (if st.outcome.is_empty() { "error" } else { st.outcome }, st.status.clone(), st.message.clone(), table)
        }
    };
    json!({
        "index": index + 1,
        "name": t.name,
        "type": t.kind,
        "interval": t.interval,
        "state": state,
        "status": status,
        "message": message,
        "table": table,
    })
}

fn load_deck(config_arg: Option<&str>, demo: bool) -> Result<config::Deck, ExitCode> {
    let path = config::config_path(config_arg);
    let shown = config::pretty(&path);
    if !path.exists() {
        if config_arg.is_none() && demo {
            Ok(config::demo_deck())
        } else {
            Err(fatal("no_deck", format!("no deck at {shown}"), Some(shown)))
        }
    } else {
        config::load(config_arg).map_err(|e| fatal("config", e.to_string(), Some(shown)))
    }
}

/// `--act TAB ROW KEY CHOICE`: fetch the tab, press KEY on the row with that key, confirm CHOICE.
/// Prints `{schema, ok, message}`; exit 1 when it failed.
pub fn act(config_arg: Option<&str>, tab: &str, row_key: &str, key: &str, choice: &str, demo: bool) -> ExitCode {
    let fail = |m: String| {
        let mut doc = header();
        doc.insert("ok".into(), json!(false));
        doc.insert("message".into(), json!(m));
        emit(doc);
        ExitCode::from(1)
    };
    let deck = match load_deck(config_arg, demo) {
        Ok(d) => d,
        Err(c) => return c,
    };
    let tabs: Vec<Tab> = deck.tabs.iter().map(Tab::from_config).collect();
    let Some(i) = find_tab(&tabs, tab) else { return fail(format!("no tab {tab:?}")) };
    let Some(k) = key.chars().next().filter(|_| key.chars().count() == 1) else {
        return fail("KEY is one character".into());
    };
    let t = &tabs[i];
    if !t.source.lock().unwrap().action_keys().contains(&k) {
        return fail(format!("{key:?} is not an action of that tab"));
    }
    Tab::refresh(&t.source, &t.state);
    let (row, why) = {
        let st = t.state.lock().unwrap();
        let row = st.table.rows.iter().find(|r| r.key.as_deref() == Some(row_key)).cloned();
        (row, if st.outcome == "error" { st.message.clone() } else { None })
    };
    let Some(row) = row else {
        return fail(match why {
            Some(m) => format!("no row {row_key:?}: {}", crate::util::short_err(&m)),
            None => format!("no row {row_key:?}"),
        });
    };
    let mut src = t.source.lock().unwrap();
    let Some(mut prompt) = src.on_key(k, Some(&row)) else { return fail(format!("{key:?} does nothing on that row")) };
    let Some(idx) = prompt.ids.iter().position(|id| id == choice) else {
        return fail(format!("{choice:?} is not one of: {}", prompt.ids.join(", ")));
    };
    prompt.choice = idx;
    let result = src.confirm(&prompt);
    drop(src);
    let mut doc = header();
    match result {
        Ok(m) => {
            doc.insert("ok".into(), json!(true));
            doc.insert("message".into(), json!(m));
            emit(doc);
            ExitCode::SUCCESS
        }
        Err(e) => fail(crate::util::short_err(&e.to_string())),
    }
}

pub fn run(config_arg: Option<&str>, tab: Option<&str>, demo: bool, timeout: f64) -> ExitCode {
    let timeout = clamp_timeout(timeout);
    let deck = match load_deck(config_arg, demo) {
        Ok(d) => d,
        Err(c) => return c,
    };
    let tabs: Vec<Tab> = deck.tabs.iter().map(Tab::from_config).collect();
    let only = match tab {
        None => None,
        Some(want) => match find_tab(&tabs, want) {
            Some(i) => Some(i),
            None => {
                let msg = format!(
                    "no tab {want:?} in {} (it has {} tabs)",
                    config::pretty(Path::new(&deck.source)),
                    tabs.len()
                );
                return fatal("no_such_tab", msg, None);
            }
        },
    };
    let fetched = fetch(&tabs, only, timeout);
    let mut doc = header();
    doc.insert("deck".into(), json!(config::pretty(Path::new(&deck.source))));
    doc.insert("demo".into(), json!(demo));
    let list: Vec<Value> =
        tabs.iter().zip(&fetched).enumerate().map(|(i, (t, f))| tab_json(i, t, *f, timeout)).collect();
    doc.insert("tabs".into(), Value::Array(list));
    emit(doc);
    if fetched.contains(&Fetch::TimedOut) {
        // abandon the stuck fetch threads
        std::process::exit(0);
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tab(v: Value) -> Tab {
        Tab::from_config(&v)
    }

    fn deck() -> Vec<Tab> {
        vec![
            tab(json!({"name": "Fine", "type": "command", "command": "echo hi"})),
            tab(json!({"name": "Broken", "type": "command", "command": "exit 3"})),
            tab(json!({"name": "Slow", "type": "command", "command": "sleep 30"})),
            tab(json!({"name": "Blank", "type": "http"})),
        ]
    }

    #[test]
    fn timeout_is_clamped() {
        assert_eq!(clamp_timeout(0.0), 1.0);
        assert_eq!(clamp_timeout(15.0), 15.0);
        assert_eq!(clamp_timeout(1e9), 120.0);
        assert_eq!(clamp_timeout(f64::NAN), DEFAULT_TIMEOUT);
    }

    #[test]
    fn tab_lookup() {
        let tabs = deck();
        assert_eq!(find_tab(&tabs, "1"), Some(0));
        assert_eq!(find_tab(&tabs, "4"), Some(3));
        assert_eq!(find_tab(&tabs, "0"), None);
        assert_eq!(find_tab(&tabs, "5"), None);
        assert_eq!(find_tab(&tabs, "broken"), Some(1));
        assert_eq!(find_tab(&tabs, "nope"), None);
    }

    #[test]
    fn states_per_tab() {
        let tabs = deck();
        let started = Instant::now();
        let fetched = fetch(&tabs, None, 1.0);
        assert!(started.elapsed() < Duration::from_secs(5));
        let docs: Vec<Value> =
            tabs.iter().zip(&fetched).enumerate().map(|(i, (t, f))| tab_json(i, t, *f, 1.0)).collect();
        let states: Vec<&str> = docs.iter().map(|d| d["state"].as_str().unwrap()).collect();
        assert_eq!(states, ["ok", "error", "timeout", "not_set_up"]);
        assert_eq!(docs[0]["index"], 1);
        assert_eq!(docs[0]["type"], "command");
        assert_eq!(docs[0]["status"], "OK");
        assert_eq!(docs[0]["message"], Value::Null);
        assert_eq!(docs[0]["table"]["rows"][0]["cells"][0]["text"], "hi");
        assert_eq!(docs[1]["status"], "error: exit 3");
        assert_eq!(docs[1]["message"], "exit 3");
        assert_eq!(docs[1]["table"], Value::Null);
        assert_eq!(docs[2]["status"], "timeout");
        assert_eq!(docs[2]["message"], "no answer within 1s");
        assert_eq!(docs[2]["table"], Value::Null);
        assert_eq!(docs[3]["status"], "not set up");
        assert!(docs[3]["message"].as_str().unwrap().starts_with("Not set up yet."));
        assert_eq!(docs[3]["table"]["columns"], json!(["TO DO"]));
    }

    #[test]
    fn only_one_tab_is_fetched() {
        let tabs = deck();
        let fetched = fetch(&tabs, Some(0), 1.0);
        assert_eq!(fetched, [Fetch::Done, Fetch::Skipped, Fetch::Skipped, Fetch::Skipped]);
        let skipped = tab_json(1, &tabs[1], Fetch::Skipped, 1.0);
        assert_eq!(skipped["state"], "skipped");
        assert_eq!(skipped["status"], "skipped");
        assert_eq!(skipped["table"], Value::Null);
        assert_eq!(skipped["index"], 2);
    }

    struct Fake;
    impl crate::sources::Source for Fake {
        fn fetch(&mut self) -> anyhow::Result<crate::model::Table> {
            Ok(crate::model::Table::default())
        }
        fn action_keys(&self) -> &'static [char] {
            &['x']
        }
        fn on_key(&mut self, key: char, row: Option<&crate::model::Row>) -> Option<crate::sources::Prompt> {
            let id = row?.key.clone()?;
            (key == 'x').then(|| crate::sources::Prompt {
                title: format!("Do x on {id}"),
                options: vec!["One".into(), "Two".into()],
                ids: vec!["1".into(), "2".into()],
                target: id,
                warning: Some("careful".into()),
                choice: 0,
            })
        }
    }

    #[test]
    fn keyed_rows_carry_actions() {
        use crate::model::{Cell, Row, Table};
        use std::sync::{Arc, Mutex};
        let mut t = tab(json!({"name": "A", "type": "command", "command": "true"}));
        t.source = Arc::new(Mutex::new(Box::new(Fake)));
        let rows =
            vec![Row::new(vec![Cell::text("a")]).with_key("g0"), Row::new(vec![Cell::text("b")]), Row::section("s")];
        let mut v = Table::new(vec!["C".into()], rows.clone()).to_json();
        add_actions(&t, &rows, &mut v);
        let r = &v["rows"];
        assert_eq!(r[0]["actions"][0]["key"], "x");
        assert_eq!(r[0]["actions"][0]["title"], "Do x on g0");
        assert_eq!(r[0]["actions"][0]["warning"], "careful");
        assert_eq!(r[0]["actions"][0]["options"], json!([{"id": "1", "label": "One"}, {"id": "2", "label": "Two"}]));
        assert!(r[1].get("actions").is_none());
        assert!(r[2].get("actions").is_none());
        // a tab without action keys adds nothing
        let plain = tab(json!({"name": "P", "type": "command", "command": "true"}));
        let mut v2 = Table::new(vec!["C".into()], rows.clone()).to_json();
        add_actions(&plain, &rows, &mut v2);
        assert!(v2["rows"][0].get("actions").is_none());
    }
}
