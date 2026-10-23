#!/usr/bin/env python3
"""Record herdeck's demo deck in a pseudo-terminal and write an animated SVG.

    python3 scripts/demo-svg.py [path/to/herdeck] [out.svg]

Only sample data is shown (demo tabs plus a made-up exec tab), so the picture never
contains anything from the machine it was recorded on.
"""
import fcntl, html, json, os, pty, re, select, signal, struct, sys, tempfile, termios, time

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BIN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "target", "release", "herdeck")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "docs", "demo.svg")
COLS, ROWS = 104, 20

JOBS = {
    "header": "4 backups, 1 needs attention",
    "columns": ["TARGET", "LAST RUN", "SIZE (7 days)", "STATE"],
    "rows": [
        ["photos", "02:10", {"spark": [40, 41, 41, 43, 44, 44, 46]}, {"text": "ok", "tone": "ok"}],
        ["documents", "02:25", {"spark": [12, 12, 13, 13, 13, 14, 14]}, {"text": "ok", "tone": "ok"}],
        ["mail", "02:40", {"spark": [8, 8, 9, 9, 9, 9, 10]}, {"text": "running", "tone": "accent"}],
        {"section": "needs attention"},
        ["laptop", "3 days ago", {"spark": [20, 20, 20, 0, 0, 0, 0]}, {"text": "missed", "tone": "bad"}],
    ],
    "details": ["exec tab: any script that prints JSON"],
}
DECK = """
[[tab]]
name = "Services"
type = "http"

[[tab]]
name = "GPUs"
type = "gpu"

[[tab]]
name = "AI quotas"
type = "ai-quotas"

[[tab]]
name = "Backups"
type = "exec"
command = ["cat", "%s"]
interval = 60
"""

# (keys, seconds to wait, seconds this frame stays on screen in the SVG)
SCRIPT = [
    (b"", 1.5, 2.2),
    (b"j", 0.5, 1.2),
    (b"j", 0.5, 1.4),
    (b"2", 1.0, 2.0),
    (b"s", 0.6, 2.0),
    (b"\x1b", 0.6, 0.8),
    (b"3", 1.0, 2.6),
    (b"4", 1.0, 2.6),
    (b"?", 0.6, 2.4),
]

BASE = ["#1d1f21", "#cc6666", "#b5bd68", "#f0c674", "#81a2be", "#b294bb", "#8abeb7", "#c5c8c6",
        "#666666", "#d54e53", "#b9ca4a", "#e7c547", "#7aa6da", "#c397d8", "#70c0b1", "#eaeaea"]
FG, BG = "#c5c8c6", "#1d1f21"


def xterm(n):
    if n < 16:
        return BASE[n]
    if n < 232:
        n -= 16
        v = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (v[n // 36], v[n // 6 % 6], v[n % 6])
    g = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (g, g, g)


CSI = re.compile(r"\x1b\[([0-9;?<>:]*)([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>78DEM]")


class Screen:
    def __init__(self):
        self.cells = [[(" ", None, None, False) for _ in range(COLS)] for _ in range(ROWS)]
        self.x = self.y = 0
        self.fg = self.bg = None
        self.bold = self.rev = False

    def feed(self, data):
        pos = 0
        for m in CSI.finditer(data):
            self.text(data[pos:m.start()])
            pos = m.end()
            if m.group(2):
                self.csi(m.group(1), m.group(2))
        self.text(data[pos:])

    def text(self, s):
        for ch in s:
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self.y = min(ROWS - 1, self.y + 1)
            elif ch >= " ":
                if 0 <= self.y < ROWS and 0 <= self.x < COLS:
                    fg, bg = (self.bg or BG, self.fg or FG) if self.rev else (self.fg, self.bg)
                    self.cells[self.y][self.x] = (ch, fg, bg, self.bold)
                self.x += 1

    def csi(self, params, cmd):
        if params.startswith("?"):
            return
        nums = [int(p) if p.isdigit() else 0 for p in re.split("[;:]", params)] if params else []
        if cmd in "Hf":
            self.y = max(0, (nums[0] if nums and nums[0] else 1) - 1)
            self.x = max(0, (nums[1] if len(nums) > 1 and nums[1] else 1) - 1)
        elif cmd == "J" and nums[:1] in ([2], [3]):
            self.__init__()
        elif cmd == "K":
            for i in range(self.x, COLS):
                self.cells[self.y][i] = (" ", None, self.bg, False)
        elif cmd == "G":
            self.x = max(0, (nums[0] if nums else 1) - 1)
        elif cmd == "C":
            self.x += nums[0] if nums and nums[0] else 1
        elif cmd == "D":
            self.x = max(0, self.x - (nums[0] if nums and nums[0] else 1))
        elif cmd == "m":
            self.sgr(nums or [0])

    def sgr(self, n):
        i = 0
        while i < len(n):
            c = n[i]
            if c == 0:
                self.fg = self.bg = None
                self.bold = self.rev = False
            elif c == 1:
                self.bold = True
            elif c == 22:
                self.bold = False
            elif c == 7:
                self.rev = True
            elif c == 27:
                self.rev = False
            elif 30 <= c <= 37:
                self.fg = BASE[c - 30]
            elif 90 <= c <= 97:
                self.fg = BASE[c - 82]
            elif 40 <= c <= 47:
                self.bg = BASE[c - 40]
            elif 100 <= c <= 107:
                self.bg = BASE[c - 92]
            elif c == 39:
                self.fg = None
            elif c == 49:
                self.bg = None
            elif c in (38, 48) and i + 1 < len(n):
                if n[i + 1] == 5 and i + 2 < len(n):
                    col = xterm(n[i + 2]); i += 2
                elif n[i + 1] == 2 and i + 4 < len(n):
                    col = "#%02x%02x%02x" % tuple(n[i + 2:i + 5]); i += 4
                else:
                    col = None
                if c == 38:
                    self.fg = col
                else:
                    self.bg = col
            i += 1

    def snapshot(self):
        return [row[:] for row in self.cells]


def record():
    tmp = tempfile.mkdtemp(prefix="herdeck-demo-")
    jobs = os.path.join(tmp, "jobs.json")
    with open(jobs, "w") as f:
        json.dump(JOBS, f)
    os.makedirs(os.path.join(tmp, "herdeck"))
    with open(os.path.join(tmp, "herdeck", "tabs.toml"), "w") as f:
        f.write(DECK % jobs)
    env = {"PATH": "/usr/bin:/bin", "TERM": "xterm-256color", "COLORTERM": "truecolor",
           "XDG_CONFIG_HOME": tmp, "HOME": tmp, "LANG": "C.UTF-8"}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(BIN, [BIN], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = Screen()

    def drain(sec):
        end = time.time() + sec
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    screen.feed(os.read(fd, 65536).decode(errors="replace"))
                except OSError:
                    return

    frames = []
    for keys, wait, hold in SCRIPT:
        if keys:
            os.write(fd, keys)
        drain(wait)
        frames.append((screen.snapshot(), hold))
    os.write(fd, b"q")
    drain(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return frames


CW, CH, PAD, BAR = 8.4, 17, 14, 30


def frame_svg(cells):
    out = []
    for y, row in enumerate(cells):
        top = BAR + PAD + y * CH
        x = 0
        while x < COLS:  # background runs
            bg = row[x][2]
            if bg and bg != BG:
                s = x
                while x < COLS and row[x][2] == bg:
                    x += 1
                out.append(f'<rect x="{PAD + s * CW:.1f}" y="{top}" width="{(x - s) * CW + .5:.1f}" height="{CH}" fill="{bg}"/>')
            else:
                x += 1
        x = 0
        while x < COLS:  # text runs with the same style
            ch, fg, _, bold = row[x]
            if ch == " ":
                x += 1
                continue
            s, txt = x, []
            while x < COLS and len(txt) < 12 and row[x][1] == fg and row[x][3] == bold and (row[x][0] != " " or (x + 1 < COLS and row[x + 1][0] != " ")):
                txt.append(row[x][0]); x += 1
            w = ' font-weight="bold"' if bold else ""
            out.append(f'<text x="{PAD + s * CW:.1f}" y="{top + CH - 4}" fill="{fg or FG}"{w} textLength="{len(txt) * CW:.1f}" lengthAdjust="spacingAndGlyphs">{html.escape("".join(txt))}</text>')
    return "".join(out)


def write_svg(frames):
    W, H = int(COLS * CW + 2 * PAD), int(ROWS * CH + 2 * PAD + BAR)
    total = sum(h for _, h in frames)
    css, groups, t = [], [], 0.0
    for i, (cells, hold) in enumerate(frames):
        a, b = t / total * 100, (t + hold) / total * 100
        css.append(f"@keyframes f{i}{{0%,{a:.3f}%{{opacity:0}}{a + .001:.3f}%,{b:.3f}%{{opacity:1}}{b + .001:.3f}%,100%{{opacity:0}}}}"
                   f".f{i}{{animation:f{i} {total:.1f}s step-end infinite;opacity:0}}")
        groups.append(f'<g class="f{i}">{frame_svg(cells)}</g>')
        t += hold
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="herdeck demo: tabs for services, GPUs, AI quotas and a custom backups list">
<style>text{{font-family:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;font-size:14px;white-space:pre}}{"".join(css)}</style>
<rect width="{W}" height="{H}" rx="10" fill="{BG}"/>
<circle cx="20" cy="15" r="6" fill="#ff5f57"/><circle cx="40" cy="15" r="6" fill="#febc2e"/><circle cx="60" cy="15" r="6" fill="#28c840"/>
<text x="{W / 2}" y="20" fill="#8a8f98" text-anchor="middle" style="font-size:12px">herdeck</text>
{"".join(groups)}
</svg>
'''
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(svg)
    print(f"wrote {OUT}: {len(frames)} frames, {total:.0f}s loop, {len(svg) // 1024} KB")


if __name__ == "__main__":
    write_svg(record())
