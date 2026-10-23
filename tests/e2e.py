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
ANSI = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z@`]|\x1b[()][A-Z0-9]|\x1b[=>78]|\x1b\][^\x07]*\x07")


def run(keys, cols=110, rows=26, settle=1.2):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
    env.update(TERM="xterm-256color", XDG_CONFIG_HOME="/nonexistent-herdeck", HOME="/nonexistent-herdeck")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(BIN, [BIN], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = bytearray()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    out.extend(os.read(fd, 65536))
                except OSError:
                    return

    drain(settle)
    screens = []
    for key, wait in keys:
        mark = len(out)
        os.write(fd, key)
        drain(wait)
        # ratatui moves the cursor instead of printing spaces, so compare without them
        screens.append(re.sub(r"\s+", "", ANSI.sub("", out[mark:].decode(errors="replace"))))
    exited = False
    for _ in range(40):
        if os.waitpid(pid, os.WNOHANG)[0]:
            exited = True
            break
        time.sleep(0.05)
    if not exited:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    return re.sub(r"\s+", "", ANSI.sub("", out.decode(errors="replace"))), screens, exited


def click(x, y):  # SGR mouse report, 1-based coordinates
    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()


class E2E(unittest.TestCase):
    def test_keys_switch_tabs_and_quit(self):
        full, s, exited = run([(b"2", 0.8), (b"3", 0.8), (b"\t", 0.8), (b"q", 0.5)])
        self.assertIn("●1:Processes", full)
        self.assertIn("●2:Disk", s[0])
        self.assertIn("●3:Services", s[1])
        self.assertIn("●4:GPUs", s[2])
        self.assertIn("DEMO", full)
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
        self.assertIn("Switchlane", s[1])
        self.assertNotIn("●2:Disk", s[2])  # digits go to the prompt while it is open
        self.assertIn("LLM●", s[3])  # the demo card moved to the next lane
        self.assertTrue(exited)

    def test_help_overlay(self):
        _, s, exited = run([(b"?", 0.6), (b"x", 0.5), (b"q", 0.5)])
        self.assertIn("next/previoustab", s[0])
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
        out = subprocess.run([BIN, "--once"], capture_output=True, text=True, env=env, timeout=60).stdout
        for title in ("## Processes", "## Disk", "## Services  [DEMO]", "## GPUs  [DEMO]", "## AI quotas  [DEMO]"):
            self.assertIn(title, out)  # --once output is plain text, spaces intact


if __name__ == "__main__":
    if len(sys.argv) > 1:
        BIN = os.path.abspath(sys.argv.pop(1))
    if not os.access(BIN, os.X_OK):
        sys.exit(f"no herdeck binary at {BIN}; run cargo build --release first")
    unittest.main(verbosity=2)
