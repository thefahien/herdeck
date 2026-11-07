// herdeck: your herdeck deck (herdeck --json) as a native panel. A pane, a tab, an overlay or a docked block.
// Props (all optional): bin, config, machine ("follow" = the shown space's), demo, block, fixture, width, active.
// Runs `herdeck` only through `sh -c LAUNCH`; see RECIPE.md.

import { View, div } from "gpui-kit";
import { h_flex, v_flex, v_virtual_list, Scrollbar, VirtualListScrollHandle } from "gpui-base";
import { exec, props, theme, settings, window, layout, spaces, run, closeOverlay, nextCommand } from "herdr";
import {
  GAP, PAD, ROW, SPARK_H, CH, columnLayout, detailText, sparkHeights, barPct, toneColor, interpret, clock,
} from "./model.js";

const SCHEMA = 1, NEEDS = "0.4.0";
const SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

// One exec through sh, so PATH problems never reach JS. $1 = explicit bin path (may be empty).
const LAUNCH = `b=$1; shift
conf="\${XDG_CONFIG_HOME:-$HOME/.config}/herdeck/env"
if [ -f "$conf" ]; then set -a; . "$conf"; set +a; fi
for c in "$b" "$HERDECK_BIN" "$(command -v herdeck 2>/dev/null)" "$HOME/.local/bin/herdeck" "$HOME/.cargo/bin/herdeck" \\
  /opt/homebrew/bin/herdeck /usr/local/bin/herdeck; do
  if [ -n "$c" ] && [ -x "$c" ]; then exec "$c" "$@"; fi
done
for c in "\${XDG_CONFIG_HOME:-$HOME/.config}"/herdr/plugins/*/herdeck*/bin/herdeck; do
  if [ -x "$c" ] && [ -f "$c" ]; then exec "$c" "$@"; fi
done
echo "herdeck: not found" >&2
exit 127`;

const key = (t) => `${t.index}\u0000${t.name}`;

function findPath(node, id, path = []) {
  if (!node) return null;
  const here = [...path, node];
  if (node.id === id) return here;
  for (const c of node.children ?? []) { const f = findPath(c, id, here); if (f) return f; }
  return null;
}

export default class Herdeck extends View {
  init(p, cx) {
    this.cfg = { ...(props() ?? {}), ...(p ?? {}) };   // p wins: the check shims pass fixture/width/active here
    this.focus = cx.focus_handle();
    this.scroll = VirtualListScrollHandle.new();
    this.width = this.cfg.width ?? 420;
    this.height = 460;
    this.demo = Boolean(this.cfg.demo) || localStorage.getItem("demo") === "1";
    this.tabs = [];            // last doc.tabs meta
    this.data = new Map();     // tab key -> {table, state, status, message, at, stale}
    this.sel = new Map();      // tab key -> selected row
    this.tried = new Map();    // tab index -> ms of the last load started
    this.activeName = localStorage.getItem("active");
    this.fatal = null;         // {kind, message, path?, doc?, at}
    this.busy = false; this.pending = null; this.needAll = false; this.doc = null;
    this.prompt = null;        // a row action being confirmed: {tab, row, key, title, warning, options, choice, phase, message}
    this.machine = this.resolveMachine();
    if (this.cfg.fixture) {   // test only: no exec, no timer
      const res = interpret({ code: 0, stdout: JSON.stringify(this.cfg.fixture), stderr: "" }, null, null);
      if (res.doc) {
        this.apply(res.doc, Date.now());
        const t = res.doc.tabs[(this.cfg.active ?? 0) - 1];
        if (t) this.activeName = t.name;
      } else this.fatal = { ...res.fatal, at: Date.now() };
      return;
    }
    this.load(cx, null);
    cx.timer.every(1000, (cx) => this.tick(cx));
    cx.spawn(async (cx) => { for (;;) { this.command(await nextCommand(), cx); cx.notify(); } });
  }

  // ---- data -------------------------------------------------------------------------------

  resolveMachine() {
    const m = this.cfg.machine;
    if (m == null || m === "") return null;
    if (m === "follow") return spaces().find((s) => s.active)?.machine ?? null;
    return m;
  }

  shown() {
    if (window()?.active === false) return false;
    if (!this.cfg.block) return true;
    const path = findPath(layout(), this.cfg.block);
    if (!path) return true;
    return path.every((n, i) => !n.hidden && !(n.type === "stack" && path[i + 1] && n.active != null && n.active !== path[i + 1].id));
  }

  active() { return this.tabs.find((t) => t.name === this.activeName) ?? this.tabs[0] ?? null; }
  cur() { const t = this.active(); return t ? this.data.get(key(t)) ?? null : null; }

  due(t) { return Date.now() - (this.tried.get(t.index) ?? 0) >= Math.max(2, t.interval ?? 2) * 1000; }

  tick(cx) {
    const m = this.resolveMachine();
    if (m !== this.machine) { this.machine = m; this.reset(); cx.notify(); }
    if (this.busy || !this.shown()) return;
    const now = Date.now();
    if (this.needAll || (this.fatal && now - this.fatal.at >= 30000)) return this.load(cx, null);
    const t = this.active();
    if (t && !this.fatal && this.due(t)) this.load(cx, t.index);
  }

  reset() {
    this.tabs = []; this.data.clear(); this.sel.clear(); this.tried.clear(); this.fatal = null; this.needAll = true;
    if (this.prompt?.phase !== "working") this.prompt = null;   // a dialog belongs to the machine and deck it was opened on
  }

  load(cx, index) {
    if (this.busy) return;
    this.busy = true; this.needAll = false;
    const now = Date.now(), machine = this.machine;
    if (index == null) { this.tried.clear(); for (const t of this.tabs) this.tried.set(t.index, now); }
    else this.tried.set(index, now);
    const args = ["--json", "--timeout", "15"];
    if (index != null) args.push("--tab", String(index));
    if (this.demo) args.push("--demo");
    if (this.cfg.config) args.push("--config", String(this.cfg.config));
    cx.spawn(async (cx) => {
      let res;
      try {
        const r = await exec("sh", ["-c", LAUNCH, "herdeck-mod", this.cfg.bin ?? "", ...args], { on: machine ?? undefined, timeout: 20000 });
        res = interpret(r, null, machine);
      } catch (e) { res = interpret(null, e, machine); }
      if (machine !== this.machine) this.needAll = true;   // the machine changed meanwhile: drop it
      else this.handle(res, index);
      this.busy = false;
      cx.notify();
      if (this.pending != null) { const i = this.pending; this.pending = null; this.load(cx, i); }
    });
  }

  handle(res, index) {
    const now = Date.now();
    if (res.doc) { this.fatal = null; this.apply(res.doc, now); return; }
    const f = res.fatal;
    if (f.kind === "no_such_tab") { this.activeName = null; this.needAll = true; }
    const withData = this.tabs.filter((t) => this.data.get(key(t))?.table);
    if (!withData.length) { this.fatal = { ...f, at: now }; return; }
    const hit = index == null ? withData : withData.filter((t) => t.index === index);
    for (const t of hit) {   // the person keeps seeing data
      const d = this.data.get(key(t));
      d.stale = { since: d.stale?.since ?? d.at, message: f.message, state: "error" };
    }
  }

  apply(doc, now) {
    this.doc = doc;
    this.tabs = doc.tabs.map(({ table, ...meta }) => meta);
    for (const t of doc.tabs) {
      if (t.state === "skipped") continue;
      const k = key(t), prev = this.data.get(k);
      if (t.table) this.data.set(k, { table: t.table, state: t.state, status: t.status, message: t.message, at: now, stale: null });
      else if (prev?.table) prev.stale = { since: prev.stale?.since ?? prev.at, message: t.message ?? t.status, state: t.state };
      else this.data.set(k, { table: null, state: t.state, status: t.status, message: t.message, at: now, stale: null });
    }
    if (!this.tabs.some((t) => t.name === this.activeName)) this.activeName = null;
  }

  // ---- actions ----------------------------------------------------------------------------

  rows() { return this.cur()?.table?.rows ?? []; }
  selIndex() { const t = this.active(); return t ? Math.min(this.sel.get(key(t)) ?? 0, Math.max(0, this.rows().length - 1)) : 0; }

  select(i) {
    const t = this.active(), n = this.rows().length;
    if (!t || !n) return;
    this.sel.set(key(t), Math.max(0, Math.min(n - 1, i)));
    this.scroll.scroll_to_item(this.selIndex());
  }

  show(cx, i) {
    const t = this.tabs[i];
    if (!t || t === this.active()) return;
    if (this.prompt && this.prompt.phase !== "working") this.prompt = null;
    this.activeName = t.name; localStorage.setItem("active", t.name);
    this.scroll.scroll_to_item(this.selIndex());
    if (!this.busy && this.due(t)) this.load(cx, t.index);
  }

  step(cx, d) {
    const n = this.tabs.length, t = this.active();
    if (n) this.show(cx, (this.tabs.indexOf(t) + d + n) % n);
  }

  refresh(cx) { const t = this.active(); if (t) { this.tried.delete(t.index); this.load(cx, t.index); } else this.load(cx, null); }

  // Reload one tab; when a load is already running, do it as soon as that one ends.
  refreshTab(cx, index) {
    this.tried.delete(index);
    if (this.busy) this.pending = index; else this.load(cx, index);
  }

  retry(cx) { this.fatal = null; this.load(cx, null); }

  setDemo(cx, on) {
    this.demo = on; localStorage.setItem("demo", on ? "1" : "0");
    this.reset(); if (!this.busy) this.load(cx, null);
  }

  // A row action (the TUI's `s` switch-lane): herdeck --json lists it on the row, `herdeck --act` runs it.
  rowAction(k) {
    const row = this.rows()[this.selIndex()];
    return row?.key != null ? (row.actions ?? []).find((a) => a.key === k) ?? null : null;
  }

  ask(k) {
    const a = this.rowAction(k), t = this.active(), row = this.rows()[this.selIndex()];
    if (!a || !a.options?.length || !t || this.prompt?.phase === "working") return false;
    // The prompt keeps the machine it was opened for: switching space must never send it elsewhere.
    this.prompt = { tab: t.index, machine: this.machine, row: row.key, key: a.key, title: a.title, warning: a.warning ?? null,
      options: a.options, choice: 0, phase: "ask", message: null };
    return true;
  }

  pick(d) {
    const p = this.prompt;
    if (p?.phase === "ask") p.choice = (p.choice + d + p.options.length) % p.options.length;
  }

  cancel() { if (this.prompt && this.prompt.phase !== "working") this.prompt = null; }

  confirm(cx) {
    const p = this.prompt;
    if (!p || p.phase !== "ask") return;
    p.phase = "working"; p.message = null;
    const args = ["--act", String(p.tab), p.row, p.key, p.options[p.choice].id];
    if (this.demo) args.push("--demo");
    if (this.cfg.config) args.push("--config", String(this.cfg.config));
    const machine = p.machine;
    cx.spawn(async (cx) => {
      let ok = false, message = "";
      try {
        const r = await exec("sh", ["-c", LAUNCH, "herdeck-mod", this.cfg.bin ?? "", ...args], { on: machine ?? undefined, timeout: 20000 });
        let doc = null;
        try { doc = JSON.parse(String(r.stdout ?? "").trim().split("\n").pop()); } catch (_e) { /* not JSON */ }
        if (doc && typeof doc.ok === "boolean") { ok = doc.ok; message = String(doc.message ?? ""); }
        else if (doc?.error?.message) message = String(doc.error.message);   // no deck, bad TOML: an error document, no `ok`
        else message = (String(r.stderr ?? "").trim() || "herdeck gave no answer (is it up to date?)").split("\n")[0];
      } catch (e) { message = String(e?.message ?? e); }
      p.phase = ok ? "done" : "failed"; p.message = message;
      cx.notify();
      if (ok) {   // the switch runs in the background on the server: look again a few times
        for (const ms of [1500, 5000, 12000]) { await cx.sleep(ms); if (this.machine === p.machine) this.refreshTab(cx, p.tab); }
      }
      await cx.sleep(ok ? 6000 : 15000);
      if (this.prompt === p) { this.prompt = null; cx.notify(); }
    });
  }

  command(c, cx) {
    if (c === "focus") this.focus.focus();
    else if (c === "refresh") this.refresh(cx);
    else if (c === "next") this.step(cx, 1);
    else if (c === "prev") this.step(cx, -1);
    else if (c === "close") this.close_();
  }

  close_() { const o = props()?.overlay; if (o != null) closeOverlay(o); }

  onKey(e, cx) {
    const k = e.keystroke, page = Math.max(1, Math.floor((this.height - 32 - 22 * 3 - 26) / ROW));
    const n = this.rows().length;
    let handled = true;
    if (this.prompt) {
      const p = this.prompt;
      if (p.phase === "ask") {
        if (k === "y" || k === "enter") this.confirm(cx);
        else if (k === "n" || k === "escape") this.cancel();
        else if (k === "right" || k === "down" || k === "j" || k === "l" || k === "tab" || k === "s") this.pick(1);
        else if (k === "left" || k === "up" || k === "k" || k === "h" || k === "shift-tab") this.pick(-1);
        // any other key is swallowed: the dialog is modal, as in the terminal
      } else if (k === "escape" || k === "enter") this.cancel();
      else handled = false;
      if (handled) { cx.stop_propagation(); cx.notify(); return; }
      handled = true;
    }
    if (this.rowAction(k) && this.ask(k)) { /* opened a prompt */ }
    else if (k === "]" || k === "tab") this.step(cx, 1);
    else if (k === "[" || k === "shift-tab") this.step(cx, -1);
    else if (/^[1-9]$/.test(k)) this.show(cx, Number(k) - 1);
    else if (k === "j" || k === "down") this.select(this.selIndex() + 1);
    else if (k === "k" || k === "up") this.select(this.selIndex() - 1);
    else if (k === "pagedown") this.select(this.selIndex() + page);
    else if (k === "pageup") this.select(this.selIndex() - page);
    else if (k === "g" || k === "home") this.select(0);
    else if (k === "shift-g" || k === "G" || k === "end") this.select(n - 1);
    else if (k === "r") this.refresh(cx);
    else if (k === "escape" && props()?.overlay != null) this.close_();
    else handled = false;
    if (handled) { cx.stop_propagation(); cx.notify(); }
  }

  // ---- drawing ----------------------------------------------------------------------------

  tone(tone) { return toneColor(this.t, tone, this.dark); }

  chip(d, active) {
    if (!d) return null;
    if (d.stale) return { text: "STALE", color: this.tone("warn"), tip: `Stale since ${clock(d.stale.since)}: ${d.stale.message ?? ""}` };
    if (d.state === "demo") return { text: "DEMO", color: this.tone("warn") };
    if (d.state === "not_set_up") return { text: "NOT SET UP", color: this.tone("warn") };
    if (d.state === "error") return { text: "ERROR", color: this.tone("bad"), tip: d.message };
    if (d.state === "timeout") return { text: "TIMEOUT", color: this.tone("bad"), tip: d.message };
    if (d.status && d.status !== "OK") return { text: d.status, color: this.tone("accent"), note: true, tip: d.status };
    return null;
  }

  dot(d) {
    if (!d) return null;
    if (d.state === "error" || d.state === "timeout") return this.tone("bad");
    return d.stale || d.state === "demo" || d.state === "not_set_up" ? this.tone("warn") : null;
  }

  btn(id, label, onClick, primary) {
    const t = this.t;
    return div().id(id).h(26).px(10).rounded(6).text_size(12).flex_none().items_center().flex().cursor_pointer()
      .when(primary, (el) => el.bg(t.solid).text_color(t.solid_foreground))
      .when(!primary, (el) => el.border(1).border_color(t.border).text_color(t.foreground).hover((s) => s.bg(t.hover)))
      .on_click(onClick).child(label);
  }

  iconBtn(id, label, onClick, spin) {
    const t = this.t;
    const g = div().child(label);
    if (spin) g.frames(SPIN, 90);
    return div().id(id).w(24).h(24).flex().items_center().justify_center().flex_none().rounded(6).text_size(13)
      .text_color(t.dim).hover((s) => s.bg(t.hover).text_color(t.foreground)).on_click(onClick).child(g);
  }

  card(title, body, buttons, code) {
    const t = this.t;
    return v_flex().flex_1().items_center().justify_center().gap(8).p(24)
      .child(div().text_size(13).font_semibold().text_color(t.foreground).child(title))
      .when(!!body, (el) => el.child(div().text_size(12).text_color(t.muted).max_w(340).child(body)))
      .when(!!code, (el) => el.child(div().font_family(this.mono).text_size(11.5).bg(t.raised).px(8).py(4).rounded(6).text_color(t.foreground).child(code)))
      .when(buttons.length > 0, (el) => el.child(h_flex().gap(8).children(buttons)));
  }

  fatalCard(cx) {
    const f = this.fatal, here = this.machine ? `on ${this.machine}` : "here";
    const retry = () => this.btn("hd-retry", "Retry", (_e, cx) => { this.retry(cx); cx.notify(); }, true);
    switch (f.kind) {
      case "missing":
        return this.card(`herdeck isn't installed ${here}`,
          "This panel runs the herdeck program. Install it with `herdr plugin install thefahien/herdeck`, a release from github.com/thefahien/herdeck, or `cargo install --git https://github.com/thefahien/herdeck`. Somewhere else? Give the block a `bin` prop.",
          [retry()]);
      case "too_old":
        return this.card("herdeck is too old", `This panel needs herdeck ${NEEDS} or newer (it reads \`herdeck --json\`).`, [retry()]);
      case "schema":
        return this.card(`herdeck ${f.doc?.herdeck ?? ""} is newer than this panel`,
          `It speaks data version ${f.doc?.schema}; this panel reads ${SCHEMA}. Update the herdeck mod.`, [retry()]);
      case "no_deck": {
        const btns = [];
        if (!this.machine) btns.push(this.btn("hd-setup", "Set up in a terminal", () => run("tab.new", { name: "herdeck setup", argv: [f.doc?.bin ?? "herdeck", ...(this.cfg.config ? ["--config", String(this.cfg.config)] : [])] }), true));
        btns.push(this.btn("hd-sample", "Show sample data", (_e, cx) => { this.setDemo(cx, true); cx.notify(); }, !!this.machine));
        return this.card("No deck yet", `herdeck shows the tabs listed in ${f.path ?? "your deck file"}.`, btns, this.machine ? "herdeck init" : null);
      }
      default: {
        const title = { config: "Your deck has an error", bad_output: "herdeck answered something unexpected", exec: "Couldn't run herdeck", unreachable: `Can't reach ${this.machine}` }[f.kind] ?? "Couldn't read your deck";
        const lines = String(f.message ?? "").split("\n").filter(Boolean).slice(0, 6);
        return v_flex().flex_1().items_center().justify_center().gap(8).p(24)
          .child(div().text_size(13).font_semibold().text_color(this.t.foreground).child(title))
          .child(v_flex().max_w(340).font_family(this.mono).text_size(11.5).text_color(this.t.muted).children(lines.map((l) => div().truncate().child(l))))
          .child(retry());
      }
    }
  }

  cell(c, col, cx) {
    const t = this.t;
    if (!c) return div();
    if (c.kind === "bar") {
      const pct = barPct(c), color = this.tone(c.tone);
      return h_flex().gap(6).items_center().w(col.px).flex_none()
        .child(div().flex_1().min_w(24).h(6).rounded(3).bg(t.border).child(div().h_full().rounded(3).w(`${pct}%`).bg(color)))
        .child(div().w(col.labelCh * CH).flex_none().text_right().whitespace_nowrap().text_color(color).child(c.text));
    }
    if (c.kind === "spark") {
      if (!c.values.length) return div().w(col.px).flex_none().h(14).flex().items_end().child(div().w(col.px).h(1).bg(t.border));
      const n = c.values.length, slot = Math.max(2, Math.floor((col.px - (n - 1)) / n));
      const hs = sparkHeights(c.values, c.max);
      return h_flex().h(SPARK_H).items_end().gap(1).w(col.px).flex_none()
        .children(c.values.map((v, i) => div().w(slot).h(hs[i]).flex_none().rounded(1).bg(v > 0 ? this.tone(c.tone) : t.border)));
    }
    const el = div().truncate().text_color(this.tone(c.tone));
    if (c.tone === "bad") el.font_semibold();
    if (col.flex) el.flex_1().min_w(0); else el.w(col.px).flex_none();
    return el.child(c.text ?? "");   // unknown kinds (docs/json.md: ignore them) show their text, or nothing
  }

  rowEl(row, i, lay, sel, cx) {
    const t = this.t;
    if (row.section != null) {
      return h_flex().h(ROW).px(PAD).gap(8).items_center().when(i === sel, (el) => el.bg(t.selected))
        .child(div().text_size(11).font_semibold().text_color(t.muted).flex_none().child(row.section))
        .child(div().flex_1().h(1).bg(t.border));
    }
    return h_flex().h(ROW).px(PAD).gap(GAP).items_center().font_family(this.mono).text_size(12).text_color(t.foreground)
      .when(i === sel, (el) => el.bg(t.selected))
      .children(lay.cols.map((col) => this.cell((row.cells ?? [])[col.i], col, cx)));
  }

  promptBar(cx) {
    const t = this.t, p = this.prompt;
    if (!p) return null;
    const line = (txt, color) => div().text_size(12).text_color(color ?? t.foreground).truncate().child(txt);
    if (p.phase === "working") {
      return h_flex().px(PAD).py(8).gap(8).items_center().flex_none().border_t(1).border_color(t.border).bg(t.raised)
        .child(div().text_color(t.muted).frames(SPIN, 90)).child(line(`${p.title} → ${p.options[p.choice].label}…`, t.muted));
    }
    if (p.phase === "done" || p.phase === "failed") {
      const ok = p.phase === "done";
      return h_flex().px(PAD).py(8).gap(8).items_center().flex_none().border_t(1).border_color(t.border).bg(t.raised)
        .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(this.tone(ok ? "ok" : "bad")).child(ok ? p.message : `Failed: ${p.message}`))
        .child(this.btn("hd-act-ok", "OK", () => { this.cancel(); }, false));
    }
    return v_flex().px(PAD).py(8).gap(6).flex_none().border_t(1).border_color(t.border).bg(t.raised)
      .child(div().text_size(12).font_semibold().truncate().child(p.title))
      .when(!!p.warning, (el) => el.child(line(`⚠ ${p.warning}`, this.tone("warn"))))
      // One option per row, full width, wrapping onto a second line when it must: lane names are
      // long and differ in the middle ("…-variant-…"), so they are never cut.
      .child(v_flex().gap(4).children(p.options.map((o, i) =>
        div().id(`hd-opt-${i}`).min_h(24).py(4).px(10).rounded(6).flex().items_center().min_w(0).text_size(12).cursor_pointer()
          .when(i === p.choice, (el) => el.bg(t.solid).text_color(t.solid_foreground))
          .when(i !== p.choice, (el) => el.border(1).border_color(t.border).text_color(t.foreground).hover((s) => s.bg(t.hover)))
          .on_click((_e, cx) => { p.choice = i; this.focus.focus(); cx.notify(); })
          .child(div().flex_1().min_w(0).child(o.label)))))
      .child(h_flex().gap(8).items_center()
        .child(this.btn("hd-act-go", "Switch", (_e, cx) => { this.confirm(cx); cx.notify(); }, true))
        .child(this.btn("hd-act-no", "Cancel", (_e, cx) => { this.cancel(); cx.notify(); }, false))
        .child(div().flex_1().min_w(0).truncate().text_size(11).text_color(t.dim).child("y confirm · n cancel · ↑↓←→ pick")));
  }

  tableView(d, cx) {
    const t = this.t, table = d.table, lay = columnLayout(table, this.width), rows = table.rows;
    const sel = this.selIndex(), selRow = rows[sel];
    const detail = selRow ? detailText(table, selRow, lay.hidden) : "";
    const titles = h_flex().h(22).px(PAD).gap(GAP).items_center().flex_none().border_b(1).border_color(t.border)
      .font_family(this.mono).text_size(11).font_medium().text_color(t.dim)
      .children([...lay.cols.map((col) => {
        const el = div().truncate().child(table.columns[col.i] ?? "");
        return col.flex ? el.flex_1().min_w(0) : el.w(col.px).flex_none();
      })])
      .when(lay.hidden.length > 0, (el) => el.child(div().id("hd-more").flex_none().ml_auto().tooltip(lay.hidden.map((i) => table.columns[i]).join(", ")).child(`+${lay.hidden.length}`)));
    return v_flex().flex_1().min_h(0)
      .when(!!table.header, (el) => el.child(div().h(22).px(PAD).flex().items_center().flex_none().text_size(11.5).text_color(t.muted).truncate().child(table.header)))
      .child(titles)
      .child(rows.length === 0
        ? div().flex_1().flex().items_center().justify_center().text_size(12).text_color(t.dim).child("No rows")
        : v_flex().relative().flex_1().min_h(0)
          .child(v_virtual_list("hd-rows", rows.length, ROW, (i) => "r" + i,
            (range) => rows.slice(range.start, range.end).map((r, k) => this.rowEl(r, range.start + k, lay, sel, cx)))
            .track_scroll(this.scroll)
            .on_item_click((k, cx) => { this.select(Number(k.slice(1))); this.focus.focus(); cx.notify(); }))
          .child(Scrollbar.vertical("hd-rows").absolute().inset_0()))
      .when(!!this.prompt, (el) => el.child(this.promptBar(cx)))
      .when(!!detail || !!selRow?.actions?.length, (el) => el.child(h_flex().h(30).px(PAD).gap(8).items_center().flex_none().border_t(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(div().flex_1().min_w(0).truncate().child(detail))
        .when(!this.prompt && !!selRow?.actions?.length, (e) => e.child(this.btn("hd-act-open", `${selRow.actions[0].title.split(":")[0]} (${selRow.actions[0].key})`, (_e, cx) => { this.ask(selRow.actions[0].key); this.focus.focus(); cx.notify(); }, false)))));
  }

  body(cx) {
    const t = this.t, tab = this.active(), d = this.cur();
    if (this.fatal) return this.fatalCard(cx);
    if (!tab || !d) {
      return v_flex().flex_1().items_center().justify_center().gap(8)
        .child(div().text_size(13).text_color(t.muted).frames(SPIN, 90))
        .child(div().text_size(12).text_color(t.muted).child("Reading your deck…"));
    }
    if (!d.table) {
      return v_flex().flex_1().items_center().justify_center().gap(8).p(24)
        .child(div().text_size(12).text_color(this.tone("bad")).child("Couldn't read this tab"))
        .child(div().text_size(12).text_color(t.muted).max_w(340).child(d.message ?? d.status ?? ""))
        .child(this.btn("hd-tab-retry", "Retry", (_e, cx) => { this.refresh(cx); cx.notify(); }, false));
    }
    return this.tableView(d, cx);
  }

  strip(cx) {
    const t = this.t, tab = this.active(), d = this.cur(), chip = this.chip(d);
    const overlay = props()?.overlay != null;
    return h_flex().h(32).px(8).gap(4).items_center().flex_none().border_b(1).border_color(t.border)
      .child(h_flex().id("hd-tabs").flex_1().min_w(0).gap(4).overflow_x_scrollbar().children(this.tabs.map((tb, i) => {
        const on = tb === tab, dot = this.dot(this.data.get(key(tb)));
        return h_flex().id(`hd-tab-${i}`).h(24).px(8).gap(5).items_center().flex_none().rounded(6).text_size(12)
          .when(on, (el) => el.bg(t.raised).border(1).border_color(t.border).text_color(t.foreground).font_medium())
          .when(!on, (el) => el.text_color(t.muted).hover((s) => s.bg(t.hover)))
          .on_click((_e, cx) => { this.show(cx, i); this.focus.focus(); cx.notify(); })
          .child(div().font_family(this.mono).text_size(11).text_color(t.dim).child(String(i + 1)))
          .child(div().max_w(140).truncate().child(tb.name))
          .when(!!dot, (el) => el.child(div().w(6).h(6).rounded(3).flex_none().bg(dot)));
      })))
      .when(!!chip && this.width >= 300, (el) => el.child(
        div().id("hd-chip").h(18).px(6).rounded(5).flex().items_center().flex_none().text_size(10.5).font_semibold()
          .bg(chip.color + "26").text_color(chip.color).when(chip.note, (e) => e.max_w(160).truncate())
          .when(!!chip.tip, (e) => e.tooltip(chip.tip)).child(chip.text)))
      .child(this.iconBtn("hd-refresh", "↻", (_e, cx) => { this.refresh(cx); cx.notify(); }, this.busy))
      .when(overlay, (el) => el.child(this.iconBtn("hd-close", "✕", () => this.close_(), false)));
  }

  footer(cx) {
    const t = this.t, d = this.cur();
    const deck = [this.machine, this.doc?.deck].filter(Boolean).join(" · ");
    const right = d?.stale ? `stale since ${clock(d.stale.since)}`
      : d ? (this.width >= 300 && this.doc ? `herdeck ${this.doc.herdeck} · ${clock(d.at)}` : clock(d.at)) : "";
    return h_flex().h(22).px(PAD).gap(8).items_center().justify_between().flex_none().text_size(11).text_color(t.dim)
      .child(h_flex().gap(8).min_w(0).child(div().truncate().child(deck))
        .when(this.demo, (el) => el.child(div().id("hd-demo-off").flex_none().hover((s) => s.text_color(t.foreground))
          .on_click((_e, cx) => { this.setDemo(cx, false); cx.notify(); }).child("demo ✕"))))
      .child(div().flex_none().text_color(d?.stale ? this.tone("warn") : t.dim).child(right));
  }

  render(cx) {
    this.t = theme();
    this.dark = Boolean(settings()?.dark);
    this.mono = settings()?.font_family ?? "JetBrains Mono";
    return v_flex().id("hd-root").size_full().bg(this.cfg.block ? this.t.chrome : this.t.background).text_color(this.t.foreground).text_size(12)
      .track_focus(this.focus)
      .on_mouse_down("left", () => this.focus.focus())
      .on_key_down((e, cx) => this.onKey(e, cx))
      .on_size_change((s, cx) => { this.width = s.width; this.height = s.height; cx.notify(); })
      .child(this.strip(cx))
      .child(this.body(cx))
      .child(this.footer(cx));
  }
}
