import curses
import threading
import time
from typing import List, Tuple

from herdeck.common import SafeWin, init_colors
from herdeck.views.base import BaseView
from herdeck.views.gpu import GPUView
from herdeck.views.cpu import CPUView
from herdeck.views.cliproxy import CLIProxyView

WHEEL_UP = getattr(curses, "BUTTON4_PRESSED", 0x10000)
WHEEL_DN = getattr(curses, "BUTTON5_PRESSED", 0x200000)


class DashboardApp:
    def __init__(self, views: List[BaseView] = None, refresh_interval: float = 2.0):
        self.views = views or [GPUView(), CPUView(), CLIProxyView()]
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
        stdscr.timeout(500)

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

        # 1. Top Tab Bar with mouse hitboxes
        x = 1
        stdscr.addstr(0, 0, " ", curses.color_pair(1))
        for idx, v in enumerate(self.views):
            is_active = (idx == self.active_tab)
            bullet = "●" if is_active else " "
            tab_text = f" [{bullet} {idx + 1}: {v.name}] "
            x_end = x + len(tab_text)
            if x_end < max_x - 22:
                pair = curses.color_pair(7) | curses.A_BOLD if is_active else curses.color_pair(1) | curses.A_DIM
                stdscr.addstr(0, x, tab_text, pair)
                self.tab_spans.append((x, x_end, idx))
                x = x_end + 1

        # Clock & refresh on right
        now_str = time.strftime("%H:%M:%S")
        status_text = f" ↻{int(self.interval)}s {now_str} "
        right_x = max(x + 2, max_x - len(status_text) - 1)
        if right_x < max_x:
            stdscr.addstr(0, right_x, status_text[:max_x - right_x], curses.color_pair(5))

        stdscr.hline(1, 0, curses.ACS_HLINE, max_x)

        # 2. Render Active View Content
        cur_view.draw(stdscr, start_y=2, max_y=max_y - 1, max_x=max_x)

        # 3. Global Footer
        footer = f" Tab/1-{len(self.views)}:switch · click:select · j/k/wheel:scroll · r:refresh · q/Esc:close "
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

        stdscr.addstr(start_y, start_x + 2, " HERDECK CONTROLS ", curses.color_pair(6) | curses.A_BOLD)
        lines = [
            "Mouse Click    : Click tab at top to switch, click row to select",
            "Mouse Wheel    : Scroll rows up / down",
            "Tab / ]        : Switch to NEXT dashboard tab",
            "Shift+Tab / [  : Switch to PREVIOUS dashboard tab",
            "1 - 9          : Jump directly to a tab",
            "j / Down       : Select next item / row",
            "k / Up         : Select previous item / row",
            "Enter          : Toggle details / processes",
            "s              : Switch lane of selected GPU (s again = next lane)",
            "r              : Force refresh current dashboard",
            "q / Esc        : Close dashboard window",
            "?              : Close this help",
        ]
        for idx, line in enumerate(lines):
            stdscr.addstr(start_y + 2 + idx, start_x + 2, line, curses.color_pair(6))
