import argparse
import sys

from herdeck.app import DashboardApp
from herdeck.views.gpu import GPUView
from herdeck.views.cpu import CPUView
from herdeck.views.cliproxy import CLIProxyView


def main():
    parser = argparse.ArgumentParser(description="herdeck: tabbed terminal dashboards for herdr")
    parser.add_argument("--once", action="store_true", help="Print snapshot table and exit")
    parser.add_argument("--tab", default="1", help="Tab to open by default (1: GPUs, 2: CPU, 3: Proxy, or name)")
    parser.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds (default: 2.0)")

    args = parser.parse_args()

    gpu_view = GPUView(refresh_interval=args.interval)
    cpu_view = CPUView(refresh_interval=max(args.interval, 3.0))
    proxy_view = CLIProxyView(refresh_interval=30.0)
    views = [gpu_view, cpu_view, proxy_view]

    if args.once or not sys.stdout.isatty():
        print("================================================================================")
        gpu_view.print_once()
        print()
        print("================================================================================")
        cpu_view.print_once()
        print()
        print("================================================================================")
        proxy_view.print_once()
        print("================================================================================")
        return

    # Determine default tab
    default_tab = 0
    if args.tab in ("3", "proxy", "cliproxy", "quota"):
        default_tab = 2
    elif args.tab in ("2", "cpu", "fleet"):
        default_tab = 1
    elif args.tab in ("1", "gpu", "gpus"):
        default_tab = 0

    app = DashboardApp(views=views, refresh_interval=args.interval)
    app.active_tab = default_tab
    app.run()


if __name__ == "__main__":
    main()
