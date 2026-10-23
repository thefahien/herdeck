"""Each built-in tab type against local fixtures (no network beyond 127.0.0.1)."""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for k in list(os.environ):
    if k.startswith("HERDECK"):
        del os.environ[k]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from herdeck.config import DEFAULT_TABS, build_views, expand, load  # noqa: E402
from herdeck.views.base import Bar, Spark, Text, cell_text  # noqa: E402
from herdeck.views.builtin import CommandView, HttpCheckView, JsonView, ProcessView, PrometheusView, dig, map_json  # noqa: E402

PROM = {
    "/api/v1/query": {"status": "success", "data": {"result": [
        {"metric": {"instance": "a"}, "value": [0, "12.5"]},
        {"metric": {"instance": "b"}, "value": [0, "97"]}]}},
    "/api/v1/query_range": {"status": "success", "data": {"result": [
        {"metric": {"instance": "a"}, "values": [[0, "1"], [1, "50"], [2, "100"]]}]}},
}
JOBS = {"jobs": [{"name": "encode", "progress": 0.5, "state": "running", "attempts": 0},
                 {"name": "upload", "progress": 1.0, "state": "failed", "attempts": 4}]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PROM:
            body, code = PROM[path], 200
        elif path == "/jobs":
            body, code = JOBS, 200
        elif path == "/health":
            body, code = {"ok": True}, 200
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/")
            self.end_headers()
            return
        else:
            body, code = {"error": "nope"}, 503
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()


class Helpers(unittest.TestCase):
    def test_dig(self):
        self.assertEqual(dig({"a": {"b": [1, {"c": 3}]}}, "a.b.1.c"), 3)
        self.assertIsNone(dig({"a": 1}, "a.b"))

    def test_expand(self):
        os.environ["HD_TEST_X"] = "v"
        self.assertEqual(expand({"u": "${HD_TEST_X}/${HD_MISSING:-d}"}), {"u": "v/d"})

    def test_map_json_auto_columns(self):
        cols, rows = map_json({"x": {"a": 1, "b": 2}}, "x", [])
        self.assertEqual(cols, ["key", "value"])
        self.assertEqual(len(rows), 2)


class Types(Fixture):
    def test_command_lines_highlight(self):
        v = CommandView({"command": "printf 'ok line\\nERROR here\\n'", "highlight": {"error": "bad"}})
        v.update(force=True)
        self.assertEqual(v.status, "OK")
        self.assertIsInstance(v.rows[1][0], Text)
        self.assertEqual(v.rows[1][0].tone, "bad")

    def test_command_table_with_bar(self):
        v = CommandView({"command": "printf 'name use\\nroot 41%%\\n'", "format": "table",
                         "column": [{"title": "use", "bar": 100}]})
        v.update(force=True)
        self.assertEqual(v.columns, ["name", "use"])
        self.assertIsInstance(v.rows[0][1], Bar)
        self.assertEqual(v.rows[0][1].value, 41)

    def test_command_jsonl(self):
        v = CommandView({"command": "printf '{\"n\":\"a\",\"s\":\"up\"}\\n{\"n\":\"b\",\"s\":\"down\"}\\n'",
                         "format": "jsonl", "columns": [{"title": "N", "path": "n"},
                                                        {"title": "S", "path": "s", "bad_values": ["down"]}]})
        v.update(force=True)
        self.assertEqual([cell_text(r[0]) for r in v.rows], ["a", "b"])
        self.assertEqual(v.rows[1][1].tone, "bad")

    def test_command_env_is_left_to_the_shell(self):
        os.environ["HD_TEST_INJECT"] = "x; echo INJECTED"
        v = build_views([{"type": "command", "command": "printf '%s\\n' \"$HD_TEST_INJECT\""}])[0]
        v.update(force=True)
        self.assertEqual([cell_text(r[0]) for r in v.rows], ["x; echo INJECTED"])

    def test_command_json_uses_tab_column_tables(self):
        v = CommandView({"command": "echo '[{\"s\": \"down\"}]'", "format": "json",
                         "column": [{"title": "S", "path": "s", "bad_values": ["down"]}]})
        v.update(force=True)
        self.assertEqual(v.columns, ["S"])
        self.assertEqual(v.rows[0][0].tone, "bad")

    def test_command_failure_keeps_last_rows(self):
        v = CommandView({"command": "echo hi"})
        v.update(force=True)
        v.cfg["command"] = "echo boom >&2; exit 3"
        v.cfg["format"] = "table"
        v.update(force=True)
        self.assertTrue(v.status.startswith("Stale"), v.status)
        self.assertEqual(cell_text(v.rows[0][0]), "hi")

    def test_processes(self):
        v = ProcessView({"limit": 5})
        v.update(force=True)
        self.assertEqual(v.status, "OK")
        self.assertTrue(0 < len(v.rows) <= 5)

    def test_http_checks(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            v = HttpCheckView({"check": [
                {"name": "up", "url": self.base + "/health", "contains": "ok"},
                {"name": "down", "url": self.base + "/missing"},
                {"name": "redirect", "url": self.base + "/redirect"},
                {"name": "tcp", "tcp": f"127.0.0.1:{port}"}]})
            v.update(force=True)
        finally:
            sock.close()
        state = {cell_text(r[0]): r[1].text for r in v.rows}
        self.assertEqual(state, {"up": "UP", "down": "DOWN", "redirect": "DOWN", "tcp": "UP"})

    def test_json_api(self):
        v = JsonView({"url": self.base + "/jobs", "items": "jobs", "column": [
            {"title": "Job", "path": "name"}, {"title": "P", "path": "progress", "scale": 100, "bar": 100},
            {"title": "Retries", "path": "attempts", "bad_above": 3}]})
        v.update(force=True)
        self.assertEqual(v.status, "OK")
        self.assertEqual(v.rows[0][1].value, 50)
        self.assertEqual(v.rows[1][2].tone, "bad")

    def test_prometheus(self):
        v = PrometheusView({"url": self.base, "metric": [
            {"title": "CPU", "query": "q", "bar": 100}, {"title": "Trend", "query": "q", "spark": True}]})
        v.update(force=True)
        self.assertEqual(v.columns, ["INSTANCE", "CPU", "Trend"])
        self.assertEqual([cell_text(r[0]) for r in v.rows], ["a", "b"])
        self.assertIsInstance(v.rows[0][2], Spark)

    def test_unreachable_is_an_error_not_demo(self):
        v = JsonView({"url": "http://127.0.0.1:9/x"})
        v.update(force=True)
        self.assertTrue(v.status.startswith("error"), v.status)
        self.assertFalse(v.is_demo)


class Config(unittest.TestCase):
    def test_default_deck_runs_without_config(self):
        views = build_views(DEFAULT_TABS)
        self.assertEqual([v.name for v in views], ["Processes", "Disk", "Services", "GPUs", "AI quotas"])
        for v in views:
            v.update(force=True)
        self.assertTrue(views[2].is_demo and views[3].is_demo)

    def test_bad_tab_becomes_error_tab(self):
        v = build_views([{"name": "x", "type": "nope"}])[0]
        self.assertEqual(v.get_status(), "config error")

    def test_python_plugin_and_toml(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "mine.py"), "w") as f:
            f.write("from herdeck.views import TableView\n"
                    "class View(TableView):\n"
                    "    def fetch(self):\n"
                    "        return ['A'], [[self.cfg['word']]]\n")
        cfg = os.path.join(d, "tabs.toml")
        with open(cfg, "w") as f:
            f.write(f'[[tab]]\nname = "Mine"\ntype = "python"\npath = "{d}/mine.py"\nword = "hello"\n')
        views, _, src = load(cfg)
        views[0].update(force=True)
        self.assertEqual(src, cfg)
        self.assertEqual(views[0].rows, [["hello"]])

    def test_example_config_parses(self):
        ex = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "tabs.toml")
        views, _, _ = load(ex)
        self.assertGreaterEqual(len(views), 10)
        self.assertEqual([v for v in views if v.get_status() == "config error" and "Weather" not in v.name], [])


if __name__ == "__main__":
    unittest.main()
