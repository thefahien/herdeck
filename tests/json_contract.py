#!/usr/bin/env python3
"""Contract test for `herdeck --json` (docs/json.md), stdlib only.

Checks the sample documents in docs/json/ against the schema rules, then runs the
real binary against throwaway decks. Usage: python3 -I tests/json_contract.py [path/to/herdeck]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BIN = os.path.abspath(
    (sys.argv.pop(1) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else None)
    or os.environ.get("HERDECK_BIN")
    or os.path.join(ROOT, "target", "release", "herdeck")
)
SAMPLES = os.path.join(ROOT, "docs", "json")

TONES = {"ok", "warn", "bad", "accent", "dim"}
STATES = {"ok", "demo", "not_set_up", "error", "timeout", "skipped"}
NO_TABLE = {"error", "timeout", "skipped"}
ERROR_KINDS = {"no_deck", "config", "no_such_tab"}


def isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def isint(x):
    return isinstance(x, int) and not isinstance(x, bool)


def keys(d, expected, what):
    assert isinstance(d, dict), f"{what}: not an object"
    assert list(d) == expected, f"{what}: keys {list(d)} != {expected}"


def bar_tone(value, mx):
    r = min(max(value / mx, 0.0), 1.0) if mx > 0 else 0.0
    return "bad" if r >= 0.9 else "warn" if r >= 0.7 else "ok"


def check_cell(c, where):
    assert isinstance(c, dict), f"{where}: cell is not an object"
    kind = c.get("kind")
    if kind == "text":
        keys(c, ["kind", "text", "tone"], where)
        assert isinstance(c["text"], str), where
        assert c["tone"] is None or c["tone"] in TONES, f"{where}: tone {c['tone']!r}"
    elif kind == "bar":
        keys(c, ["kind", "value", "max", "text", "tone", "width"], where)
        assert isnum(c["value"]) and isnum(c["max"]), where
        assert isinstance(c["text"], str), where
        assert c["tone"] == bar_tone(c["value"], c["max"]), f"{where}: bar tone {c['tone']!r}"
        assert isint(c["width"]) and c["width"] >= 0, where
    elif kind == "spark":
        keys(c, ["kind", "values", "max", "tone", "width"], where)
        assert isinstance(c["values"], list) and all(isnum(v) for v in c["values"]), where
        assert c["max"] is None or isnum(c["max"]), where
        assert c["tone"] == "accent", where
        assert isint(c["width"]) and c["width"] >= 0, where
        assert len(c["values"]) <= c["width"], f"{where}: more values than width"
    else:
        raise AssertionError(f"{where}: unknown cell kind {kind!r}")


def check_row(r, where):
    assert isinstance(r, dict), where
    if "section" in r:
        keys(r, ["section"], where)
        assert isinstance(r["section"], str), where
        return
    ks = list(r)
    assert ks[0] == "cells" and ks == [k for k in ("cells", "detail", "key", "actions") if k in r], f"{where}: keys {ks}"
    assert isinstance(r["cells"], list), where
    for i, c in enumerate(r["cells"]):
        check_cell(c, f"{where}.cells[{i}]")
    for k in ("detail", "key"):
        if k in r:
            assert isinstance(r[k], str) and r[k], f"{where}.{k} must be a non-empty string when present"
    if "actions" in r:
        assert "key" in r, f"{where}: actions need a key"
        check_actions(r["actions"], f"{where}.actions")


def check_actions(acts, where):
    assert isinstance(acts, list) and acts, f"{where}: actions must be a non-empty list when present"
    for i, a in enumerate(acts):
        w = f"{where}[{i}]"
        keys(a, ["key", "title", "warning", "options"], w)
        assert isinstance(a["key"], str) and len(a["key"]) == 1, w
        assert isinstance(a["title"], str) and a["title"], w
        assert a["warning"] is None or isinstance(a["warning"], str), w
        assert isinstance(a["options"], list) and a["options"], w
        for o in a["options"]:
            keys(o, ["id", "label"], w + ".options")
            assert isinstance(o["id"], str) and isinstance(o["label"], str), w


def check_table(t, where):
    keys(t, ["header", "columns", "rows"], where)
    assert t["header"] is None or isinstance(t["header"], str), where
    assert isinstance(t["columns"], list) and all(isinstance(c, str) for c in t["columns"]), where
    assert isinstance(t["rows"], list), where
    for i, r in enumerate(t["rows"]):
        check_row(r, f"{where}.rows[{i}]")


STATUS = {
    "skipped": lambda s: s == "skipped",
    "timeout": lambda s: s == "timeout",
    "error": lambda s: s.startswith("error: "),
    "demo": lambda s: s.startswith("DEMO ("),
    "not_set_up": lambda s: s == "not set up",
}


def check_tab(t, index):
    where = f"tabs[{index - 1}]"
    keys(t, ["index", "name", "type", "interval", "state", "status", "message", "table"], where)
    assert t["index"] == index, f"{where}: index {t['index']}"
    assert isinstance(t["name"], str) and isinstance(t["type"], str), where
    assert isnum(t["interval"]) and t["interval"] >= 0.5, where
    assert t["state"] in STATES, f"{where}: state {t['state']!r}"
    assert isinstance(t["status"], str), where
    if t["state"] in STATUS:
        assert STATUS[t["state"]](t["status"]), f"{where}: status {t['status']!r} for {t['state']}"
    assert t["message"] is None or isinstance(t["message"], str), where
    if t["state"] in ("ok", "skipped"):
        assert t["message"] is None, f"{where}: message on {t['state']}"
    else:
        assert t["message"], f"{where}: {t['state']} needs a message"
    if t["state"] in NO_TABLE:
        assert t["table"] is None, f"{where}: {t['state']} must have table null"
    else:
        assert t["table"] is not None, f"{where}: {t['state']} needs a table"
        check_table(t["table"], where + ".table")


def check_header(d):
    assert d["schema"] == 1, d["schema"]
    assert isinstance(d["herdeck"], str) and re.match(r"^\d+\.\d+\.\d+", d["herdeck"]), d["herdeck"]
    assert d["bin"] is None or isinstance(d["bin"], str)


def check_doc(d):
    keys(d, ["schema", "herdeck", "bin", "deck", "demo", "tabs"], "document")
    check_header(d)
    assert isinstance(d["deck"], str) and d["deck"], "deck"
    assert isinstance(d["demo"], bool), "demo"
    assert isinstance(d["tabs"], list) and d["tabs"], "tabs must not be empty"
    for i, t in enumerate(d["tabs"], 1):
        check_tab(t, i)
    return d


def check_fatal(d, kind):
    keys(d, ["schema", "herdeck", "bin", "error"], "fatal document")
    check_header(d)
    keys(d["error"], ["kind", "message", "path"], "error")
    assert d["error"]["kind"] in ERROR_KINDS, d["error"]["kind"]
    assert d["error"]["kind"] == kind, f"error kind {d['error']['kind']!r}, wanted {kind!r}"
    assert isinstance(d["error"]["message"], str) and d["error"]["message"]
    assert d["error"]["path"] is None or isinstance(d["error"]["path"], str)
    return d


class Samples(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(SAMPLES, name), encoding="utf-8") as f:
            return json.load(f)

    def test_sample_demo(self):
        d = check_doc(self.load("sample-demo.json"))
        self.assertEqual(len(d["tabs"]), 5)

    def test_sample_multi(self):
        d = check_doc(self.load("sample-multi.json"))
        self.assertEqual({t["state"] for t in d["tabs"]}, {"ok", "error", "timeout", "not_set_up"})

    def test_validator_rejects_bad_documents(self):
        d = self.load("sample-multi.json")
        d["tabs"][2]["table"] = d["tabs"][0]["table"]
        with self.assertRaises(AssertionError):
            check_doc(d)
        d = self.load("sample-demo.json")
        d["tabs"][0]["table"]["rows"][0]["cells"][2]["tone"] = "ok"  # a 93% bar is bad
        with self.assertRaises(AssertionError):
            check_doc(d)


class Binary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert os.path.exists(BIN), f"no binary at {BIN}"
        cls.home = tempfile.TemporaryDirectory()
        cls.env = {k: v for k, v in os.environ.items() if k not in ("HERDECK_CONFIG", "XDG_CONFIG_HOME")}
        cls.env["HOME"] = cls.home.name
        cls.env["LC_ALL"] = "C"

    @classmethod
    def tearDownClass(cls):
        cls.home.cleanup()

    def write(self, name, text):
        path = os.path.join(self.home.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def run_bin(self, *args, deck=None, timeout=30):
        env = dict(self.env)
        if deck:
            env["HERDECK_CONFIG"] = deck
        t0 = time.monotonic()
        p = subprocess.run([BIN, *args], env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p, time.monotonic() - t0

    def mixed_deck(self):
        return self.write(
            "mixed.toml",
            '[[tab]]\nname = "Hello"\ntype = "command"\ncommand = "echo hi"\n\n'
            '[[tab]]\nname = "Broken"\ntype = "command"\ncommand = "exit 3"\n\n'
            '[[tab]]\nname = "Slow"\ntype = "command"\ncommand = "sleep 30"\n\n'
            '[[tab]]\nname = "Blank"\ntype = "http"\n',
        )

    # --- no deck ----------------------------------------------------------

    def test_demo_without_a_deck(self):
        none = os.path.join(self.home.name, "none.toml")
        p, _ = self.run_bin("--json", "--demo", deck=none)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = check_doc(json.loads(p.stdout))
        self.assertEqual(len(d["tabs"]), 5)
        self.assertTrue(d["demo"])
        self.assertEqual(d["deck"], "demo deck (sample data)")
        with open(os.path.join(SAMPLES, "sample-demo.json"), encoding="utf-8") as f:
            sample = json.load(f)
        pick = lambda doc: [(t["index"], t["name"], t["type"], t["interval"]) for t in doc["tabs"]]
        self.assertEqual(pick(d), pick(sample))
        self.assertNotIn("skipped", {t["state"] for t in d["tabs"]})

    def test_no_deck_is_a_fatal_document(self):
        none = os.path.join(self.home.name, "none.toml")
        p, _ = self.run_bin("--json", deck=none)
        self.assertEqual(p.returncode, 1)
        d = check_fatal(json.loads(p.stdout), "no_deck")
        self.assertEqual(d["error"]["path"], "~/none.toml")
        self.assertNotIn("tabs", d)
        p, _ = self.run_bin("--json", "--config", os.path.join(self.home.name, "other.toml"))
        self.assertEqual(p.returncode, 1)
        check_fatal(json.loads(p.stdout), "no_deck")

    def test_bad_toml_is_a_config_error(self):
        bad = self.write("bad.toml", "[[tab]\nname = \n")
        p, _ = self.run_bin("--json", deck=bad)
        self.assertEqual(p.returncode, 1)
        d = check_fatal(json.loads(p.stdout), "config")
        self.assertEqual(d["error"]["path"], "~/bad.toml")
        empty = self.write("empty.toml", "refresh = 1\n")
        p, _ = self.run_bin("--json", deck=empty)
        self.assertEqual(p.returncode, 1)
        check_fatal(json.loads(p.stdout), "config")

    # --- a deck with every kind of outcome --------------------------------

    def test_states_and_deadline(self):
        p, wall = self.run_bin("--json", "--timeout", "2", deck=self.mixed_deck())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertLess(wall, 4.0, f"--timeout 2 took {wall:.1f}s")
        d = check_doc(json.loads(p.stdout))
        self.assertEqual([t["state"] for t in d["tabs"]], ["ok", "error", "timeout", "not_set_up"])
        self.assertEqual(d["deck"], "~/mixed.toml")
        self.assertFalse(d["demo"])
        hello = d["tabs"][0]
        self.assertEqual(hello["status"], "OK")
        self.assertEqual(hello["table"]["rows"][0]["cells"], [{"kind": "text", "text": "hi", "tone": None}])
        self.assertEqual(d["tabs"][1]["message"], "exit 3")
        self.assertEqual(d["tabs"][2]["message"], "no answer within 2s")

    def test_single_tab(self):
        deck = self.mixed_deck()
        p, _ = self.run_bin("--json", "--tab", "2", deck=deck)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = check_doc(json.loads(p.stdout))
        self.assertEqual([t["state"] for t in d["tabs"]], ["skipped", "error", "skipped", "skipped"])
        p, _ = self.run_bin("--json", "--tab", "bLaNk", deck=deck)
        d = check_doc(json.loads(p.stdout))
        self.assertEqual([t["state"] for t in d["tabs"]], ["skipped", "skipped", "skipped", "not_set_up"])

    def test_unknown_tab(self):
        deck = self.mixed_deck()
        for want in ("nope", "9", "0"):
            p, _ = self.run_bin("--json", "--tab", want, deck=deck)
            self.assertEqual(p.returncode, 1, want)
            d = check_fatal(json.loads(p.stdout), "no_such_tab")
            self.assertIsNone(d["error"]["path"])

    # --- --act ------------------------------------------------------------

    def act(self, *args, deck=None):
        none = deck or os.path.join(self.home.name, "none.toml")
        p, _ = self.run_bin("--demo", "--act", *args, deck=none)
        return p, json.loads(p.stdout.strip().splitlines()[-1])

    def test_act_switches_a_lane_in_demo(self):
        p, d = self.act("GPUs", "gpu0", "s", "asr")
        self.assertEqual((p.returncode, d["ok"]), (0, True), p.stdout)
        self.assertIn("ASR", d["message"])
        self.assertEqual(d["schema"], 1)

    def test_act_refuses_what_the_deck_did_not_offer(self):
        for args, why in [
            (("GPUs", "gpu9", "s", "asr"), "no row"),
            (("GPUs", "gpu0", "s", "nope"), "not one of"),
            (("GPUs", "gpu0", "ss", "asr"), "one character"),
            (("GPUs", "gpu0", "x", "asr"), "not an action"),
            (("Processes", "gpu0", "s", "asr"), "not an action"),
            (("Nope", "gpu0", "s", "asr"), "no tab"),
        ]:
            p, d = self.act(*args)
            self.assertEqual((p.returncode, d["ok"]), (1, False), f"{args}: {p.stdout}")
            self.assertIn(why, d["message"], args)

    def test_act_without_a_deck_is_an_error_document(self):
        p, _ = self.run_bin("--act", "GPUs", "gpu0", "s", "asr", "--config", os.path.join(self.home.name, "missing.toml"))
        d = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("ok", d)
        self.assertEqual(d["error"]["kind"], "no_deck")

    def test_bad_arguments_exit_2(self):
        for args in (["--json", "--timeout"], ["--json", "--timeout", "soon"], ["--jsn"]):
            p, _ = self.run_bin(*args, deck=self.mixed_deck())
            self.assertEqual(p.returncode, 2, args)
            self.assertEqual(p.stdout, "")
            self.assertIn("unknown argument" if args == ["--jsn"] else "--timeout needs", p.stderr)

    # --- the plain-text modes did not change ------------------------------

    def test_once_is_unchanged(self):
        deck = self.write("hello.toml", '[[tab]]\nname = "Hello"\ntype = "command"\ncommand = "echo hi"\n')
        p, _ = self.run_bin("--once", deck=deck)
        self.assertEqual(p.returncode, 0, p.stderr)
        first, rest = p.stdout.split("\n", 1)
        self.assertRegex(first, r"^# herdeck \d+\.\d+\.\d+: .*hello\.toml$")
        self.assertEqual(rest, "\n## Hello\n  $ echo hi\n  output\n  hi\n")

    def test_json_wins_over_once(self):
        deck = self.write("hello.toml", '[[tab]]\nname = "Hello"\ntype = "command"\ncommand = "echo hi"\n')
        p, _ = self.run_bin("--once", "--json", deck=deck)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(check_doc(json.loads(p.stdout))["tabs"][0]["name"], "Hello")

    def test_types_and_init_list(self):
        p, _ = self.run_bin("--types")
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("command     run a shell command;"), p.stdout[:60])
        p, _ = self.run_bin("init", "--list")
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.startswith("TEMPLATE "), p.stdout[:60])


if __name__ == "__main__":
    unittest.main(verbosity=2)
