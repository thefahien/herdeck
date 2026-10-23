//! The interactive deck: tab bar, table, footer, help and prompt overlays.

use crate::model::{Row, Tone};
use crate::sources::Prompt;
use crate::tab::Tab;
use anyhow::Result;
use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton,
    MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::{Constraint, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph, Row as TRow, Table as TTable, TableState, Wrap};
use ratatui::{DefaultTerminal, Frame};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

pub fn tone_style(t: Option<Tone>) -> Style {
    match t {
        Some(Tone::Ok) => Style::new().fg(Color::Green),
        Some(Tone::Warn) => Style::new().fg(Color::Yellow),
        Some(Tone::Bad) => Style::new().fg(Color::Red).add_modifier(Modifier::BOLD),
        Some(Tone::Accent) => Style::new().fg(Color::Cyan),
        Some(Tone::Dim) => Style::new().fg(Color::DarkGray),
        None => Style::new(),
    }
}

struct View {
    sel: usize,
    state: TableState,
    body_top: u16,
    visible: usize,
}

pub struct App {
    tabs: Vec<Tab>,
    views: Vec<View>,
    active: usize,
    help: bool,
    prompt: Option<Prompt>,
    tab_spans: Vec<(u16, u16, usize)>,
    refresh: f64,
    stop: Arc<AtomicBool>,
}

impl App {
    pub fn new(tabs: Vec<Tab>, start: usize, refresh: f64) -> App {
        let views =
            tabs.iter().map(|_| View { sel: 0, state: TableState::default(), body_top: 0, visible: 1 }).collect();
        App {
            tabs,
            views,
            active: start,
            help: false,
            prompt: None,
            tab_spans: vec![],
            refresh,
            stop: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn run(mut self) -> Result<()> {
        for t in &self.tabs {
            t.spawn_loop(self.stop.clone());
        }
        self.activate(self.active);
        let mut term = ratatui::init();
        let hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            let _ = restore();
            hook(info);
        }));
        execute!(std::io::stdout(), EnableMouseCapture)?;
        let res = self.event_loop(&mut term);
        self.stop.store(true, Ordering::Relaxed);
        restore()?;
        res
    }

    fn activate(&mut self, i: usize) {
        if i >= self.tabs.len() {
            return;
        }
        for (j, t) in self.tabs.iter().enumerate() {
            t.active.store(j == i, Ordering::Relaxed);
        }
        if i != self.active {
            self.tabs[i].force.store(true, Ordering::Relaxed);
        }
        self.active = i;
    }

    fn event_loop(&mut self, term: &mut DefaultTerminal) -> Result<()> {
        loop {
            term.draw(|f| self.draw(f))?;
            if !event::poll(Duration::from_secs_f64(self.refresh))? {
                continue;
            }
            match event::read()? {
                Event::Key(k) if k.kind != KeyEventKind::Release => {
                    if !self.on_key(k) {
                        return Ok(());
                    }
                }
                Event::Mouse(m) => self.on_mouse(m.kind, m.column, m.row),
                _ => {}
            }
        }
    }

    fn rows_len(&self) -> usize {
        self.tabs[self.active].state.lock().unwrap().table.rows.len()
    }

    fn selected_row(&self) -> Option<Row> {
        let st = self.tabs[self.active].state.lock().unwrap();
        st.table.rows.get(self.views[self.active].sel).cloned()
    }

    fn move_sel(&mut self, delta: i64) {
        let n = self.rows_len() as i64;
        if n == 0 {
            return;
        }
        let v = &mut self.views[self.active];
        v.sel = (v.sel as i64 + delta).clamp(0, n - 1) as usize;
    }

    /// Returns false to quit.
    fn on_key(&mut self, k: KeyEvent) -> bool {
        if k.modifiers.contains(KeyModifiers::CONTROL) && k.code == KeyCode::Char('c') {
            return false;
        }
        if let Some(p) = self.prompt.as_mut() {
            match k.code {
                KeyCode::Char('y') | KeyCode::Char('Y') | KeyCode::Enter => {
                    let p = self.prompt.take().unwrap();
                    self.tabs[self.active].confirm(p);
                }
                KeyCode::Char('n') | KeyCode::Char('q') | KeyCode::Esc => self.prompt = None,
                KeyCode::Char('s') | KeyCode::Tab | KeyCode::Right | KeyCode::Down | KeyCode::Char('j') => {
                    p.choice = (p.choice + 1) % p.options.len().max(1)
                }
                KeyCode::Left | KeyCode::Up | KeyCode::Char('k') | KeyCode::BackTab => {
                    p.choice = (p.choice + p.options.len().max(1) - 1) % p.options.len().max(1)
                }
                _ => {}
            }
            return true;
        }
        if self.help {
            self.help = false;
            return true;
        }
        let n = self.tabs.len();
        let page = self.views[self.active].visible.max(1) as i64;
        match k.code {
            KeyCode::Char('q') | KeyCode::Esc => return false,
            KeyCode::Tab | KeyCode::Char(']') => self.activate((self.active + 1) % n),
            KeyCode::BackTab | KeyCode::Char('[') => self.activate((self.active + n - 1) % n),
            KeyCode::Char(c @ '1'..='9') => self.activate(c as usize - '1' as usize),
            KeyCode::Down | KeyCode::Char('j') => self.move_sel(1),
            KeyCode::Up | KeyCode::Char('k') => self.move_sel(-1),
            KeyCode::PageDown => self.move_sel(page),
            KeyCode::PageUp => self.move_sel(-page),
            KeyCode::Home | KeyCode::Char('g') => self.views[self.active].sel = 0,
            KeyCode::End | KeyCode::Char('G') => self.move_sel(i64::MAX / 2),
            KeyCode::Char('r') => self.tabs[self.active].force.store(true, Ordering::Relaxed),
            KeyCode::Char('?') => self.help = true,
            KeyCode::Char(c) => {
                let row = self.selected_row();
                if let Some(p) = self.tabs[self.active].key(c, row.as_ref()) {
                    self.prompt = Some(p);
                }
            }
            _ => {}
        }
        true
    }

    fn on_mouse(&mut self, kind: MouseEventKind, x: u16, y: u16) {
        match kind {
            MouseEventKind::ScrollDown => self.move_sel(1),
            MouseEventKind::ScrollUp => self.move_sel(-1),
            MouseEventKind::Down(MouseButton::Left) => {
                if self.help {
                    self.help = false;
                } else if y == 0 {
                    if let Some(&(_, _, i)) = self.tab_spans.iter().find(|(a, b, _)| x >= *a && x < *b) {
                        self.activate(i);
                    }
                } else if self.prompt.is_none() {
                    let v = &self.views[self.active];
                    if y >= v.body_top && ((y - v.body_top) as usize) < v.visible {
                        let i = v.state.offset() + (y - v.body_top) as usize;
                        if i < self.rows_len() {
                            self.views[self.active].sel = i;
                        }
                    }
                }
            }
            _ => {}
        }
    }

    fn draw(&mut self, f: &mut Frame) {
        let area = f.area();
        if area.height < 3 || area.width < 10 {
            f.render_widget(Paragraph::new("herdeck: too small"), area);
            return;
        }
        self.draw_tabs(f, Rect { height: 1, ..area });
        let st = self.tabs[self.active].state.lock().unwrap();
        let status = st.status.clone();
        let table = st.table.clone();
        drop(st);
        let body = Rect { x: area.x, y: area.y + 1, width: area.width, height: area.height - 2 };
        let mut y = body.y;
        if let Some(h) = &table.header {
            f.render_widget(
                Paragraph::new(Span::styled(
                    format!(" {h}"),
                    Style::new().fg(Color::Cyan).add_modifier(Modifier::BOLD),
                )),
                Rect { y, height: 1, ..body },
            );
            y += 1;
        }
        let sel = self.views[self.active].sel.min(table.rows.len().saturating_sub(1));
        self.views[self.active].sel = sel;
        let detail = table.rows.get(sel).and_then(|r| r.detail.clone());
        let bottom = body.y + body.height;
        let table_h = bottom.saturating_sub(y).saturating_sub(if detail.is_some() { 1 } else { 0 });
        if table.rows.is_empty() {
            let msg = if status == "loading" { "loading…".to_string() } else { status.clone() };
            f.render_widget(
                Paragraph::new(Span::styled(format!("  {msg}"), Style::new().fg(Color::Yellow)))
                    .wrap(Wrap { trim: false }),
                Rect { y: y + 1, height: table_h.saturating_sub(1), ..body },
            );
        } else {
            let widths = col_widths(&table.columns, &table.rows, body.width);
            let header = TRow::new(
                table
                    .columns
                    .iter()
                    .map(|c| Span::styled(c.clone(), Style::new().fg(Color::Cyan).add_modifier(Modifier::DIM))),
            );
            let rows: Vec<TRow> = table
                .rows
                .iter()
                .map(|r| match &r.section {
                    Some(t) => TRow::new(vec![Span::styled(
                        format!("── {t}"),
                        Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD),
                    )]),
                    None => TRow::new(r.cells.iter().map(|c| Span::styled(c.render(), tone_style(c.tone())))),
                })
                .collect();
            let t = TTable::new(rows, widths.iter().map(|w| Constraint::Length(*w)).collect::<Vec<_>>())
                .header(header)
                .column_spacing(2)
                .row_highlight_style(Style::new().add_modifier(Modifier::REVERSED));
            let v = &mut self.views[self.active];
            v.state.select(Some(sel));
            v.body_top = y + 1;
            v.visible = table_h.saturating_sub(1) as usize;
            let r = Rect { x: body.x + 1, y, width: body.width.saturating_sub(1), height: table_h };
            f.render_stateful_widget(t, r, &mut v.state);
            // a section title spans the whole row; a table cell would cut it at the
            // first column's width, so draw it over the row after the table
            let offset = v.state.offset();
            for (i, row) in table.rows.iter().enumerate().skip(offset).take(v.visible) {
                if let Some(title) = &row.section {
                    let mut style = Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD);
                    if i == sel {
                        style = style.add_modifier(Modifier::REVERSED);
                    }
                    let ry = y + 1 + (i - offset) as u16;
                    f.render_widget(
                        Paragraph::new(Span::styled(format!("── {title}"), style)),
                        Rect { x: r.x, y: ry, width: r.width, height: 1 },
                    );
                }
            }
            if table.rows.len() > v.visible {
                let pos = format!(" {}/{} ", sel + 1, table.rows.len());
                let w = pos.chars().count() as u16;
                f.render_widget(
                    Paragraph::new(Span::styled(pos, Style::new().fg(Color::DarkGray))),
                    Rect { x: area.width.saturating_sub(w + 1), y: body.y, width: w, height: 1 },
                );
            }
        }
        if let Some(d) = detail {
            f.render_widget(
                Paragraph::new(Span::styled(format!(" {d}"), Style::new().fg(Color::Yellow))),
                Rect { y: bottom - 1, height: 1, ..body },
            );
        }
        self.draw_footer(f, Rect { y: area.y + area.height - 1, height: 1, ..area }, &status);
        if self.help {
            self.draw_help(f, area);
        }
        if let Some(p) = &self.prompt {
            draw_prompt(f, area, p);
        }
    }

    fn draw_tabs(&mut self, f: &mut Frame, r: Rect) {
        self.tab_spans.clear();
        let clock = chrono_now();
        let room = r.width.saturating_sub(clock.len() as u16 + 2);
        let labels: Vec<String> = self
            .tabs
            .iter()
            .enumerate()
            .map(|(i, t)| if i < 9 { format!(" {}:{} ", i + 1, t.name) } else { format!(" {} ", t.name) })
            .collect();
        let mut first = 0;
        while first < self.active
            && labels[first..=self.active].iter().map(|l| l.chars().count() as u16 + 1).sum::<u16>() + 2 > room
        {
            first += 1;
        }
        let mut spans = vec![];
        let mut x = r.x;
        if first > 0 {
            spans.push(Span::styled("‹", Style::new().fg(Color::DarkGray)));
            x += 1;
        }
        for (i, label) in labels.iter().enumerate().skip(first) {
            let w = label.chars().count() as u16;
            if x + w > r.x + room {
                spans.push(Span::styled("›", Style::new().fg(Color::DarkGray)));
                break;
            }
            let active = i == self.active;
            let text = if active { format!("●{}", &label[1..]) } else { label.clone() };
            let style = if active {
                Style::new().fg(Color::Black).bg(Color::Green).add_modifier(Modifier::BOLD)
            } else {
                Style::new()
            };
            spans.push(Span::styled(text, style));
            spans.push(Span::raw(" "));
            self.tab_spans.push((x, x + w, i));
            x += w + 1;
        }
        f.render_widget(Paragraph::new(Line::from(spans)), r);
        let cw = clock.len() as u16;
        f.render_widget(
            Paragraph::new(Span::styled(clock, Style::new().fg(Color::Cyan))),
            Rect { x: r.x + r.width.saturating_sub(cw + 1), width: cw, ..r },
        );
    }

    fn draw_footer(&self, f: &mut Frame, r: Rect, status: &str) {
        let status: String = format!(" [{status}] ").chars().take((r.width / 2).max(10) as usize).collect();
        let sw = status.chars().count() as u16;
        let hint = " Tab/1-9/click: tabs · j/k/wheel: rows · r: refresh · ?: help · q: close";
        let room = r.width.saturating_sub(sw + 1) as usize;
        f.render_widget(
            Paragraph::new(Span::styled(hint.chars().take(room).collect::<String>(), Style::new().fg(Color::DarkGray))),
            r,
        );
        let style = if status.contains("DEMO") || status.contains("Stale") {
            Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD)
        } else if status.contains("error") || status.contains("failed") {
            Style::new().fg(Color::Red).add_modifier(Modifier::BOLD)
        } else {
            Style::new().fg(Color::Cyan)
        };
        f.render_widget(
            Paragraph::new(Span::styled(status, style)),
            Rect { x: r.x + r.width.saturating_sub(sw), width: sw, ..r },
        );
    }

    fn draw_help(&self, f: &mut Frame, area: Rect) {
        let mut lines = vec![
            "Tab ] / Shift+Tab [     next / previous tab",
            "1 - 9, click            jump to a tab",
            "j k, arrows, wheel      move selection; click selects",
            "PgUp PgDn, g G          page, top, bottom",
            "r                       refresh this tab now",
            "q / Esc                 close",
        ];
        let extra = self.tabs[self.active].keys_help;
        if !extra.is_empty() {
            lines.push("");
            lines.push(extra);
        }
        lines.push("");
        lines.push("Tabs come from ~/.config/herdeck/tabs.toml (herdeck --types)");
        let w = (lines.iter().map(|l| l.chars().count()).max().unwrap_or(40) as u16 + 4).min(area.width);
        let h = (lines.len() as u16 + 2).min(area.height);
        let r = centered(area, w, h);
        f.render_widget(Clear, r);
        let text: Vec<Line> = lines.iter().map(|l| Line::from(format!(" {l}"))).collect();
        f.render_widget(Paragraph::new(text).block(Block::default().borders(Borders::ALL).title(" herdeck ")), r);
    }
}

fn draw_prompt(f: &mut Frame, area: Rect, p: &Prompt) {
    let mut lines = vec![Line::from("")];
    let opts: Vec<Span> = p
        .options
        .iter()
        .enumerate()
        .flat_map(|(i, o)| {
            let s = if i == p.choice {
                Style::new().fg(Color::Black).bg(Color::Green).add_modifier(Modifier::BOLD)
            } else {
                Style::new()
            };
            vec![Span::styled(format!(" {o} "), s), Span::raw(" ")]
        })
        .collect();
    lines.push(Line::from([vec![Span::raw(" to: ")], opts].concat()));
    if let Some(w) = &p.warning {
        lines.push(Line::from(Span::styled(format!(" {w}"), Style::new().fg(Color::Yellow))));
    }
    lines.push(Line::from(""));
    lines.push(Line::from(" y confirm · n/Esc cancel · s/←→ pick"));
    let r = centered(area, 64.min(area.width), (lines.len() as u16 + 2).min(area.height));
    f.render_widget(Clear, r);
    f.render_widget(
        Paragraph::new(lines).block(Block::default().borders(Borders::ALL).title(format!(" {} ", p.title))),
        r,
    );
}

fn centered(area: Rect, w: u16, h: u16) -> Rect {
    Rect {
        x: area.x + area.width.saturating_sub(w) / 2,
        y: area.y + area.height.saturating_sub(h) / 2,
        width: w,
        height: h,
    }
}

fn restore() -> Result<()> {
    execute!(std::io::stdout(), DisableMouseCapture)?;
    ratatui::restore();
    Ok(())
}

/// Column widths: content width capped at 60, widest columns shrunk first to fit.
pub fn col_widths(cols: &[String], rows: &[Row], total: u16) -> Vec<u16> {
    let n = cols.len().max(rows.iter().map(|r| r.cells.len()).max().unwrap_or(0));
    let mut w: Vec<usize> = (0..n).map(|i| cols.get(i).map(|c| c.chars().count()).unwrap_or(0)).collect();
    for r in rows.iter().filter(|r| r.section.is_none()).take(500) {
        for (i, c) in r.cells.iter().enumerate() {
            w[i] = w[i].max(c.render().chars().count());
        }
    }
    let mut w: Vec<usize> = w.into_iter().map(|x| x.min(60)).collect();
    let mut over = (w.iter().sum::<usize>() + 2 * n + 2) as i64 - total as i64;
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by_key(|i| std::cmp::Reverse(w[*i]));
    for i in order {
        if over <= 0 {
            break;
        }
        let cut = (over as usize).min(w[i].saturating_sub(6));
        w[i] -= cut;
        over -= cut as i64;
    }
    w.into_iter().map(|x| x as u16).collect()
}

/// HH:MM:SS local time. The UTC offset is read once from `date +%z`
/// (no date crate, no libc calls).
fn chrono_now() -> String {
    static OFFSET: std::sync::OnceLock<i64> = std::sync::OnceLock::new();
    let off = *OFFSET.get_or_init(|| {
        std::process::Command::new("date")
            .arg("+%z")
            .output()
            .ok()
            .and_then(|o| {
                let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
                let sign = if s.starts_with('-') { -1 } else { 1 };
                let d = s.trim_start_matches(['+', '-']);
                let h: i64 = d.get(0..2)?.parse().ok()?;
                let m: i64 = d.get(2..4)?.parse().ok()?;
                Some(sign * (h * 3600 + m * 60))
            })
            .unwrap_or(0)
    });
    let now =
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
            + off;
    let s = now.rem_euclid(86400);
    format!("{:02}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
}
