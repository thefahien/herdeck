import argparse
import sys

from herdeck import __version__
from herdeck.app import DashboardApp
from herdeck.config import known_types, load


def main():
    p = argparse.ArgumentParser(prog="herdeck", description="herdeck: tabbed terminal dashboards for herdr")
    p.add_argument("--config", help="tabs.toml to use (default: ~/.config/herdeck/tabs.toml)")
    p.add_argument("--once", action="store_true", help="print every tab once and exit")
    p.add_argument("--tab", default="1", help="tab to start on: number or name")
    p.add_argument("--types", action="store_true", help="list tab types and exit")
    p.add_argument("--version", action="version", version=f"herdeck {__version__}")
    args = p.parse_args()

    if args.types:
        print("\n".join(known_types()))
        return

    views, data, source = load(args.config)

    if args.once or not sys.stdout.isatty():
        print(f"# herdeck: {source}")
        for v in views:
            print()
            v.print_once()
        return

    start = 0
    if args.tab.isdigit():
        start = max(0, min(len(views) - 1, int(args.tab) - 1))
    else:
        start = next((i for i, v in enumerate(views) if v.name.lower() == args.tab.lower()), 0)
    app = DashboardApp(views=views, refresh_interval=float(data.get("refresh", 1.0)))
    app.active_tab = start
    app.run()


if __name__ == "__main__":
    main()
