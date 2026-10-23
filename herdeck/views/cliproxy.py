import collections
import curses
import datetime as dt
import json
import os
import re
import shlex
import ssl
import subprocess
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from herdeck.common import get_ssl_context, make_pbar, open_url
from herdeck.demo import get_demo_cliproxy
from herdeck.views.base import BaseView

CLIPROXY_SSH = os.environ.get("HERDECK_CLIPROXY_SSH", "")  # ssh target of the docker host, e.g. user@host
CLIPROXY_CONTAINER = os.environ.get("HERDECK_CLIPROXY_CONTAINER", "cli-proxy-api")
CLIPROXY_AUTH_DIR = os.environ.get("HERDECK_CLIPROXY_AUTH_DIR", "/root/.cli-proxy-api")


def format_iso_time(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        t = dt.datetime.fromisoformat(re.sub(r"\.\d+", "", iso_str).replace("Z", "+00:00"))
        now = dt.datetime.now(dt.timezone.utc)
        if t.date() == now.date():
            return f"resets {t.strftime('%H:%M')} UTC"
        return f"↻ {t.strftime('%a %b %d %H:%M')} UTC"
    except Exception:
        return iso_str[:16]


class CLIProxyView(BaseView):
    name = "AI quotas"

    def __init__(self, cfg=None, refresh_interval: float = 30.0):
        super().__init__(cfg)
        self.interval = float(self.cfg.get("interval", refresh_interval))
        self.ssh_target = self.cfg.get("ssh", CLIPROXY_SSH)
        self.container = self.cfg.get("container", CLIPROXY_CONTAINER)
        self.auth_dir = self.cfg.get("auth_dir", CLIPROXY_AUTH_DIR)
        self.last_fetch = 0.0
        self.last_ok_time = 0.0
        self.is_fetching = False
        self.is_demo = False
        self.status_line = "Ready"

        # Quota data for all 3 providers
        self.claude_data = {"5h": 0.0, "7d": 0.0, "5h_reset": "", "7d_reset": ""}
        self.codex_data = {"plan": "Plus", "primary": 0.0, "secondary": 0.0, "allowed": True}
        self.agy_data = {"gemini": 0.0, "gemini_reset": "", "other": 0.0, "other_reset": ""}

        self.model_counts: List[Tuple[str, int]] = []
        self.post_stats: Dict[str, int] = {}
        self.total_reqs = 0
        self.selected_idx = 0
        self.ctx = get_ssl_context(allow_insecure_optin=False)
        self.table_start_y = 11

    def update(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self.last_fetch < self.interval):
            return
        if self.is_fetching:
            return

        self.last_fetch = now
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _ssh(self, cmd: str, timeout: int = 8) -> bytes:
        return subprocess.check_output(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "--", self.ssh_target, cmd],
            timeout=timeout, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def _read_auth(self, prefix: str) -> Dict[str, Any]:
        """First auth file named <prefix>-*.json in the proxy's auth dir (inside the container)."""
        if not re.fullmatch(r"[a-z]+", prefix):
            raise ValueError("bad prefix")
        c = shlex.quote(self.container)
        inner = f"ls -1 {shlex.quote(self.auth_dir)}/{prefix}-*.json 2>/dev/null | head -n1"
        cmd = f'f=$(docker exec {c} sh -c {shlex.quote(inner)}); [ -n "$f" ] && docker exec {c} cat "$f"'
        auth = json.loads(self._ssh(cmd))
        if not auth.get("access_token"):
            raise ValueError("auth file has no access_token")
        return auth

    def _get_json(self, url: str, headers: Dict[str, str], data: Optional[bytes] = None) -> Dict[str, Any]:
        req = urllib.request.Request(url, data=data, headers=headers)
        with open_url(req, 5, self.ctx) as r:
            return json.load(r)

    def _fetch_worker(self):
        self.is_fetching = True
        try:
            if not self.ssh_target:
                self._load_demo("not configured")
                return

            c_data = {"5h": 0.0, "7d": 0.0, "5h_reset": "", "7d_reset": ""}
            cx_data = {"plan": "-", "primary": 0.0, "secondary": 0.0, "allowed": True}
            ag_data = {"gemini": 0.0, "gemini_reset": "", "other": 0.0, "other_reset": ""}
            models: collections.Counter = collections.Counter()
            posts: collections.Counter = collections.Counter()
            ok, failed = [], []

            # Claude: plan usage meters (5-hour + weekly)
            try:
                tok = self._read_auth("claude").get("access_token")
                d = self._get_json("https://api.anthropic.com/oauth/usage",
                                   {"Authorization": f"Bearer {tok}", "Accept": "application/json"})
                c_data.update({
                    "5h": float((d.get("five_hour") or {}).get("utilization") or 0.0),
                    "7d": float((d.get("seven_day") or {}).get("utilization") or 0.0),
                    "5h_reset": (d.get("five_hour") or {}).get("resets_at") or "",
                    "7d_reset": (d.get("seven_day") or {}).get("resets_at") or "",
                })
                ok.append("claude")
            except Exception:
                failed.append("claude")

            # Codex (ChatGPT plan)
            try:
                auth = self._read_auth("codex")
                hdrs = {"Authorization": f"Bearer {auth.get('access_token')}", "Accept": "application/json",
                        "User-Agent": "herdeck"}
                if auth.get("account_id"):
                    hdrs["chatgpt-account-id"] = auth["account_id"]
                res = self._get_json("https://chatgpt.com/backend-api/wham/usage", hdrs)
                rl = res.get("rate_limit") or {}
                cx_data.update({
                    "plan": str(res.get("plan_type") or "-").capitalize(),
                    "allowed": rl.get("allowed", True),
                    "primary": float((rl.get("primary_window") or {}).get("used_percent") or 0.0),
                    "secondary": float((rl.get("secondary_window") or {}).get("used_percent") or 0.0),
                })
                ok.append("codex")
            except Exception:
                failed.append("codex")

            # Antigravity (Google): Gemini pool + Claude/GPT pool
            try:
                tok = self._read_auth("antigravity").get("access_token")
                hdrs = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                        "Accept": "application/json", "User-Agent": "antigravity"}
                base = "https://cloudcode-pa.googleapis.com/v1internal:"
                d1 = self._get_json(base + "retrieveUserQuotaSummary", hdrs, b"{}")
                for grp in d1.get("groups", []):
                    for bk in grp.get("buckets", []):
                        if bk.get("bucketId") == "3p-weekly" and bk.get("remainingFraction") is not None:
                            ag_data["other"] = round((1.0 - float(bk["remainingFraction"])) * 100.0, 1)
                            ag_data["other_reset"] = bk.get("resetTime") or ""
                d2 = self._get_json(base + "retrieveUserQuota", hdrs, b"{}")
                lowest, rst = 1.0, ""
                for bk in d2.get("buckets", []):
                    if str(bk.get("modelId", "")).startswith("gemini") and bk.get("remainingFraction") is not None:
                        rem = float(bk["remainingFraction"])
                        if rem < lowest:
                            lowest, rst = rem, bk.get("resetTime") or ""
                ag_data["gemini"] = round((1.0 - lowest) * 100.0, 1)
                ag_data["gemini_reset"] = rst
                ok.append("antigravity")
            except Exception:
                failed.append("antigravity")

            # 24h traffic from the proxy's own log
            try:
                raw = self._ssh(f"docker logs --since 24h {shlex.quote(self.container)} 2>&1", timeout=15).decode(errors="replace")
                for ln in raw.splitlines():
                    if "selector.go" in ln and "session-affinity" in ln:
                        mm = re.search(r"model=(\S+)", ln)
                        if mm:
                            models[mm.group(1)] += 1
                    elif "gin_logger" in ln and "POST" in ln:
                        code = re.search(r"\] (\d{3}) \|", ln)
                        if code:
                            posts[code.group(1)] += 1
                ok.append("logs")
            except Exception:
                failed.append("logs")

            if not ok:
                raise RuntimeError("every source failed")

            self.claude_data, self.codex_data, self.agy_data = c_data, cx_data, ag_data
            self.model_counts = models.most_common(12)
            self.total_reqs = sum(models.values())
            self.post_stats = dict(posts)
            self.last_ok_time = time.time()
            self.is_demo = False
            self.status_line = "OK" if not failed else "partial: " + ",".join(failed) + " failed"

        except Exception:
            if self.last_ok_time > 0:
                self.status_line = f"Stale ({int(time.time() - self.last_ok_time)}s)"
            else:
                self._load_demo("proxy unreachable")
        finally:
            self.is_fetching = False

    def _load_demo(self, why: str = "not configured"):
        self.is_demo = True
        d = get_demo_cliproxy()
        self.claude_data = d["claude"]
        self.codex_data = d["codex"]
        self.agy_data = d["antigravity"]
        self.model_counts = d["models"]
        self.total_reqs = sum(c for _, c in d["models"])
        self.post_stats = d["posts"]
        self.status_line = f"DEMO ({why})"

    def get_status(self) -> str:
        if self.is_fetching:
            return "Refreshing..."
        return self.status_line

    def handle_key(self, ch: int) -> bool:
        if ch in (ord('j'), curses.KEY_DOWN):
            if self.model_counts:
                self.selected_idx = (self.selected_idx + 1) % len(self.model_counts)
            return True
        elif ch in (ord('k'), curses.KEY_UP):
            if self.model_counts:
                self.selected_idx = (self.selected_idx - 1 + len(self.model_counts)) % len(self.model_counts)
            return True
        return False

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        idx = my - self.table_start_y
        if 0 <= idx < len(self.model_counts):
            self.selected_idx = idx
            return True
        return False

    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        y = start_y
        W = 16

        sec_hdr = " SUBSCRIPTION PLAN QUOTAS (LIVE FROM PROVIDERS) "
        stdscr.addstr(y, 0, sec_hdr, curses.color_pair(5) | curses.A_BOLD)
        y += 1

        c_5h = self.claude_data.get("5h", 0.0)
        c_7d = self.claude_data.get("7d", 0.0)
        c_5h_rst = format_iso_time(self.claude_data.get("5h_reset", ""))
        c_7d_rst = format_iso_time(self.claude_data.get("7d_reset", ""))
        self._draw_meter_line(stdscr, y, "CLAUDE", "5-Hour", c_5h, W, c_5h_rst, max_x)
        y += 1
        self._draw_meter_line(stdscr, y, " (Anthropic)", "Weekly", c_7d, W, c_7d_rst, max_x)
        y += 1

        cx_p = self.codex_data.get("primary", 0.0)
        cx_s = self.codex_data.get("secondary", 0.0)
        plan_str = f"Plan: {self.codex_data.get('plan', 'Plus')}"
        self._draw_meter_line(stdscr, y, "CODEX", "5-Hour", cx_p, W, plan_str, max_x)
        y += 1
        self._draw_meter_line(stdscr, y, " (OpenAI)", "Weekly", cx_s, W, "● Active" if self.codex_data.get("allowed", True) else "✖ Limited", max_x)
        y += 1

        ag_gemini = self.agy_data.get("gemini", 0.0)
        ag_other = self.agy_data.get("other", 0.0)
        ag_g_rst = format_iso_time(self.agy_data.get("gemini_reset", ""))
        ag_o_rst = format_iso_time(self.agy_data.get("other_reset", ""))
        self._draw_meter_line(stdscr, y, "ANTIGRAVITY", "Gemini", ag_gemini, W, ag_g_rst, max_x)
        y += 1
        self._draw_meter_line(
            stdscr,
            y,
            " (Google)",
            "Cl/GPT",
            ag_other,
            W,
            ag_o_rst + ("  [EXHAUSTED]" if ag_other >= 99.0 else ""),
            max_x,
            is_exhausted=(ag_other >= 99.0),
        )
        y += 1

        stdscr.hline(y, 0, curses.ACS_HLINE, max_x)
        y += 1

        self.table_start_y = y + 1
        col_hdr = f" # MODEL                     REQUESTS (24h)    SHARE %     TOTAL: {self.total_reqs}"
        stdscr.addstr(y, 0, col_hdr[:max_x], curses.color_pair(5) | curses.A_DIM)
        y += 1

        if not self.model_counts:
            stdscr.addstr(y + 1, 2, "No model traffic recorded...", curses.color_pair(3))
            return y + 3

        for idx, (m_name, count) in enumerate(self.model_counts):
            if y >= max_y - 2:
                break
            is_sel = (idx == self.selected_idx)
            share_pct = (count / self.total_reqs * 100.0) if self.total_reqs > 0 else 0.0
            pbar = make_pbar(count, self.total_reqs, 8)

            num_str = f" {idx + 1}"
            name_str = f"{m_name:<25.25}"
            cnt_str = f"{count:>6d}"
            share_str = f"[{pbar}] {int(share_pct):>2}%"

            base_attr = curses.A_REVERSE if is_sel else curses.A_NORMAL
            line = f"{num_str} {name_str} {cnt_str}       {share_str}".ljust(max_x - 1)[:max_x - 1]
            stdscr.addstr(y, 0, line, base_attr)

            if not is_sel:
                stdscr.addstr(y, 30, cnt_str, curses.color_pair(5) | curses.A_BOLD)
                stdscr.addstr(y, 45, f"{int(share_pct):>2}%", curses.color_pair(2))
            y += 1

        p200 = self.post_stats.get("200", 0)
        p429 = self.post_stats.get("429", 0)
        stat_line = f" TRAFFIC (24h): 200 OK: {p200} · 429 RateLimit: {p429} · Total Calls: {self.total_reqs}"
        if y < max_y:
            stdscr.addstr(y, 0, stat_line[:max_x - 1], curses.color_pair(1) | curses.A_DIM)

        return y + 1

    def _draw_meter_line(
        self,
        stdscr,
        y: int,
        prov: str,
        pool: str,
        used_pct: float,
        bar_w: int,
        note: str,
        max_x: int,
        is_exhausted: bool = False,
    ):
        bar = make_pbar(used_pct, 100.0, bar_w)
        line = f" {prov:<14.14} {pool:<8.8}  ▕{bar}▏ {int(used_pct):>3}%   {note}"
        line = line.ljust(max_x - 1)[:max_x - 1]

        stdscr.addstr(y, 0, line, curses.color_pair(1))

        col = 4 if used_pct >= 90 or is_exhausted else (3 if used_pct >= 60 else 2)
        stdscr.addstr(y, 26, f"▕{bar}▏", curses.color_pair(col))
        stdscr.addstr(y, 26 + bar_w + 3, f"{int(used_pct):>3}%", curses.color_pair(col) | curses.A_BOLD)

        if is_exhausted:
            pos = line.find("[EXHAUSTED]")
            if pos != -1:
                stdscr.addstr(y, pos, "[EXHAUSTED]", curses.color_pair(4) | curses.A_BOLD)

    def print_once(self) -> None:
        self._fetch_worker()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        tag = " [DEMO]" if self.is_demo else ""
        print(f"Subscription Quotas & Proxy Usage — {now_str}{tag}")
        print("--------------------------------------------------------------------------------")
        print(f"CLAUDE (5-Hour):     [{make_pbar(self.claude_data.get('5h', 0.0), 100.0, 10)}] {int(self.claude_data.get('5h', 0.0))}% used   {format_iso_time(self.claude_data.get('5h_reset', ''))}")
        print(f"CLAUDE (Weekly):     [{make_pbar(self.claude_data.get('7d', 0.0), 100.0, 10)}] {int(self.claude_data.get('7d', 0.0))}% used   {format_iso_time(self.claude_data.get('7d_reset', ''))}")
        print(f"CODEX ({self.codex_data.get('plan', '-')} 5-Hour): [{make_pbar(self.codex_data.get('primary', 0.0), 100.0, 10)}] {int(self.codex_data.get('primary', 0.0))}% used")
        print(f"CODEX (Weekly):      [{make_pbar(self.codex_data.get('secondary', 0.0), 100.0, 10)}] {int(self.codex_data.get('secondary', 0.0))}% used")
        print(f"ANTIGRAVITY (Gemini):[{make_pbar(self.agy_data.get('gemini', 0.0), 100.0, 10)}] {int(self.agy_data.get('gemini', 0.0))}% used   {format_iso_time(self.agy_data.get('gemini_reset', ''))}")
        other_ex = "  [EXHAUSTED]" if self.agy_data.get("other", 0.0) >= 99.0 else ""
        print(f"ANTIGRAVITY (Cl/GPT):[{make_pbar(self.agy_data.get('other', 0.0), 100.0, 10)}] {int(self.agy_data.get('other', 0.0))}% used   {format_iso_time(self.agy_data.get('other_reset', ''))}{other_ex}")
        print("--------------------------------------------------------------------------------")
        print(f"{'#':<3} {'MODEL':<28} {'REQUESTS (24h)':<16} {'SHARE %'}")
        print("-" * 65)
        for idx, (model_name, count) in enumerate(self.model_counts):
            share_pct = (count / self.total_reqs * 100.0) if self.total_reqs > 0 else 0.0
            print(f"{idx + 1:<3} {model_name:<28} {count:>8d}         [{make_pbar(count, self.total_reqs, 6)}] {int(share_pct)}%")
        print("--------------------------------------------------------------------------------")
        p200 = self.post_stats.get("200", 0)
        p429 = self.post_stats.get("429", 0)
        print(f"Traffic: 200 OK: {p200} | 429 RateLimit: {p429} | Total Calls: {self.total_reqs}")
