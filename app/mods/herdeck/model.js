// Pure helpers for the herdeck panel: no "herdr" import, so `node` can load this file too.
// selftest() throws on the first wrong answer; the check script runs it through every shim mod.

export const CH = 7.2;   // px per character of a 12 px mono font (0.6 em advance)
export const GAP = 12;   // px between columns
export const PAD = 12;   // px of row padding each side
export const ROW = 24;   // px per row (virtual list item size)
export const SPARK_H = 14;

const len = (s) => [...String(s ?? "")].length;

// Tone -> theme token. `t` is theme(); `dark` is settings().dark.
export function toneColor(t, tone, dark) {
  switch (tone) {
    case "ok": return dark ? t.ansi10 : t.ansi2;
    case "warn": return dark ? t.ansi11 : t.ansi3;
    case "bad": return dark ? t.ansi9 : t.ansi1;
    case "accent": return t.accent;
    case "dim": return t.dim;
    default: return t.foreground;
  }
}

// How many characters a cell wants (TUI chars).
function cellChars(c) {
  if (!c) return 0;
  if (c.kind === "bar") return (c.width ?? 8) + 1 + Math.max(5, len(c.text));
  if (c.kind === "spark") return c.width ?? 10;
  return len(c.text);
}

function barLabelChars(table, i) {
  let n = 5;
  for (const r of table.rows) if (r.cells?.[i]?.kind === "bar") n = Math.max(n, len(r.cells[i].text));
  return n;
}

// Port of the TUI's column fitting (src/app.rs), in px.
// -> { cols: [{i, px, flex, kind, labelCh}], hidden: [i] }
export function columnLayout(table, width) {
  const data = table.rows.filter((r) => r.cells).slice(0, 500);
  const n = Math.max(table.columns.length, ...data.map((r) => r.cells.length), 0);
  if (n === 0) return { cols: [], hidden: [] };
  const first = data[0]?.cells ?? [];
  const kind = (i) => first[i]?.kind ?? "text";
  const chars = [], px = [], min = [];
  for (let i = 0; i < n; i++) {
    let c = len(table.columns[i]);
    for (const r of data) c = Math.max(c, cellChars(r.cells[i]));
    chars[i] = Math.min(c, 60);
    px[i] = (chars[i] + 1) * CH;   // one character of slack: a font a hair wider than 0.6 em must not cut every cell
    min[i] = Math.min(px[i], kind(i) === "bar" ? (5 + barLabelChars(table, i)) * CH : 6 * CH);
  }
  const natural = px.slice();
  let over = px.reduce((a, b) => a + b, 0) + GAP * (n - 1) - (width - 2 * PAD);
  for (const i of [...px.keys()].sort((a, b) => px[b] - px[a])) {   // stable: ties keep column order
    if (over <= 0) break;
    const cut = Math.min(over, px[i] - min[i]);
    px[i] -= cut; over -= cut;
  }
  const vis = [...px.keys()], hidden = [];
  while (over > 0 && vis.length > 1) {
    const i = vis.pop();
    hidden.unshift(i); over -= px[i] + GAP;
  }
  if (hidden.length) {
    let give = -over;
    for (const i of vis) { const g = Math.max(0, Math.min(give, natural[i] - px[i])); px[i] += g; give -= g; }
  }
  const cols = vis.map((i) => ({ i, px: px[i], kind: kind(i), labelCh: barLabelChars(table, i), flex: false }));
  const last = cols[cols.length - 1];
  if (last.kind === "text") last.flex = true;
  return { cols, hidden };
}

// "TITLE value" for each hidden text/bar cell of a row, in column order.
export function hiddenText(table, row, hidden) {
  return hidden.map((i) => {
    const c = row.cells?.[i];
    if (!c || c.kind === "spark") return null;
    return `${table.columns[i] ?? ""} ${c.text}`.trim();
  }).filter(Boolean).join(" · ");
}

export const detailText = (table, row, hidden) =>
  [row?.detail, row?.cells ? hiddenText(table, row, hidden) : ""].filter(Boolean).join("  ·  ");

// Heights in px of a spark's slots.
export function sparkHeights(values, max) {
  const top = max > 0 ? max : (Math.max(...values, 0) || 1);
  return values.map((v) => (v > 0 ? Math.min(SPARK_H, Math.max(2, Math.round((v / top) * SPARK_H))) : 1));
}

export const barPct = (c) => (c.max > 0 ? Math.max(0, Math.min(1, c.value / c.max)) * 100 : 0);

export const pad2 = (n) => String(n).padStart(2, "0");
export const clock = (ms) => { const d = new Date(ms); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };

// Turn an exec result (or its failure) into { doc } or { fatal: {kind, message} }. DESIGN 3.4.
export function interpret(r, err, machine) {
  if (err) {
    const m = String(err?.message ?? err);
    if (/ENOENT|No such file|not found/i.test(m) && !machine) return { fatal: { kind: "exec", message: "herdeck needs Linux or macOS (sh not found)" } };
    return { fatal: { kind: machine ? "unreachable" : "exec", message: m } };
  }
  let doc = null;
  try { doc = JSON.parse(r.stdout); } catch { /* not json */ }
  if (doc && typeof doc === "object" && typeof doc.schema === "number") {
    if (doc.schema !== 1) return { fatal: { kind: "schema", message: `data version ${doc.schema}`, doc } };
    if (doc.error) return { fatal: { kind: doc.error.kind, message: doc.error.message, path: doc.error.path, doc } };
    if (Array.isArray(doc.tabs)) return { doc };
  }
  const se = r.stderr ?? "";
  if (r.code === 127 && se.includes("herdeck: not found")) return { fatal: { kind: "missing", message: "herdeck: not found" } };
  if (r.code === 2 && se.includes('unknown argument "--json"')) return { fatal: { kind: "too_old", message: "herdeck is too old" } };
  const last = se.split("\n").map((s) => s.trim()).filter(Boolean).pop();
  return { fatal: { kind: "bad_output", message: last || (r.stdout ?? "").slice(0, 120) || `exit ${r.code}` } };
}

const eq = (a, b, what) => {
  const x = JSON.stringify(a), y = JSON.stringify(b);
  if (x !== y) throw new Error(`selftest: ${what}: got ${x}, want ${y}`);
};

export function selftest() {
  const text = (s, tone = null) => ({ kind: "text", text: s, tone });
  const bar = (v, s, tone) => ({ kind: "bar", value: v, max: 100, text: s, tone, width: 8 });
  const table = {
    header: "3 processes", columns: ["PID", "USER", "CPU", "MEM", "UP", "COMMAND"],
    rows: [
      { cells: [text("4211"), text("dev"), bar(93, "93%", "bad"), bar(12.4, "12.4%", "ok"), text("02:14:09"), text("cargo build --release")], detail: "cargo build --release --locked" },
      { cells: [text("3870"), text("dev"), bar(74, "74%", "warn"), bar(3.1, "3.1%", "ok"), text("00:41"), text("node")] },
      { section: "idle" },
    ],
  };
  const vis = (w) => columnLayout(table, w).cols.map((c) => c.i);
  eq(vis(900), [0, 1, 2, 3, 4, 5], "900 visible");
  eq(vis(420), [0, 1, 2, 3, 4, 5], "420 visible");
  const l420 = columnLayout(table, 420);
  if (Math.abs(l420.cols[5].px - 43.2) > 0.01) throw new Error(`selftest: 420 COMMAND px ${l420.cols[5].px}`);
  const l260 = columnLayout(table, 260);
  eq(l260.cols.map((c) => c.i), [0, 1, 2], "260 visible");
  eq(l260.hidden, [3, 4, 5], "260 hidden");
  if (Math.abs(l260.cols[2].px - 108) > 0.01) throw new Error(`selftest: 260 CPU px ${l260.cols[2].px}`);
  eq(hiddenText(table, table.rows[0], l260.hidden), "MEM 12.4% · UP 02:14:09 · COMMAND cargo build --release", "hidden text");
  eq(detailText(table, table.rows[0], []), "cargo build --release --locked", "detail without hidden");
  eq(sparkHeights([0, 50, 100], 100), [1, 7, 14], "spark heights");
  eq(sparkHeights([], null), [], "empty spark");
  eq(barPct({ value: 5, max: 0 }), 0, "bar max 0");
  const t = {}; for (let i = 0; i < 16; i++) t[`ansi${i}`] = `A${i}`;
  Object.assign(t, { accent: "ACC", dim: "DIM", foreground: "FG" });
  for (const tone of ["ok", "warn", "bad", "accent", "dim", null])
    for (const dark of [true, false]) if (!String(toneColor(t, tone, dark)).match(/^(A\d+|ACC|DIM|FG)$/)) throw new Error(`selftest: tone ${tone}`);
  eq(toneColor(t, "bad", true), "A9", "bad dark");
  eq(toneColor(t, "ok", false), "A2", "ok light");
  eq(interpret({ code: 2, stdout: "", stderr: 'error: unknown argument "--json"' }).fatal.kind, "too_old", "too_old");
  eq(interpret({ code: 127, stdout: "", stderr: "herdeck: not found\n" }).fatal.kind, "missing", "missing");
  eq(interpret({ code: 1, stdout: '{"schema":1,"error":{"kind":"no_deck","message":"m","path":"p"}}', stderr: "" }).fatal.kind, "no_deck", "no_deck");
  eq(interpret({ code: 0, stdout: '{"schema":2,"tabs":[]}', stderr: "" }).fatal.kind, "schema", "schema");
  eq(interpret({ code: 0, stdout: "nope", stderr: "boom\n" }).fatal.message, "boom", "bad_output");
}
