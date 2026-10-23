"""Drive the real curses UI in a pseudo-terminal (demo mode, no network)."""
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
ANSI = re.compile(r"\x1b\[[0-9;?<>]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]")


def run(keys, cols=110, rows=26):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDECK")}
    env.update(TERM="xterm-256color", ESCDELAY="25", XDG_CONFIG_HOME="/nonexistent-herdeck")
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execvpe(sys.executable, [sys.executable, "bin/herdeck"], env)
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

    drain(1.5)
    screens = []
    for key, wait in keys:
        mark = len(out)
        os.write(fd, key)
        drain(wait)
        screens.append(ANSI.sub("", out[mark:].decode(errors="replace")))
    exited = os.waitpid(pid, os.WNOHANG)[0] != 0
    if not exited:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    return ANSI.sub("", out.decode(errors="replace")), screens, exited


def click(x, y):  # SGR mouse, 1-based
    return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()


class TuiE2E(unittest.TestCase):
    def test_keys_switch_tabs_and_quit(self):
        full, s, exited = run([(b"2", 0.8), (b"3", 0.8), (b"\t", 0.8), (b"q", 1.0)])
        self.assertIn("●2:Disk", s[0])
        self.assertIn("●3:Services", s[1])
        self.assertIn("●4:GPUs", s[2])
        self.assertIn("DEMO", full)
        self.assertIn("●1:Processes", full)
        self.assertNotIn("Traceback", full)
        self.assertTrue(exited, "q did not quit")

    def test_mouse_clicks_tabs(self):
        # tab bar: " 1:Processes " (0-12) " 2:Disk " (14-21) " 3:Services " (23-34) ...
        _, s, exited = run([(click(28, 1), 0.8), (click(17, 1), 0.8), (b"\x1b", 1.0)])
        self.assertIn("●3:Services", s[0])
        self.assertIn("●2:Disk", s[1])
        self.assertTrue(exited, "Esc did not quit")

    def test_lane_prompt_is_modal_and_cancellable(self):
        _, s, exited = run([(b"4", 0.8), (b"s", 0.6), (b"2", 0.4), (b"q", 0.6), (b"q", 1.0)])
        s = s[1:]
        self.assertIn("CONFIRM LANE SWITCH", s[0])
        self.assertNotIn("●2:Disk", s[1])  # digits are swallowed while the prompt is open
        self.assertIn("cancelled", s[2])
        self.assertTrue(exited)

    def test_small_terminals_do_not_crash(self):
        for cols, rows in [(80, 24), (60, 14), (40, 8), (25, 5)]:
            full, _, exited = run([(b"\t", 0.4), (b"\t", 0.4), (b"?", 0.3), (b"?", 0.3), (b"q", 0.8)], cols, rows)
            self.assertNotIn("Traceback", full, f"{cols}x{rows}")
            self.assertTrue(exited, f"{cols}x{rows} did not quit")


if __name__ == "__main__":
    unittest.main()
