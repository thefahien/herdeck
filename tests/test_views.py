import os
import sys
import unittest

for k in list(os.environ):
    if k.startswith("HERDECK_"):
        del os.environ[k]
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from herdeck.common import make_pbar, make_sparkline
from herdeck.views.gpu import GPUView
from herdeck.views.cpu import CPUView
from herdeck.views.cliproxy import CLIProxyView


class TestViews(unittest.TestCase):
    def test_gpu_view_init(self):
        v = GPUView()
        self.assertEqual(v.name, "GPUs")
        self.assertEqual(v.key_hint, "1")

    def test_cpu_view_init(self):
        v = CPUView()
        self.assertEqual(v.name, "Fleet CPU")
        self.assertEqual(v.key_hint, "2")

    def test_proxy_view_init(self):
        v = CLIProxyView()
        self.assertEqual(v.name, "CLI Proxy")
        self.assertEqual(v.key_hint, "3")

    def test_demo_fallback(self):
        # Point to closed local ports
        gv = GPUView(base_url="http://127.0.0.1:59999")
        gv.update(force=True)
        self.assertTrue(gv.is_demo)
        self.assertIn("DEMO", gv.get_status())

        cv = CPUView(prom_url="http://127.0.0.1:59999")
        cv.update(force=True)
        self.assertTrue(cv.is_demo)
        self.assertIn("DEMO", cv.get_status())

        pv = CLIProxyView()
        pv._fetch_worker()
        self.assertTrue(pv.is_demo)
        self.assertIn("DEMO", pv.get_status())

    def test_pbar(self):
        self.assertEqual(make_pbar(50, 100, 6), "███░░░")

    def test_sparkline(self):
        self.assertEqual(make_sparkline([0, 50, 100], 3), " ▄█")


if __name__ == "__main__":
    unittest.main()
