"""View contract, plus TableView: the base most tab types build on.

A view is one tab. The app calls ``update(force)`` from a background thread and
``draw()``, ``handle_key()`` and ``handle_mouse_click()`` from the UI thread.
``update`` may block on I/O; it must never touch the screen.
"""
import curses
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from herdeck.common import make_pbar, make_sparkline


class BaseView(ABC):
    #: tab title; overridden by the ``name`` key of the tab's config
    name: str = "Tab"

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = dict(cfg or {})
        if self.cfg.get("name"):
            self.name = str(self.cfg["name"])

    @abstractmethod
    def update(self, force: bool = False) -> None:
        """Fetch fresh data (runs off the UI thread)."""

    @abstractmethod
    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        """Render rows start_y..max_y-1. Returns the next free row."""

    def handle_key(self, ch: int) -> bool:
        """Return True if the key was used (the app then ignores it)."""
        return False

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        return False

    def get_status(self) -> str:
        return "OK"

    def snapshot(self) -> List[str]:
        """Plain-text lines for ``herdeck --once``."""
        return []

    def print_once(self) -> None:
        self.update(force=True)
        print(f"## {self.name}" + ("  [DEMO]" if getattr(self, "is_demo", False) else ""))
        for line in self.snapshot():
            print(line)


# ---------------------------------------------------------------- cells
class Bar:
    """A cell drawn as a meter: ``Bar(value, max, text=None)``."""

    def __init__(self, value: float, maximum: float = 100.0, text: Optional[str] = None, width: int = 10):
        self.value, self.maximum, self.width = float(value or 0), float(maximum or 0), width
        if text is None:
            text = f"{self.value:.0f}%" if self.maximum == 100 else f"{self.value:g}"
        self.text = text

    def ratio(self) -> float:
        return 0.0 if self.maximum <= 0 else max(0.0, min(1.0, self.value / self.maximum))

    def render(self) -> str:
        return f"{make_pbar(self.value, self.maximum, self.width)} {self.text:>5}"


class Spark:
    def __init__(self, values: Sequence[float], width: int = 10, maximum: Optional[float] = 100.0):
        self.values, self.width, self.maximum = [float(v) for v in values], width, maximum

    def render(self) -> str:
        top = self.maximum or max(self.values or [1.0]) or 1.0
        return make_sparkline([v / top * 100 for v in self.values], self.width)


class Text:
    """Text with a tone: 'ok', 'warn', 'bad', 'accent', 'dim' or None."""

    def __init__(self, text: Any, tone: Optional[str] = None):
        self.text, self.tone = str(text), tone

    def render(self) -> str:
        return self.text


TONES = {"ok": 2, "warn": 3, "bad": 4, "accent": 5, "dim": 8}


def cell_text(c: Any) -> str:
    return c.render() if hasattr(c, "render") else ("" if c is None else str(c))


def cell_attr(c: Any) -> int:
    if isinstance(c, Bar):
        r = c.ratio()
        return curses.color_pair(4 if r >= 0.9 else 3 if r >= 0.7 else 2)
    if isinstance(c, Text) and c.tone in TONES:
        return curses.color_pair(TONES[c.tone]) | (curses.A_BOLD if c.tone == "bad" else 0)
    if isinstance(c, Spark):
        return curses.color_pair(5)
    return curses.A_NORMAL


# ---------------------------------------------------------------- table
class TableView(BaseView):
    """Rows and columns with selection, scrolling, mouse, stale and demo handling.

    Subclasses implement ``fetch()`` returning ``(columns, rows)``: columns is a
    list of titles, rows a list of lists of cells (str, Text, Bar, Spark). They may
    override ``configured()``, ``demo()``, ``header()`` and ``detail(row)``.
    """

    default_interval = 5.0

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.interval = float(self.cfg.get("interval", self.default_interval))
        self.columns: List[str] = []
        self.rows: List[List[Any]] = []
        self.selected = 0
        self.scroll = 0
        self.last_fetch = 0.0
        self.last_ok = 0.0
        self.is_demo = False
        self.status = "loading"
        self._table_top = 0
        self._visible = 0

    # -- data
    def fetch(self):  # pragma: no cover - abstract
        raise NotImplementedError

    def configured(self) -> bool:
        return True

    def demo(self):
        """(columns, rows) shown while the source is not configured, or None."""
        return None

    def update(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_fetch < self.interval:
            return
        self.last_fetch = now
        if not self.configured():
            d = self.demo()
            if d:
                self.columns, self.rows = d
                self.is_demo, self.status = True, "DEMO (not configured)"
            else:
                self.status = "not configured"
            return
        try:
            self.columns, self.rows = self.fetch()
            self.last_ok, self.is_demo, self.status = now, False, "OK"
        except Exception as e:  # keep the last good rows and say why
            msg = (str(e).splitlines() or [type(e).__name__])[0][:60]
            self.status = f"Stale {int(now - self.last_ok)}s: {msg}" if self.last_ok else f"error: {msg}"
        self.selected = min(self.selected, max(0, len(self.rows) - 1))

    def get_status(self) -> str:
        return self.status

    def header(self) -> str:
        return ""

    def detail(self, row: List[Any]) -> str:
        return ""

    # -- input
    def handle_key(self, ch: int) -> bool:
        n = len(self.rows)
        if not n:
            return False
        page = max(1, self._visible)
        step = {ord("j"): 1, curses.KEY_DOWN: 1, ord("k"): -1, curses.KEY_UP: -1,
                curses.KEY_NPAGE: page, curses.KEY_PPAGE: -page}.get(ch)
        if step is not None:
            self.selected = max(0, min(n - 1, self.selected + step))
            return True
        if ch in (ord("g"), curses.KEY_HOME):
            self.selected = 0
            return True
        if ch in (ord("G"), curses.KEY_END):
            self.selected = n - 1
            return True
        return False

    def handle_mouse_click(self, mx: int, my: int) -> bool:
        i = self.scroll + (my - self._table_top)
        if self._table_top <= my < self._table_top + self._visible and 0 <= i < len(self.rows):
            self.selected = i
            return True
        return False

    # -- drawing
    def _widths(self, max_x: int) -> List[int]:
        n = len(self.columns)
        w = [len(t) for t in self.columns]
        for r in self.rows[:300]:
            for i in range(n):
                w[i] = max(w[i], len(cell_text(r[i] if i < len(r) else "")))
        w = [min(x, 60) for x in w]
        over = sum(w) + 2 * n + 1 - max_x
        for i in sorted(range(n), key=lambda i: -w[i]):  # shrink the widest first
            if over <= 0:
                break
            cut = min(over, max(0, w[i] - 6))
            w[i] -= cut
            over -= cut
        return w

    def draw(self, stdscr, start_y: int, max_y: int, max_x: int) -> int:
        y = start_y
        head = self.header()
        if head:
            stdscr.addstr(y, 1, head[: max_x - 2], curses.color_pair(5) | curses.A_BOLD)
            y += 1
        if not self.rows:
            msg = "loading…" if self.status == "loading" else self.status
            stdscr.addstr(y + 1, 2, msg, curses.color_pair(3))
            return y + 2
        widths = self._widths(max_x)
        x = 1
        for t, w in zip(self.columns, widths):
            stdscr.addstr(y, x, t[:w].ljust(w), curses.color_pair(5) | curses.A_DIM)
            x += w + 2
        y += 1
        sel_row = self.rows[self.selected] if 0 <= self.selected < len(self.rows) else None
        detail = self.detail(sel_row) if sel_row is not None else ""
        self._table_top = y
        self._visible = max(1, max_y - y - (2 if detail else 0))
        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + self._visible:
            self.scroll = self.selected - self._visible + 1
        for i in range(self.scroll, min(len(self.rows), self.scroll + self._visible)):
            row, sel = self.rows[i], i == self.selected
            if sel:
                stdscr.addstr(y, 0, " " * (max_x - 1), curses.A_REVERSE)
            x = 1
            for j, w in enumerate(widths):
                c = row[j] if j < len(row) else ""
                txt = cell_text(c)
                txt = (txt[: w - 1] + "…") if len(txt) > w else txt.ljust(w)
                stdscr.addstr(y, x, txt, curses.A_REVERSE if sel else cell_attr(c))
                x += w + 2
            y += 1
        if len(self.rows) > self._visible:
            pos = f"{self.selected + 1}/{len(self.rows)}"
            stdscr.addstr(start_y, max_x - len(pos) - 2, pos, curses.color_pair(8))
        if detail:
            stdscr.hline(y, 0, curses.ACS_HLINE, max_x)
            stdscr.addstr(y + 1, 1, detail[: max_x - 2], curses.color_pair(3))
            y += 2
        return y

    def snapshot(self) -> List[str]:
        if not self.rows:
            return [f"  ({self.status})"]
        widths = self._widths(160)
        out = ["  " + "  ".join(t.ljust(w) for t, w in zip(self.columns, widths))]
        for r in self.rows:
            out.append("  " + "  ".join(cell_text(r[i] if i < len(r) else "")[:w].ljust(w)
                                        for i, w in enumerate(widths)).rstrip())
        return out
