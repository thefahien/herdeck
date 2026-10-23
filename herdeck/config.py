"""Load tabs.toml and turn each ``[[tab]]`` into a view.

Lookup order: ``--config PATH``, ``$HERDECK_CONFIG``,
``${XDG_CONFIG_HOME:-~/.config}/herdeck/tabs.toml``, then the built-in demo deck.
String values may reference environment variables as ``${NAME}`` or
``${NAME:-default}``; keep secrets in the environment (or the env file the pane
script sources), not in tabs.toml.
"""
import importlib.util
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

from herdeck.views.base import BaseView
from herdeck.views.builtin import TYPES as BUILTIN

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(v: Any) -> Any:
    """Substitute ${NAME} / ${NAME:-default}. ``command`` values are left alone:
    the shell expands variables there itself, and pasting values into a shell
    string would let a variable's content run as code."""
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), v)
    if isinstance(v, list):
        return [expand(x) for x in v]
    if isinstance(v, dict):
        return {k: (x if k == "command" else expand(x)) for k, x in v.items()}
    return v


def config_path(explicit: Optional[str] = None) -> str:
    if explicit:
        return os.path.expanduser(explicit)
    if os.environ.get("HERDECK_CONFIG"):
        return os.path.expanduser(os.environ["HERDECK_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "herdeck", "tabs.toml")


# The deck you get with no config: real local data where that needs no setup,
# demo data (marked DEMO) for tabs that need an endpoint.
DEFAULT_TABS: List[Dict[str, Any]] = [
    {"name": "Processes", "type": "processes", "sort": "cpu", "limit": 40},
    {"name": "Disk", "type": "command", "command": "df -hP | grep -vE '^(tmpfs|devtmpfs|overlay|squashfs|udev|shm)'", "format": "table",
     "headers": ["Filesystem", "Size", "Used", "Avail", "Use%", "Mounted on"], "interval": 30,
     "column": [{"title": "Use%", "bar": 100, "width": 8}]},
    {"name": "Services", "type": "http"},
    {"name": "GPUs", "type": "gpu"},
    {"name": "AI quotas", "type": "ai-quotas"},
]


def _plugin_types() -> Dict[str, Any]:
    from herdeck.views.cliproxy import CLIProxyView
    from herdeck.views.gpu import GPUView
    return {"gpu": GPUView, "ai-quotas": CLIProxyView}


def load_python_view(path: str, cfg: Dict[str, Any]) -> BaseView:
    """``type = "python"``: a file defining ``class View(BaseView)``."""
    path = os.path.expanduser(path)
    spec = importlib.util.spec_from_file_location(f"herdeck_user_{abs(hash(path))}", path)
    if not spec or not spec.loader:
        raise ValueError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, cfg.get("class", "View"), None)
    if cls is None:
        raise ValueError(f"{path} has no class {cfg.get('class', 'View')}")
    return cls(cfg)


class ErrorView(BaseView):
    """Shown in place of a tab whose config is broken, so the rest still works."""

    def __init__(self, cfg, message):
        super().__init__(cfg)
        self.message = message

    def update(self, force=False):
        pass

    def draw(self, stdscr, start_y, max_y, max_x):
        import curses
        stdscr.addstr(start_y + 1, 2, "This tab could not be loaded:", curses.color_pair(4) | curses.A_BOLD)
        for i, line in enumerate(self.message.splitlines()[: max(1, max_y - start_y - 3)]):
            stdscr.addstr(start_y + 2 + i, 4, line, curses.color_pair(3))
        return max_y

    def get_status(self):
        return "config error"

    def snapshot(self):
        return [f"  config error: {self.message}"]


def build_views(tabs: List[Dict[str, Any]]) -> List[BaseView]:
    types = dict(BUILTIN, **_plugin_types())
    views: List[BaseView] = []
    for raw in tabs:
        cfg = expand(raw)
        kind = cfg.get("type", "")
        cfg.setdefault("name", kind or "tab")
        try:
            if kind == "python":
                views.append(load_python_view(cfg["path"], cfg))
            elif kind in types:
                views.append(types[kind](cfg))
            else:
                raise ValueError(f"unknown type {kind!r}; known: {', '.join(sorted(types))}, python")
        except Exception as e:
            views.append(ErrorView(cfg, f"{type(e).__name__}: {e}"))
    return views


def load(explicit: Optional[str] = None) -> Tuple[List[BaseView], Dict[str, Any], str]:
    path = config_path(explicit)
    if not os.path.exists(path):
        if explicit:
            sys.exit(f"herdeck: config not found: {path}")
        return build_views(DEFAULT_TABS), {}, "built-in demo deck"
    if tomllib is None:
        sys.exit("herdeck: reading tabs.toml needs Python 3.11+")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    tabs = data.get("tab") or []
    if not tabs:
        sys.exit(f"herdeck: {path} has no [[tab]] entries")
    return build_views(tabs), data, path


def known_types() -> List[str]:
    return sorted(list(BUILTIN) + list(_plugin_types()) + ["python"])
