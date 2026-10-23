import curses
import threading
import time
from typing import List, Tuple

from herdeck.common import SafeWin, init_colors
from herdeck.views.base import BaseView

WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0x10000)
WHEEL_DN = getattr(curses, "BUTTON5_PRESSED", 0x200000)


class DashboardApp:
    def __init__(self, views: List[BaseView], refresh_interval: float = 2.0, title: str = "herdeck"):
        self.views = views
        self.title = title
        self.active_tab = 0
        self.interval = refresh_interval
        self.show_help = False
        self.tab_spans: List[Tuple[int, int, int]] = []  # (x_start, x_end, tab_idx)
        self._updating = set()

    def _bg_update(self, view: BaseView, force: bool = False) -> None:
        """Fetch off the UI thread so a slow or dead endpoint never freezes keys."""
        if id(view) in self._updating:
            return
        self._updating.add(id(view))

        def run():
            try:
                view.update(force=force)
            except Exception:
                pass
            finally:
                self._updating.discard(id(view))

        threading.Thread(target=run, daemon=True).start()

    def run(self):
        curses.wrapper(self._main_loop)

    def _main_loop(self, raw_stdscr):
        stdscr = SafeWin(raw_stdscr)
        init_colors()
        curses.curs_set(0)
        stdscr.timeout(max(100, min(2000, int(self.interval * 1000))))

        # Enable mouse support in curses without flood-motion reporting
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
            curses.mouseinterval(0)
            print("\033[?1003l", end="", flush=True)  # disable any-motion tracking
        except Exception:
            pass

        # Initial fetch for all views
        for v in self.views:
            self._bg_update(v, force=True)

        while True:
            cur_view = self.views[self.active_tab]
            self._bg_update(cur_view)

            stdscr.erase()
            max_y, max_x = stdscr.getmaxyx()

            if max_y < 8 or max_x < 50:
                stdscr.addstr(0, 0, "Terminal too small!", curses.color_pair(4))
            else:
                self._draw_frame(stdscr, max_y, max_x)

            stdscr.refresh()

            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                break

            if ch == -1:
                continue

            if ch == curses.KEY_RESIZE:
                continue

            # Mouse Event Handling
            if ch == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    # 1. Click on Tab Bar (y=0)
                    if my == 0:
                        for x0, x1, t_idx in self.tab_spans:
                            if x0 <= mx < x1:
                                self.active_tab = t_idx
                                self._bg_update(self.views[self.active_tab], force=True)
                                break
                    # 2. Wheel Up / Down
                    elif bstate & WHEEL_UP:
                        cur_view.handle_key(curses.KEY_UP)
                    elif bstate & WHEEL_DN:
                        cur_view.handle_key(curses.KEY_DOWN)
                    # 3. Click inside view
                    elif bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                        cur_view.handle_mouse_click(mx, my)
                except curses.error:
                    pass
                continue

            # Global Help
            if self.show_help:
                if ch in (ord('?'), ord('q'), 27, 10, 13, ord(' ')):
                    self.show_help = False
                continue

            # First let the active view handle modal keys (e.g. switch confirmation)
            if cur_view.handle_key(ch):
                continue

            # Tab navigation keys
            # Tab (9), Shift-Tab / BackTab (curses.KEY_BTAB or 353), ']' next, '[' prev
            if ch in (9, ord(']')):  # Tab or ']'
                self.active_tab = (self.active_tab + 1) % len(self.views)
                self._bg_update(self.views[self.active_tab], force=True)
            elif ch in (curses.KEY_BTAB, 353, ord('[')):  # Shift+Tab or '['
                self.active_tab = (self.active_tab - 1 + len(self.views)) % len(self.views)
                self._bg_update(self.views[self.active_tab], force=True)

            # Direct jump: '1', '2', '3'
            elif ord('1') <= ch <= ord('9'):
                idx = ch - ord('1')
                if 0 <= idx < len(self.views):
                    self.active_tab = idx
                    self._bg_update(self.views[self.active_tab], force=True)

            elif ch in (ord('r'), ord('R')):
                self._bg_update(cur_view, force=True)

            elif ch == ord('?'):
                self.show_help = True

            elif ch in (ord('q'), 27):  # q or Esc
                break

    def _draw_frame(self, stdscr, max_y: int, max_x: int):
        cur_view = self.views[self.active_tab]
        self.tab_spans = []

        # 1. Tab bar. If the tabs do not fit, keep the active one in view.
        now_str = time.strftime("%H:%M:%S")
        right = f" {now_str} "
        labels = [f" {i + 1}:{v.name} " if i < 9 else f" {v.name} " for i, v in enumerate(self.views)]
        room = max_x - len(right) - 2
        first = 0
        while first < self.active_tab and sum(len(l) + 1 for l in labels[first:self.active_tab + 1]) + 2 > room:
            first += 1
        x = 0
        if first:
            stdscr.addstr(0, 0, "‹", curses.color_pair(8))
            x = 1
        for idx in range(first, len(self.views)):
            text = labels[idx]
            if x + len(text) > room:
                stdscr.addstr(0, x, "›", curses.color_pair(8))
                break
            active = idx == self.active_tab
            pair = curses.color_pair(7) | curses.A_BOLD if active else curses.color_pair(1)
            stdscr.addstr(0, x, ("●" + text[1:]) if active else text, pair)
            self.tab_spans.append((x, x + len(text), idx))
            x += len(text) + 1
        stdscr.addstr(0, max_x - len(right) - 1, right, curses.color_pair(5))

        stdscr.hline(1, 0, curses.ACS_HLINE, max_x)

        # 2. Render Active View Content
        cur_view.draw(stdscr, start_y=2, max_y=max_y - 1, max_x=max_x)

        # 3. Global Footer
        footer = " Tab/1-9/click:tabs · j/k/wheel:rows · r:refresh · ?:help · q:close "
        status = f" [{cur_view.get_status()}] "[: max(10, max_x // 2)]
        if max_y - 1 > 0:
            # status wins over the key hints: it carries DEMO / Stale / errors
            room = max(0, max_x - len(status) - 2)
            stdscr.addstr(max_y - 1, 0, footer[:room], curses.color_pair(1) | curses.A_DIM)
            pair = curses.color_pair(3) | curses.A_BOLD if "DEMO" in status or "Stale" in status else curses.color_pair(5)
            stdscr.addstr(max_y - 1, max_x - len(status) - 1, status, pair)

        if self.show_help:
            self._draw_help(stdscr, max_y, max_x)

    def _draw_help(self, stdscr, max_y: int, max_x: int):
        box_w = min(max_x - 4, 74)
        box_h = 14
        start_y = max(2, (max_y - box_h) // 2)
        start_x = max(2, (max_x - box_w) // 2)

        for y in range(start_y, start_y + box_h):
            stdscr.addstr(y, start_x, " " * box_w, curses.color_pair(6))

        stdscr.addstr(start_y, start_x + 2, " herdeck ", curses.color_pair(6) | curses.A_BOLD)
        lines = [
            "Tab / ]  Shift+Tab / [   next / previous tab",
            "1 - 9                    jump to tab",
            "click a tab / a row      switch tab / select row",
            "j k, arrows, wheel       move selection",
            "PgUp PgDn, g G           page, top, bottom",
            "r                        refresh this tab now",
            "q / Esc                  close",
            "",
            "Tab-specific keys (GPUs): s lane switch, Enter processes",
            "Tabs come from ~/.config/herdeck/tabs.toml",
        ]
        for idx, line in enumerate(lines):
            stdscr.addstr(start_y + 2 + idx, start_x + 2, line, curses.color_pair(6))
