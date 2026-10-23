"""Purely synthetic, generic demo and mock data for herdeck when services are unconfigured."""

from typing import Any, Dict, List


def get_demo_gpus() -> List[Dict[str, Any]]:
    return [
        {
            "id": "gpu0",
            "label": "GPU 0: RTX 4090",
            "active": "render-lane",
            "card": {
                "util": 45.0,
                "vram_used_mb": 12288.0,
                "vram_total_mb": 24576.0,
                "temp_c": 58.0,
                "power_w": 240.0,
            },
            "lanes": [
                {"id": "render-lane", "label": "Render Lane (Active)", "kind": "render"},
                {"id": "llm-lane", "label": "LLM Inference Lane", "kind": "llm"},
            ],
            "procs": [
                {"pid": "1042", "name": "python render_worker.py", "mem": "11.8 GiB"}
            ],
            "util_hist": [10.0, 15.0, 20.0, 45.0, 60.0, 45.0, 50.0, 45.0],
        },
        {
            "id": "gpu1",
            "label": "GPU 1: RTX 3080",
            "active": "llm-lane",
            "card": {
                "util": 12.0,
                "vram_used_mb": 4096.0,
                "vram_total_mb": 10240.0,
                "temp_c": 46.0,
                "power_w": 85.0,
            },
            "lanes": [
                {"id": "llm-lane", "label": "LLM Inference Lane", "kind": "llm"},
                {"id": "whisper-lane", "label": "Audio ASR Lane", "kind": "asr"},
            ],
            "procs": [
                {"pid": "2088", "name": "llama-server", "mem": "3.8 GiB"}
            ],
            "util_hist": [5.0, 5.0, 12.0, 12.0, 12.0, 10.0, 12.0, 12.0],
        },
    ]


def get_demo_nodes(node_names: List[str]) -> Dict[str, Dict[str, Any]]:
    nodes = {}
    sample_metrics = [
        {"cpu": 18.0, "l1": 0.45, "l5": 0.50, "l15": 0.42, "used_g": 6.2, "tot_g": 32.0, "cores": 16, "spark": [10, 12, 15, 20, 18, 18, 18]},
        {"cpu": 8.0, "l1": 0.20, "l5": 0.18, "l15": 0.15, "used_g": 4.1, "tot_g": 32.0, "cores": 16, "spark": [5, 5, 8, 8, 8, 8, 8]},
    ]
    for idx, name in enumerate(node_names):
        m = sample_metrics[idx % len(sample_metrics)]
        mem_pct = (m["used_g"] / m["tot_g"]) * 100.0
        nodes[name] = {
            "key": name,
            "cpu_util": m["cpu"],
            "cores": m["cores"],
            "load1": m["l1"],
            "load5": m["l5"],
            "load15": m["l15"],
            "mem_used_gb": m["used_g"],
            "mem_total_gb": m["tot_g"],
            "mem_pct": mem_pct,
            "spark": [float(x) for x in m["spark"]],
        }
    return nodes


def get_demo_cliproxy() -> Dict[str, Any]:
    return {
        "claude": {"5h": 12.0, "7d": 45.0, "5h_reset": "2026-10-23T04:00:00Z", "7d_reset": "2026-10-26T05:00:00Z"},
        "codex": {"plan": "Plus", "primary": 5.0, "secondary": 10.0, "allowed": True},
        "antigravity": {"gemini": 25.0, "gemini_reset": "2026-10-23T04:30:00Z", "other": 80.0, "other_reset": "2026-10-26T18:00:00Z"},
        "models": [
            ("claude-3-7-sonnet", 420),
            ("gemini-2.5-flash", 280),
            ("gpt-4o", 115),
            ("claude-3-5-haiku", 64),
        ],
        "posts": {"200": 870, "429": 2},
    }
