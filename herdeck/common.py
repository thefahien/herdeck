import curses
import os
import ssl
import urllib.request
from typing import List

# Reduce Esc key delay in curses (default is 1000ms)
os.environ.setdefault("ESCDELAY", "25")

SPARKS = " ▂▃▄▅▆▇█"


def get_ssl_context(allow_insecure_optin: bool = True) -> ssl.SSLContext:
    """Default to secure TLS verification; only skip if explicitly opted in via env."""
    insecure = allow_insecure_optin and (os.environ.get("HERDECK_INSECURE_TLS", "0") == "1")
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def make_sparkline(vals: List[float], width: int = 8) -> str:
    if not vals:
        return "░" * width
    tail = vals[-width:] if len(vals) >= width else vals
    chars = []
    for v in tail:
        clamped = max(0.0, min(100.0, float(v)))
        idx = int((clamped / 100.0) * (len(SPARKS) - 1))
        chars.append(SPARKS[idx])
    return "".join(chars).ljust(width)


def make_pbar(val: float, max_val: float, width: int = 6) -> str:
    if max_val <= 0:
        return "░" * width
    ratio = max(0.0, min(1.0, float(val) / float(max_val)))
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    try:
        curses.init_pair(1, curses.COLOR_WHITE, -1)     # Normal
        curses.init_pair(2, curses.COLOR_GREEN, -1)     # Low / Good / Active
        curses.init_pair(3, curses.COLOR_YELLOW, -1)    # Med / Warning
        curses.init_pair(4, curses.COLOR_RED, -1)       # High / Alert / Error
        curses.init_pair(5, curses.COLOR_CYAN, -1)      # Header / Title / Nav
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)   # Selected row / highlight
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_GREEN)  # Active tab badge
        curses.init_pair(8, curses.COLOR_MAGENTA, -1)   # Accent / Dim
    except Exception:
        pass


class SafeWin:
    """Wraps a curses window so drawing past the edge clips instead of raising.

    Views draw with fixed column offsets; on a narrow pane those land outside the
    window and curses raises. Clip to the window and ignore the error from the
    bottom-right cell instead.
    """

    def __init__(self, win):
        self._win = win

    def __getattr__(self, name):
        return getattr(self._win, name)

    def addstr(self, y, x, text, attr=0):
        max_y, max_x = self._win.getmaxyx()
        if y < 0 or y >= max_y or x < 0 or x >= max_x:
            return
        try:
            self._win.addstr(y, x, str(text)[: max_x - x], attr)
        except curses.error:
            pass

    def hline(self, y, x, ch, n):
        max_y, max_x = self._win.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x:
            try:
                self._win.hline(y, x, ch, min(n, max_x - x))
            except curses.error:
                pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: urllib would resend the Authorization header to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_url(req, timeout: float, context: ssl.SSLContext):
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect())
    return opener.open(req, timeout=timeout)
