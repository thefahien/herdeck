import curses
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from herdeck.common import get_ssl_context, make_pbar, make_sparkline, open_url
from herdeck.demo import get_demo_gpus
from herdeck.views.base import BaseView

GPU_URL = os.environ.get("HERDECK_GPU_URL", "")
GPU_TOKEN = os.environ.get("HERDECK_GPU_TOKEN", "")


class GPUView(BaseView):
    name = "GPUs"

    def __init__(self, cfg=None, base_url=None, refresh_interval: float = 2.0):
        super().__init__(cfg)
        self.base_url = (base_url if base_url is not None else self.cfg.get("url", GPU_URL) or "").rstrip("/")
        self.token = self.cfg.get("token", GPU_TOKEN)
        refresh_interval = float(self.cfg.get("interval", refresh_interval))
        self.interval = refresh_interval
        self.last_fetch = 0.0
        self.last_ok_time = 0.0
        self.is_demo = False
        self.state: Dict[str, Any] = {}
        self.error_msg = ""
        self.selected_idx = 0
        self.show_procs = True
        self.status_line = "Ready"
        self.switch_modal: Optional[Dict[str, Any]] = None
        self.force_switch = False
        self.ctx = get_ssl_context()

    def update(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self.last_fetch < self.interval):
            return
        self.last_fetch = now

        if not self.base_url:
            self._load_demo("not configured")
            return
        url = f"{self.base_url}/api/state"
        req = urllib.request.Request(url)
        try:
            with open_url(req, 2.0, self.ctx) as resp:
                self.state = json.loads(resp.read().decode())
                self.last_ok_time = now
                self.is_demo = False
                self.error_msg = ""
                if not self.switch_modal:
                    self.status_line = "OK"
        except Exception as e:
            self.error_msg = str(e)
            if self.last_ok_time > 0:
                stale_s = int(now - self.last_ok_time)
                self.status_line = f"Stale ({stale_s}s)"
            else:
                self._load_demo("GPU endpoint unreachable")

    def _load_demo(self, why: str) -> None:
        if not self.is_demo:
            self.state = {"gpus": get_demo_gpus()}
        self.is_demo = True
        self.status_line = f"DEMO ({why})"

    def get_status(self) -> str:
        return self.status_line

    def handle_key(self, ch: int) -> bool:
        if self.switch_modal:
            if ch in (ord('y'), ord('Y')):
                self._execute_switch()
            elif ch in (ord('n'), ord('N'), ord('q'), 27):
                self.switch_modal = None
                self.status_line = "Switch cancelled"
            elif ch in (ord('f'), ord('F')):
                self.force_switch = not self.force_switch
            elif ch in (ord('s'), 9, curses.KEY_RIGHT, curses.KEY_LEFT):
                self._cycle_lane(-1 if ch == curses.KEY_LEFT else 1)
            return True  # the prompt is modal: swallow everything else

        gpus = self.state.get("gpus", [])
        if ch in (ord('j'), curses.KEY_DOWN):
            if gpus:
                self.selected_idx = (self.selected_idx + 1) % len(gpus)
            return True
        elif ch in (ord('k'), curses.KEY_UP):
            if gpus:
                self.selected_idx = (self.selected_idx - 1 + len(gpus)) % len(gpus)
            return True
        elif ch in (10, 13):
            self.show_procs = not self.show_procs
            return True
        elif ch == ord('s'):
            self._open_lane_picker()
            return True
        return False

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        gpus = self.state.get("gpus", [])
        idx = my - 3
        if 0 <= idx < len(gpus):
            self.selected_idx = idx
            return True
        return False

    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        y = start_y
        col_hdr = " # CARD            UTIL         VRAM               TEMP   PWR    10m       ACTIVE LANE"
        stdscr.addstr(y, 0, col_hdr[:max_x], curses.color_pair(5) | curses.A_DIM)
        y += 1

        gpus = self.state.get("gpus", [])
        if not gpus:
            stdscr.addstr(y + 1, 2, "Waiting for response...", curses.color_pair(3))
            return y + 3

        for idx, g in enumerate(gpus):
            if y >= max_y - 6:
                break
            is_selected = (idx == self.selected_idx)
            self._draw_gpu_row(stdscr, y, g, idx + 1, is_selected, max_x)
            y += 1

        stdscr.hline(y, 0, curses.ACS_HLINE, max_x)
        y += 1

        if 0 <= self.selected_idx < len(gpus):
            cur_gpu = gpus[self.selected_idx]
            y = self._draw_details(stdscr, y, cur_gpu, max_y, max_x)

        if self.switch_modal:
            self._draw_switch_modal(stdscr, max_y, max_x)

        return y

    def _draw_gpu_row(self, stdscr, y: int, g: Dict[str, Any], num: int, selected: bool, max_x: int):
        card = g.get("card") or {}
        label = g.get("label", g.get("id", "Unknown"))
        util = float(card.get("util") or 0.0)
        vram_used = float(card.get("vram_used_mb") or 0.0) / 1024.0
        vram_total = float(card.get("vram_total_mb") or 0.0) / 1024.0
        temp = float(card.get("temp_c") or 0.0)
        power = float(card.get("power_w") or 0.0)
        hist = g.get("util_hist") or []
        active_lane = g.get("active") or "none"

        num_str = f" {num}"
        name_str = f"{label:<14.14}"
        util_bar = make_pbar(util, 100.0, 6)
        util_str = f"{util_bar} {int(util):>2}%"
        vram_bar = make_pbar(vram_used, vram_total, 6)
        vram_str = f"{vram_bar} {vram_used:>4.1f}/{vram_total:>4.1f}G"
        temp_str = f"{int(temp):>3}°C"
        pwr_str = f"{int(power):>3}W"
        spark = make_sparkline(hist, 8)
        lane_str = f"[{active_lane}]"

        base_attr = curses.A_REVERSE if selected else curses.A_NORMAL
        line = f"{num_str} {name_str} {util_str}  {vram_str}  {temp_str}  {pwr_str}  {spark}  {lane_str}"
        line = line.ljust(max_x - 1)[:max_x - 1]

        stdscr.addstr(y, 0, line, base_attr)
        if not selected:
            col_util = 4 if util >= 95 else (3 if util >= 80 else 2)
            stdscr.addstr(y, 18, f"{int(util):>2}%", curses.color_pair(col_util))
            col_temp = 4 if temp >= 85 else 1
            stdscr.addstr(y, 45, temp_str, curses.color_pair(col_temp))
            if active_lane != "none":
                stdscr.addstr(y, 67, lane_str[:max_x - 68], curses.color_pair(2) | curses.A_BOLD)

    def _draw_details(self, stdscr, start_y: int, g: Dict[str, Any], max_y: int, max_x: int) -> int:
        y = start_y
        lanes = g.get("lanes", [])
        active_lane = g.get("active")
        procs = g.get("procs", [])

        lane_hdr = f" LANES for {g.get('label')}: "
        stdscr.addstr(y, 0, lane_hdr, curses.color_pair(5) | curses.A_BOLD)
        x = len(lane_hdr)

        if not lanes:
            stdscr.addstr(y, x, "(no switchable lanes)", curses.color_pair(8))
            y += 1
        else:
            for l_idx, lane in enumerate(lanes):
                l_id = lane.get("id", "")
                is_active = (l_id == active_lane)
                l_tag = f" {lane.get('label', l_id)} "
                if is_active:
                    l_tag += "●active "
                if x + len(l_tag) < max_x:
                    pair = curses.color_pair(7) if is_active else curses.color_pair(1)
                    stdscr.addstr(y, x, l_tag, pair)
                    x += len(l_tag) + 1
            y += 1

        if self.show_procs and y < max_y - 2:
            proc_hdr = " PROCESSES: "
            stdscr.addstr(y, 0, proc_hdr, curses.color_pair(5))
            if not procs:
                stdscr.addstr(y, len(proc_hdr), "none active (idle)", curses.color_pair(2))
                y += 1
            else:
                proc_strs = [f"{os.path.basename(p.get('name', 'proc'))} ({p.get('mem', '')})" for p in procs]
                joined = " · ".join(proc_strs)
                stdscr.addstr(y, len(proc_hdr), joined[:max_x - len(proc_hdr) - 1], curses.color_pair(3))
                y += 1
        return y

    def _open_lane_picker(self):
        gpus = self.state.get("gpus", [])
        if not gpus or not (0 <= self.selected_idx < len(gpus)):
            return
        cur_gpu = gpus[self.selected_idx]
        lanes = cur_gpu.get("lanes", [])
        if not lanes:
            return
        active = cur_gpu.get("active")
        target = next((l for l in lanes if l.get("id") != active), lanes[0])
        self._prompt_switch(cur_gpu, target)

    def _cycle_lane(self, step: int) -> None:
        m = self.switch_modal
        gpus = self.state.get("gpus", [])
        gpu = next((x for x in gpus if x.get("id") == m["gpu_id"]), None)
        if not gpu:
            return
        choices = [l for l in gpu.get("lanes", []) if l.get("id") != gpu.get("active")]
        if len(choices) < 2:
            return
        ids = [l.get("id") for l in choices]
        i = ids.index(m["lane_id"]) if m["lane_id"] in ids else 0
        nxt = choices[(i + step) % len(choices)]
        m["lane_id"], m["lane_label"] = nxt.get("id"), nxt.get("label")

    def _prompt_switch(self, gpu: Dict[str, Any], lane: Dict[str, Any]):
        if lane.get("id") == gpu.get("active"):
            self.status_line = f"'{lane.get('label')}' is already active"
            return
        self.switch_modal = {
            "gpu_id": gpu.get("id"),
            "gpu_label": gpu.get("label"),
            "lane_id": lane.get("id"),
            "lane_label": lane.get("label"),
            "procs": gpu.get("procs", []),
        }

    def _draw_switch_modal(self, stdscr, max_y: int, max_x: int):
        m = self.switch_modal
        if not m:
            return
        card_lbl = m["gpu_label"]
        lane_lbl = m["lane_label"]
        has_procs = bool(m.get("procs"))

        box_w = min(max_x - 4, 76)
        box_h = 7 if has_procs else 5
        start_y = max(2, (max_y - box_h) // 2)
        start_x = max(2, (max_x - box_w) // 2)

        for y in range(start_y, start_y + box_h):
            stdscr.addstr(y, start_x, " " * box_w, curses.color_pair(6))

        title = f" CONFIRM LANE SWITCH: {card_lbl} "
        stdscr.addstr(start_y, start_x + 2, title, curses.color_pair(6) | curses.A_BOLD)
        q_line = f"Switch to [{lane_lbl}]?  y confirm · n/Esc cancel · s next lane"
        stdscr.addstr(start_y + 2, start_x + 2, q_line, curses.color_pair(6) | curses.A_BOLD)

        if has_procs:
            warn = f"Warning: {len(m['procs'])} process(es) active! May stop running work."
            force_tag = f" [f] Force: {'ON' if self.force_switch else 'OFF'}"
            stdscr.addstr(start_y + 4, start_x + 2, (warn + force_tag)[:box_w - 4], curses.color_pair(6))

    def _execute_switch(self):
        m = self.switch_modal
        self.switch_modal = None
        if not m:
            return
        if self.is_demo:
            # Simulate switch in demo mode
            for g in self.state.get("gpus", []):
                if g.get("id") == m["gpu_id"]:
                    g["active"] = m["lane_id"]
            self.status_line = f"Demo: switched {m['gpu_label']} to {m['lane_label']}"
            return

        key = self.token
        if not key:
            self.status_line = "Switch needs a token (token = ... in tabs.toml)"
            return
        host = urllib.parse.urlparse(self.base_url)
        if host.scheme != "https" and host.hostname not in ("localhost", "127.0.0.1", "::1"):
            self.status_line = "Refusing to send token over plain http to a remote host"
            return

        gpu_id, lane_id, force = m["gpu_id"], m["lane_id"], self.force_switch
        self.status_line = f"Switching {m['gpu_label']} to {m['lane_label']}..."
        url = f"{self.base_url}/api/switch"
        payload = json.dumps({"gpu": gpu_id, "lane": lane_id, "force": force}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with open_url(req, 4.0, self.ctx) as resp:
                res = json.loads(resp.read().decode())
                op_id = res.get("op")
                self.status_line = f"Switch initiated (op {op_id}). Refreshing..."
                time.sleep(0.5)
                self.update(force=True)
        except Exception as e:
            self.status_line = f"Switch error: {e}"

    def print_once(self) -> None:
        self.update(force=True)
        gpus = self.state.get("gpus", [])
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        tag = " [DEMO]" if self.is_demo else ""
        print(f"GPUs — {now_str}{tag}")
        print(f"{'#':<3} {'CARD':<16} {'UTIL':<14} {'VRAM':<18} {'TEMP':<7} {'PWR':<6} {'10m':<10} {'ACTIVE LANE'}")
        print("-" * 88)
        for idx, g in enumerate(gpus):
            card = g.get("card") or {}
            label = g.get("label", g.get("id", "Unknown"))
            util = float(card.get("util") or 0.0)
            vram_used = float(card.get("vram_used_mb") or 0.0) / 1024.0
            vram_total = float(card.get("vram_total_mb") or 0.0) / 1024.0
            temp = float(card.get("temp_c") or 0.0)
            power = float(card.get("power_w") or 0.0)
            hist = g.get("util_hist") or []
            active_lane = g.get("active") or "none"
            num_str = f"{idx + 1}"
            util_str = f"{make_pbar(util, 100.0, 6)} {int(util):>2}%"
            vram_str = f"{make_pbar(vram_used, vram_total, 6)} {vram_used:>4.1f}/{vram_total:>4.1f}G"
            temp_str = f"{int(temp):>3}°C"
            pwr_str = f"{int(power):>3}W"
            spark = make_sparkline(hist, 8)
            lane_str = f"[{active_lane}]"
            print(f"{num_str:<3} {label:<16} {util_str:<14} {vram_str:<18} {temp_str:<7} {pwr_str:<6} {spark:<10} {lane_str}")
