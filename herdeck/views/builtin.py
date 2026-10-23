"""Generic tab types. Each one is configured from a ``[[tab]]`` table in tabs.toml.

    command     run a shell command; show its lines, a parsed table, or JSON
    processes   local processes (ps), filtered and sorted
    http        health checks for HTTP endpoints and TCP ports
    json        any HTTP API that returns JSON, mapped to columns
    prometheus  PromQL queries as columns, one row per label value
"""
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Dict, List, Tuple

from herdeck.common import get_ssl_context, open_url
from herdeck.views.base import Bar, Spark, TableView, Text


# ---------------------------------------------------------------- helpers
def dig(obj: Any, path: str) -> Any:
    """Follow a dotted path: ``a.b.0.c``. ``""`` returns obj itself."""
    if not path:
        return obj
    for part in str(path).split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.lstrip("-").isdigit():
            i = int(part)
            obj = obj[i] if -len(obj) <= i < len(obj) else None
        else:
            return None
        if obj is None:
            return None
    return obj


def num(v: Any):
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().rstrip("%")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tone_for(value: Any, col: Dict[str, Any]):
    """Colour rules: ok/bad value lists, warn_above/bad_above, warn_below/bad_below."""
    s = str(value)
    if s in [str(x) for x in col.get("bad_values", [])]:
        return "bad"
    if s in [str(x) for x in col.get("warn_values", [])]:
        return "warn"
    if s in [str(x) for x in col.get("ok_values", [])]:
        return "ok"
    n = num(value)
    if n is not None:
        if "bad_above" in col and n > col["bad_above"] or "bad_below" in col and n < col["bad_below"]:
            return "bad"
        if "warn_above" in col and n > col["warn_above"] or "warn_below" in col and n < col["warn_below"]:
            return "warn"
        if any(k in col for k in ("bad_above", "warn_above", "bad_below", "warn_below")):
            return "ok"
    return None


def make_cell(value: Any, col: Dict[str, Any]):
    """Turn a raw value into a cell according to a column spec."""
    if isinstance(value, list) and col.get("spark"):
        return Spark([num(v) or 0 for v in value], int(col.get("width", 10)), col.get("bar"))
    if col.get("bar") is not None and num(value) is not None:
        n = num(value) * float(col.get("scale", 1))
        unit = col.get("unit", "%" if float(col["bar"]) == 100 else "")
        fmt = col.get("format", "{:.0f}")
        return Bar(n, float(col["bar"]), fmt.format(n) + unit, int(col.get("width", 10)))
    if value is None:
        return Text("-", "dim")
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if num(value) is not None and ("format" in col or "scale" in col):
        value = col.get("format", "{:g}").format(num(value) * float(col.get("scale", 1)))
    if isinstance(value, (dict, list)):
        value = json.dumps(value, separators=(",", ":"))
    text = f"{value}{col.get('unit', '')}"
    return Text(text, tone_for(value, col)) if tone_for(value, col) else text


def map_json(data: Any, items: str, columns: List[Dict[str, Any]], limit: int = 500):
    """Map a JSON document to (titles, rows). Without ``columns``, take the keys."""
    rows_src = dig(data, items) if items else data
    if isinstance(rows_src, dict):
        if all(isinstance(v, dict) for v in rows_src.values()):
            rows_src = [dict({"key": k}, **v) for k, v in rows_src.items()]
        else:  # a flat object: show it as key / value
            rows_src = [{"key": k, "value": v} for k, v in rows_src.items()]
    if not isinstance(rows_src, list):
        rows_src = [rows_src]
    if not columns:
        keys: List[str] = []
        for r in rows_src[:20]:
            for k in (r.keys() if isinstance(r, dict) else []):
                if k not in keys and len(keys) < 8:
                    keys.append(k)
        columns = [{"title": k, "path": k} for k in keys] or [{"title": "value", "path": ""}]
    titles = [c.get("title", c.get("path", "")) for c in columns]
    rows = [[make_cell(dig(r, c.get("path", "")), c) for c in columns] for r in rows_src[:limit]]
    return titles, rows


def fmt_secs(s: float) -> str:
    s = int(s)
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit}"
    return f"{s}s"


# ---------------------------------------------------------------- command
class CommandView(TableView):
    """Run a command every ``interval`` seconds.

    command is a string run with ``sh -c`` (use $VARS freely, the shell expands
    them) or a list run directly without a shell.
    format = "lines" (default): one row per output line, ``highlight`` maps a regex
             to a tone, e.g. {"error|fail" = "bad", "warn" = "warn"}
    format = "table": split each line on whitespace into ``headers`` (default: the
             words of the first line; the last column takes the rest of the line).
             ``[[tab.column]]`` entries with a matching ``title`` add bars/colours.
    format = "json" / "jsonl": parse output, then ``items`` and ``columns`` as in
             the json tab type
    """

    default_interval = 10.0

    def configured(self):
        return bool(self.cfg.get("command"))

    def fetch(self):
        cmd = self.cfg["command"]
        argv = cmd if isinstance(cmd, list) else ["sh", "-c", cmd]
        p = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           timeout=float(self.cfg.get("timeout", 20)))
        fmt = self.cfg.get("format", "lines")
        self.exit_code = p.returncode
        err = (p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1]
        if p.returncode and (fmt != "lines" or not p.stdout.strip()):
            if fmt == "lines" and p.stderr.strip():
                return [self.cfg.get("title", "output")], [[Text(l, "bad")] for l in p.stderr.splitlines()]
            raise RuntimeError(err)
        out = p.stdout
        if fmt in ("json", "jsonl"):
            data = [json.loads(l) for l in out.splitlines() if l.strip()] if fmt == "jsonl" else json.loads(out)
            return map_json(data, self.cfg.get("items", ""), self.cfg.get("column") or self.cfg.get("columns") or [])
        lines = out.rstrip("\n").splitlines()
        if fmt == "table" and lines:
            titles = self.cfg.get("headers") or lines[0].split()
            body = lines[1:] if self.cfg.get("skip_header", True) else lines
            specs = {c.get("title"): c for c in self.cfg.get("column", [])}
            rows = []
            for ln in body:
                if not ln.strip():
                    continue
                parts = ln.split(None, len(titles) - 1)
                rows.append([make_cell(x, specs[h]) if h in specs else self._tone(x)
                             for h, x in zip(titles, parts)])
            return titles, rows
        return [self.cfg.get("title", "output")], [[self._tone(l)] for l in lines]

    def _tone(self, text: str):
        for pat, tone in (self.cfg.get("highlight") or {}).items():
            if re.search(pat, text, re.I):
                return Text(text, tone)
        return text

    def header(self):
        code = getattr(self, "exit_code", 0)
        cmd = self.cfg.get("command")
        return f"$ {cmd if isinstance(cmd, str) else shlex.join(cmd or [])}" + (f"   (exit {code})" if code else "")


# ---------------------------------------------------------------- processes
class ProcessView(TableView):
    """Local processes. Options: match (regex on the command line), user,
    sort = "cpu" | "mem" | "pid", limit."""

    default_interval = 3.0

    def fetch(self):
        out = subprocess.run(["ps", "-axo", "pid=,user=,pcpu=,pmem=,etime=,args="],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10).stdout
        pat = re.compile(self.cfg["match"], re.I) if self.cfg.get("match") else None
        me = os.getpid()
        procs = []
        for ln in out.splitlines():
            parts = ln.split(None, 5)
            if len(parts) < 6:
                continue
            pid, user, cpu, mem, etime, args = parts
            comm = args.split()[0] if args.split() else args
            if int(pid) == me or (pat and not pat.search(args)) or (self.cfg.get("user") and user != self.cfg["user"]):
                continue
            procs.append((int(pid), user, float(cpu), float(mem), etime, os.path.basename(comm), args))
        key = {"cpu": lambda p: -p[2], "mem": lambda p: -p[3], "pid": lambda p: p[0]}.get(self.cfg.get("sort", "cpu"))
        procs.sort(key=key)
        procs = procs[: int(self.cfg.get("limit", 50))]
        self._args = {p[0]: p[6] for p in procs}
        rows = [[str(p[0]), p[1][:12], Bar(p[2], 100, f"{p[2]:.0f}%", 8), Bar(p[3], 100, f"{p[3]:.1f}%", 8),
                 p[4], p[5]] for p in procs]
        return ["PID", "USER", "CPU", "MEM", "UP", "COMMAND"], rows

    def header(self):
        what = f"matching /{self.cfg['match']}/" if self.cfg.get("match") else "all"
        return f"{len(self.rows)} processes ({what}), sorted by {self.cfg.get('sort', 'cpu')}"

    def detail(self, row):
        return getattr(self, "_args", {}).get(int(str(row[0])), "")


# ---------------------------------------------------------------- http / tcp checks
class HttpCheckView(TableView):
    """Health checks. Each ``[[tab.check]]`` has name plus either
    url (expect = 200, contains = "text", method, timeout, insecure) or
    tcp = "host:port"."""

    default_interval = 15.0

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.hist: Dict[str, deque] = {}

    def configured(self):
        return bool(self.cfg.get("check"))

    def demo(self):
        cols = ["SERVICE", "STATE", "CODE", "LATENCY", "TREND", "TARGET"]
        rows = [["api", Text("UP", "ok"), "200", "42 ms", Spark([30, 41, 38, 42], 10, None), "https://api.example.com/health"],
                ["database", Text("UP", "ok"), "tcp", "3 ms", Spark([3, 2, 3, 3], 10, None), "db.example.com:5432"],
                ["worker", Text("DOWN", "bad"), "503", "120 ms", Spark([80, 90, 120], 10, None), "http://worker.example.com/ready"]]
        return cols, rows

    def _check(self, c: Dict[str, Any]) -> Tuple[bool, str, float, str]:
        t0 = time.time()
        timeout = float(c.get("timeout", 5))
        try:
            if c.get("tcp"):
                host, port = c["tcp"].rsplit(":", 1)
                with socket.create_connection((host, int(port)), timeout=timeout):
                    return True, "tcp", time.time() - t0, ""
            req = urllib.request.Request(c["url"], method=c.get("method", "GET"),
                                         headers={"User-Agent": "herdeck"})
            ctx = get_ssl_context()
            if c.get("insecure"):
                import ssl
                ctx = ssl._create_unverified_context()
            try:
                with open_url(req, timeout, ctx) as r:
                    code, body = r.status, r.read(65536).decode(errors="replace")
            except urllib.error.HTTPError as e:
                code, body = e.code, ""
                e.close()
            want = c.get("expect", 200)
            ok = code in want if isinstance(want, list) else code == want
            if ok and c.get("contains") and c["contains"] not in body:
                return False, str(code), time.time() - t0, f"body lacks {c['contains']!r}"
            return ok, str(code), time.time() - t0, "" if ok else f"expected {want}"
        except Exception as e:
            return False, "-", time.time() - t0, str(e)[:80]

    def fetch(self):
        checks = self.cfg["check"]
        results: List[Any] = [None] * len(checks)

        def run(i, c):
            results[i] = self._check(c)

        threads = [threading.Thread(target=run, args=(i, c), daemon=True) for i, c in enumerate(checks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(float(self.cfg.get("timeout", 10)) + 2)
        rows, self._why = [], {}
        for c, r in zip(checks, results):
            ok, code, dt, why = r or (False, "-", 0.0, "timed out")
            name = c.get("name") or c.get("url") or c.get("tcp")
            h = self.hist.setdefault(name, deque(maxlen=20))
            h.append(dt * 1000)
            self._why[name] = why
            rows.append([name, Text("UP" if ok else "DOWN", "ok" if ok else "bad"), code,
                         f"{dt * 1000:.0f} ms", Spark(list(h), 10, None), c.get("url") or c.get("tcp")])
        return ["SERVICE", "STATE", "CODE", "LATENCY", "TREND", "TARGET"], rows

    def header(self):
        up = sum(1 for r in self.rows if isinstance(r[1], Text) and r[1].text == "UP")
        return f"{up}/{len(self.rows)} up"

    def detail(self, row):
        return getattr(self, "_why", {}).get(str(row[0]), "")


# ---------------------------------------------------------------- json api
class JsonView(TableView):
    """GET a URL returning JSON. ``items`` = dotted path to the list (or object),
    ``[[tab.column]]`` = title, path, and optional bar / unit / format / scale /
    colour rules (warn_above, bad_above, ok_values, bad_values). ``headers`` adds
    request headers; values may use ${ENV_VAR}."""

    default_interval = 10.0

    def configured(self):
        return bool(self.cfg.get("url"))

    def fetch(self):
        req = urllib.request.Request(self.cfg["url"], headers=dict({"Accept": "application/json",
                                                                    "User-Agent": "herdeck"},
                                                                   **(self.cfg.get("headers") or {})))
        with open_url(req, float(self.cfg.get("timeout", 8)), get_ssl_context()) as r:
            data = json.load(r)
        return map_json(data, self.cfg.get("items", ""), self.cfg.get("column", []))


# ---------------------------------------------------------------- prometheus
class PrometheusView(TableView):
    """One row per value of ``label`` (default "instance"); one column per
    ``[[tab.metric]]`` (title, query, and bar / unit / format / colour rules as for
    json columns; spark = true adds a 10-minute trend)."""

    default_interval = 5.0

    def configured(self):
        return bool(self.cfg.get("url") and self.cfg.get("metric"))

    def _get(self, path: str, **params):
        url = self.cfg["url"].rstrip("/") + path + "?" + urllib.parse.urlencode(params)
        with open_url(urllib.request.Request(url, headers={"User-Agent": "herdeck"}), 8, get_ssl_context()) as r:
            d = json.load(r)
        if d.get("status") != "success":
            raise RuntimeError(d.get("error", "query failed"))
        return d["data"]["result"]

    def fetch(self):
        label = self.cfg.get("label", "instance")
        keys: List[str] = list(self.cfg.get("rows", []))
        table: Dict[str, Dict[int, Any]] = {}
        now = int(time.time())
        for j, m in enumerate(self.cfg["metric"]):
            if m.get("spark"):
                res = self._get("/api/v1/query_range", query=m["query"], start=now - 600, end=now, step=30)
                for s in res:
                    k = s["metric"].get(label, "")
                    table.setdefault(k, {})[j] = [float(v[1]) for v in s["values"]]
            else:
                for s in self._get("/api/v1/query", query=m["query"]):
                    k = s["metric"].get(label, "")
                    table.setdefault(k, {})[j] = float(s["value"][1])
            for k in table:
                if k not in keys and not self.cfg.get("rows"):
                    keys.append(k)
        if not table:
            raise RuntimeError("queries returned no series")
        titles = [label.upper()] + [m.get("title", m["query"][:16]) for m in self.cfg["metric"]]
        rows = [[k] + [make_cell(table.get(k, {}).get(j), m) for j, m in enumerate(self.cfg["metric"])]
                for k in keys]
        return titles, rows


TYPES = {
    "command": CommandView,
    "processes": ProcessView,
    "http": HttpCheckView,
    "json": JsonView,
    "prometheus": PrometheusView,
}
