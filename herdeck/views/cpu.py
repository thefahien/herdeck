import curses
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from herdeck.common import get_ssl_context, make_pbar, make_sparkline
from herdeck.demo import get_demo_nodes
from herdeck.views.base import BaseView

PROM_URL = os.environ.get("HERDECK_PROMETHEUS_URL", "")
NODES = [n.strip() for n in os.environ.get("HERDECK_NODES", "worker-1,worker-2").split(",") if n.strip()]


class CPUView(BaseView):
    name = "Fleet CPU"
    key_hint = "2"

    def __init__(self, prom_url: str = PROM_URL, refresh_interval: float = 3.0):
        self.prom_url = (prom_url or "").rstrip("/")
        self.interval = refresh_interval
        self.last_fetch = 0.0
        self.last_ok_time = 0.0
        self.is_demo = False
        self.nodes_data: Dict[str, Dict[str, Any]] = {}
        self.error_msg = ""
        self.selected_idx = 0
        self.status_line = "Ready"
        self.ctx = get_ssl_context()
        self.node_keys = list(NODES)

    def _query_prom(self, query: str, timeout: float = 2.0) -> List[Dict[str, Any]]:
        url = f"{self.prom_url}/api/v1/query?query={urllib.parse.quote(query)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as resp:
            data = json.loads(resp.read().decode())
            return data.get("data", {}).get("result", [])

    def _query_prom_range(self, query: str, step: int = 60, span_s: int = 600) -> Dict[str, List[float]]:
        now = int(time.time())
        start = now - span_s
        url = (
            f"{self.prom_url}/api/v1/query_range?"
            f"query={urllib.parse.quote(query)}&start={start}&end={now}&step={step}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.5, context=self.ctx) as resp:
            data = json.loads(resp.read().decode())
            results = data.get("data", {}).get("result", [])
            out = {}
            for r in results:
                job = r.get("metric", {}).get("job", "")
                vals = [float(v[1]) for v in r.get("values", [])]
                out[job] = vals
            return out

    def update(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self.last_fetch < self.interval):
            return
        self.last_fetch = now

        if not self.prom_url:
            self._load_demo("HERDECK_PROMETHEUS_URL not set")
            return
        try:
            pattern = "|".join(re.escape(k) for k in self.node_keys)
            cpu_q = f"100 - (avg by (job) (rate(node_cpu_seconds_total{{mode=\"idle\", job=~\"{pattern}\"}}[1m])) * 100)"
            cpu_res = self._query_prom(cpu_q)
            if not cpu_res:
                raise ValueError("No metrics returned from Prometheus")

            cpu_map = {r["metric"]["job"]: float(r["value"][1]) for r in cpu_res}

            l1_res = self._query_prom(f"node_load1{{job=~\"{pattern}\"}}")
            l5_res = self._query_prom(f"node_load5{{job=~\"{pattern}\"}}")
            l15_res = self._query_prom(f"node_load15{{job=~\"{pattern}\"}}")
            l1_map = {r["metric"]["job"]: float(r["value"][1]) for r in l1_res}
            l5_map = {r["metric"]["job"]: float(r["value"][1]) for r in l5_res}
            l15_map = {r["metric"]["job"]: float(r["value"][1]) for r in l15_res}

            tot_res = self._query_prom(f"node_memory_MemTotal_bytes{{job=~\"{pattern}\"}}")
            avail_res = self._query_prom(f"node_memory_MemAvailable_bytes{{job=~\"{pattern}\"}}")
            tot_map = {r["metric"]["job"]: float(r["value"][1]) for r in tot_res}
            avail_map = {r["metric"]["job"]: float(r["value"][1]) for r in avail_res}

            cores_res = self._query_prom(f"count by (job) (node_cpu_seconds_total{{mode=\"idle\", job=~\"{pattern}\"}})")
            cores_map = {r["metric"]["job"]: int(r["value"][1]) for r in cores_res}

            range_map = self._query_prom_range(cpu_q, step=60, span_s=600)

            for key in self.node_keys:
                tot_b = tot_map.get(key, 0.0)
                avail_b = avail_map.get(key, 0.0)
                used_b = max(0.0, tot_b - avail_b)
                used_gb = used_b / (1024.0 ** 3)
                tot_gb = tot_b / (1024.0 ** 3)
                mem_pct = (used_b / tot_b * 100.0) if tot_b > 0 else 0.0

                self.nodes_data[key] = {
                    "key": key,
                    "cpu_util": cpu_map.get(key, 0.0),
                    "cores": cores_map.get(key, 0),
                    "load1": l1_map.get(key, 0.0),
                    "load5": l5_map.get(key, 0.0),
                    "load15": l15_map.get(key, 0.0),
                    "mem_used_gb": used_gb,
                    "mem_total_gb": tot_gb,
                    "mem_pct": mem_pct,
                    "spark": range_map.get(key, []),
                }

            self.last_ok_time = now
            self.is_demo = False
            self.error_msg = ""
            self.status_line = "OK"

        except Exception as e:
            self.error_msg = str(e)
            if self.last_ok_time > 0:
                stale_s = int(now - self.last_ok_time)
                self.status_line = f"Stale ({stale_s}s)"
            else:
                self._load_demo("Prometheus unreachable")

    def _load_demo(self, why: str) -> None:
        self.nodes_data = get_demo_nodes(self.node_keys)
        self.is_demo = True
        self.status_line = f"DEMO ({why})"

    def get_status(self) -> str:
        return self.status_line

    def handle_key(self, ch: int) -> bool:
        if ch in (ord('j'), curses.KEY_DOWN):
            if self.node_keys:
                self.selected_idx = (self.selected_idx + 1) % len(self.node_keys)
            return True
        elif ch in (ord('k'), curses.KEY_UP):
            if self.node_keys:
                self.selected_idx = (self.selected_idx - 1 + len(self.node_keys)) % len(self.node_keys)
            return True
        return False

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        idx = my - 3
        if 0 <= idx < len(self.node_keys):
            self.selected_idx = idx
            return True
        return False

    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        y = start_y
        col_hdr = " # NODE        CPU UTIL     LOAD (1m/5m/15m)      MEMORY (RAM)           CORES  10m SPARK"
        stdscr.addstr(y, 0, col_hdr[:max_x], curses.color_pair(5) | curses.A_DIM)
        y += 1

        if not self.nodes_data:
            stdscr.addstr(y + 1, 2, "Waiting for response...", curses.color_pair(3))
            return y + 3

        for idx, key in enumerate(self.node_keys):
            if y >= max_y - 6:
                break
            node = self.nodes_data.get(key, {})
            is_selected = (idx == self.selected_idx)
            self._draw_node_row(stdscr, y, node, idx + 1, is_selected, max_x)
            y += 1

        stdscr.hline(y, 0, curses.ACS_HLINE, max_x)
        y += 1

        if 0 <= self.selected_idx < len(self.node_keys):
            sel_key = self.node_keys[self.selected_idx]
            sel_node = self.nodes_data.get(sel_key, {})
            y = self._draw_details(stdscr, y, sel_node, max_y, max_x)

        return y

    def _draw_node_row(self, stdscr, y: int, n: Dict[str, Any], num: int, selected: bool, max_x: int):
        key = n.get("key", "node")
        cpu_util = float(n.get("cpu_util") or 0.0)
        l1 = float(n.get("load1") or 0.0)
        l5 = float(n.get("load5") or 0.0)
        l15 = float(n.get("load15") or 0.0)
        mem_used = float(n.get("mem_used_gb") or 0.0)
        mem_tot = float(n.get("mem_total_gb") or 0.0)
        mem_pct = float(n.get("mem_pct") or 0.0)
        cores = int(n.get("cores") or 0)
        spark_vals = n.get("spark") or []

        num_str = f" {num}"
        name_str = f"{key:<10.10}"
        cpu_bar = make_pbar(cpu_util, 100.0, 6)
        cpu_str = f"{cpu_bar} {int(cpu_util):>2}%"
        load_str = f"{l1:>4.2f} / {l5:>4.2f} / {l15:>4.2f}"
        mem_bar = make_pbar(mem_used, mem_tot, 6)
        mem_str = f"{mem_bar} {mem_used:>4.1f}/{mem_tot:>4.1f}G ({int(mem_pct)}%)"
        cores_str = f"{cores:>2}t"
        spark = make_sparkline(spark_vals, 8)

        base_attr = curses.A_REVERSE if selected else curses.A_NORMAL
        line = f"{num_str} {name_str} {cpu_str}  {load_str}   {mem_str}   {cores_str}   {spark}"
        line = line.ljust(max_x - 1)[:max_x - 1]

        stdscr.addstr(y, 0, line, base_attr)

        if not selected:
            col_cpu = 4 if cpu_util >= 90 else (3 if cpu_util >= 70 else 2)
            stdscr.addstr(y, 14, f"{int(cpu_util):>2}%", curses.color_pair(col_cpu))
            col_mem = 4 if mem_pct >= 90 else (3 if mem_pct >= 80 else 1)
            stdscr.addstr(y, 44, f"({int(mem_pct)}%)", curses.color_pair(col_mem))

    def _draw_details(self, stdscr, start_y: int, n: Dict[str, Any], max_y: int, max_x: int) -> int:
        y = start_y
        key = n.get("key", "node")

        hdr = f" NODE INFO [{key}]: "
        stdscr.addstr(y, 0, hdr, curses.color_pair(5) | curses.A_BOLD)
        cores = n.get("cores", 1)
        stdscr.addstr(y, len(hdr), f"{cores} Cores/Threads", curses.color_pair(1))
        y += 1

        if y < max_y - 1:
            l1, l5, l15 = n.get("load1", 0.0), n.get("load5", 0.0), n.get("load15", 0.0)
            ratio = (l1 / cores * 100.0) if cores > 0 else 0.0
            stat_line = f" METRICS: Load/Capacity: {int(ratio)}% · 10m History Samples: {len(n.get('spark', []))}"
            stdscr.addstr(y, 0, stat_line[:max_x - 1], curses.color_pair(2))
            y += 1

        return y

    def print_once(self) -> None:
        self.update(force=True)
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        tag = " [DEMO]" if self.is_demo else ""
        print(f"Fleet CPU — {now_str}{tag}")
        print(f"{'#':<3} {'NODE':<12} {'CPU UTIL':<14} {'LOAD (1/5/15m)':<20} {'MEMORY (RAM)':<24} {'CORES':<8} {'10m'}")
        print("-" * 88)
        for idx, key in enumerate(self.node_keys):
            n = self.nodes_data.get(key, {})
            cpu_util = float(n.get("cpu_util") or 0.0)
            l1 = float(n.get("load1") or 0.0)
            l5 = float(n.get("load5") or 0.0)
            l15 = float(n.get("load15") or 0.0)
            mem_used = float(n.get("mem_used_gb") or 0.0)
            mem_tot = float(n.get("mem_total_gb") or 0.0)
            mem_pct = float(n.get("mem_pct") or 0.0)
            cores = int(n.get("cores") or 0)
            spark_vals = n.get("spark") or []

            num_str = f"{idx + 1}"
            cpu_bar = make_pbar(cpu_util, 100.0, 6)
            cpu_str = f"{cpu_bar} {int(cpu_util):>2}%"
            load_str = f"{l1:>4.2f}/{l5:>4.2f}/{l15:>4.2f}"
            mem_bar = make_pbar(mem_used, mem_tot, 6)
            mem_str = f"{mem_bar} {mem_used:>4.1f}/{mem_tot:>4.1f}G ({int(mem_pct)}%)"
            cores_str = f"{cores}t"
            spark = make_sparkline(spark_vals, 8)
            print(f"{num_str:<3} {key:<12} {cpu_str:<14} {load_str:<20} {mem_str:<24} {cores_str:<8} {spark}")
