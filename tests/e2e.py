#!/usr/bin/env python3
"""End-to-end: drive the real herdeck binary in a pseudo-terminal (demo deck,
no network). Usage: python3 tests/e2e.py [path/to/herdeck]"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BIN = os.environ.get("HERDECK_BIN") or os.path.join(ROOT, "target", "release", "herdeck")
CSI = re.compile(r"\x1b\[([0-9;?<>]*)([@-~])|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>78]")


class Screen:
    """Just enough of a terminal to replay ratatui's output: cursor moves, clears, text.
    ratatui only redraws changed cells, so the raw byte stream can't be read as text."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[" "] * cols for _ in range(rows)]
        self.x = self.y = 0

    def feed(self, data):
        pos = 0
        for m in CSI.finditer(data):
            self._text(data[pos:m.start()])
            pos = m.end()
            if m.group(2):
                self._csi(m.group(1), m.group(2))
        self._text(data[pos:])

    def _text(self, s):
        for ch in s:
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self.y = min(self.rows - 1, self.y + 1)
            elif ch >= " ":
                if 0 <= self.y < self.rows and 0 <= self.x < self.cols:
                    self.grid[self.y][self.x] = ch
                self.x += 1

    def _csi(self, params, cmd):
        nums = [int(p) if p.isdigit() else 0 for p in params.lstrip("?").split(";")] if params else []
        if cmd in "Hf":
            self.y = max(0, (nums[0] if nums and nums[0] else 1) - 1)
            self.x = max(0, (nums[1] if len(nums) > 1 and nums[1] else 1) - 1)
        elif cmd == "J" and nums[:1] in ([2], [3]):
            self.grid = [[" "] * self.cols for _ in range(self.rows)]
        elif cmd == "K":
            row = self.grid[min(self.y, self.rows - 1)]
            for i in range(self.x, self.cols):
                row[i] = " "
        elif cmd == "G":
            self.x = max(0, (nums[0] if nums else 1) - 1)

    def text(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)


def run(keys, cols=110, rows=26, settle=1.2, args=("--demo",), config_home="/nonexistent-herdeck"):
    """Start herdeck (default: the --demo deck), send keys; returns (final screen,
    screen after each key, exited)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
    env.update(TERM="xterm-256color", XDG_CONFIG_HOME=config_home, HOME=config_home)
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(BIN, [BIN, *args], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    screen = Screen(cols, rows)
    raw = bytearray()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                raw.extend(chunk)
                screen.feed(chunk.decode(errors="replace"))

    drain(settle)
    screens = []
    for key, wait in keys:
        os.write(fd, key)
        drain(wait)
        screens.append(screen.text())
    exited = False
    for _ in range(40):
        if os.waitpid(pid, os.WNOHANG)[0]:
            exited = True
            break
        time.sleep(0.05)
    if not exited:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    full = raw.decode(errors="replace")
    return full, screens, exited


def click(x, y):  # SGR mouse report, 1-based coordinates
    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()


class E2E(unittest.TestCase):
    def test_keys_switch_tabs_and_quit(self):
        full, s, exited = run([(b"", 0.1), (b"2", 0.8), (b"3", 0.8), (b"\t", 0.8), (b"q", 0.5)])
        self.assertIn("●1:Processes", s.pop(0))
        self.assertIn("●2:Disk", s[0])
        self.assertIn("●3:Services", s[1])
        self.assertIn("●4:GPUs", s[2])
        self.assertIn("DEMO", s[1])  # Services is sample data until configured
        self.assertNotIn("panicked", full)
        self.assertTrue(exited, "q did not quit")

    def test_mouse_clicks_tabs(self):
        # tab bar: " 1:Processes " (cols 1-13) " 2:Disk " (15-22) " 3:Services " (24-35)
        _, s, exited = run([(click(28, 1), 0.8), (click(17, 1), 0.8), (b"\x1b", 0.5)])
        self.assertIn("●3:Services", s[0])
        self.assertIn("●2:Disk", s[1])
        self.assertTrue(exited, "Esc did not quit")

    def test_lane_prompt_is_modal_and_confirms_in_demo(self):
        _, s, exited = run([(b"4", 0.8), (b"s", 0.6), (b"2", 0.4), (b"y", 1.2), (b"q", 0.5)])
        self.assertIn("Switch lane", s[1])
        self.assertIn("Switch lane", s[2])  # digits go to the prompt while it is open
        self.assertNotIn("●2:Disk", s[2])
        self.assertIn("LLM ●", s[3])  # the demo card moved to the next lane
        self.assertTrue(exited)

    def test_help_overlay(self):
        _, s, exited = run([(b"?", 0.6), (b"x", 0.5), (b"q", 0.5)])
        self.assertIn("next / previous tab", s[0])
        self.assertNotIn("next / previous tab", s[1])  # any key closes help
        self.assertTrue(exited)

    def test_small_terminals_do_not_crash(self):
        for cols, rows in [(80, 24), (60, 14), (40, 8), (20, 4), (9, 2)]:
            full, _, exited = run([(b"\t", 0.3), (b"\t", 0.3), (b"?", 0.3), (b"?", 0.3), (b"q", 0.5)], cols, rows, 0.6)
            self.assertNotIn("panicked", full, f"{cols}x{rows}")
            self.assertTrue(exited, f"{cols}x{rows} did not quit")

    def test_once_output(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
        env.update(XDG_CONFIG_HOME="/nonexistent-herdeck", HOME="/nonexistent-herdeck")
        out = subprocess.run([BIN, "--once", "--demo"], capture_output=True, text=True, env=env, timeout=60).stdout
        for title in ("## Processes", "## Disk", "## Services  [DEMO]", "## GPUs  [DEMO]", "## AI quotas  [DEMO]"):
            self.assertIn(title, out)  # --once output is plain text, spaces intact

    def test_first_run_picker_writes_deck_without_sample_data(self):
        import shutil, subprocess, tempfile
        home = tempfile.mkdtemp(prefix="herdeck-e2e-")
        try:
            # no deck and no --demo: the template picker, never sample data
            full, s, exited = run([(b"", 0.1), (b"j", 0.3), (b"\r", 1.5), (b"q", 0.5)], args=(), config_home=home)
            self.assertIn("setupyourdeck", s[0].replace(" ", ""))
            self.assertNotIn("DEMO", full)
            deck = os.path.join(home, "herdeck", "tabs.toml")
            self.assertTrue(os.path.exists(deck), "enter should write the deck")
            with open(deck) as f:
                self.assertIn('type = "processes"', f.read())
            self.assertIn("●1:Processes", s[2].replace(" ", ""))  # and open it
            self.assertTrue(exited)
            # a tab that needs settings explains itself instead of faking data
            env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
            env.update(XDG_CONFIG_HOME=home, HOME=home)
            with open(deck, "a") as f:
                f.write('\n[[tab]]\nname = "Health"\ntype = "http"\n\n[[tab]]\nname = "GPUs"\ntype = "gpu"\n')
            out = subprocess.run([BIN, "--once"], capture_output=True, text=True, env=env, timeout=60).stdout
            self.assertIn("Not set up yet", out)
            self.assertIn("[[tab.check]]", out)
            self.assertNotIn("DEMO", out)
            self.assertNotIn("RTX", out)
        finally:
            shutil.rmtree(home)

    def test_no_deck_without_terminal_explains(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
        env.update(XDG_CONFIG_HOME="/nonexistent-herdeck", HOME="/nonexistent-herdeck")
        r = subprocess.run([BIN, "--once"], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertIn("herdeck init", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_piping_into_head_does_not_panic(self):
        import subprocess, shlex
        for args in ("init --list", "--types", "--once --demo"):
            r = subprocess.run(f"{shlex.quote(BIN)} {args} | head -1", shell=True, capture_output=True, text=True,
                               env=dict(os.environ, HOME="/nonexistent-herdeck", XDG_CONFIG_HOME="/nonexistent-herdeck"), timeout=60)
            self.assertNotIn("panicked", r.stderr, args)
            self.assertTrue(r.stdout.strip(), args)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        BIN = os.path.abspath(sys.argv.pop(1))
    if not os.access(BIN, os.X_OK):
        sys.exit(f"no herdeck binary at {BIN}; run cargo build --release first")
    unittest.main(verbosity=2)
