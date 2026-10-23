#!/usr/bin/env python3
"""Example exec tab: a 3-day forecast. Any language works; print one JSON object.

    [[tab]]
    name = "Weather"
    type = "exec"
    command = "python3 ~/.config/herdeck/weather.py Toronto"
    interval = 600

Output format (herdeck rows):
    {"header": "...", "columns": ["A", "B"], "rows": [[cell, ...], ...], "details": ["...", ...]}
A cell is a string or number, or {"text": "..", "tone": "ok|warn|bad|accent|dim"},
{"bar": 41, "max": 100, "text": "41%"}, or {"spark": [1, 2, 3]}.
A row can also be {"section": "Title"}. Exit non-zero (with a message on stderr) on failure.
"""
import json
import sys
import urllib.parse
import urllib.request

city = sys.argv[1] if len(sys.argv) > 1 else "Toronto"
url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "herdeck-example"}), timeout=10) as r:
    data = json.load(r)

rows, details = [], []
for day in data["weather"]:
    rain = max(int(h["chanceofrain"]) for h in day["hourly"])
    rows.append([
        day["date"],
        f'{day["mintempC"]}..{day["maxtempC"]} C',
        {"bar": rain, "max": 100},
        {"text": "rain", "tone": "warn"} if rain > 50 else {"text": "dry", "tone": "ok"},
    ])
    details.append(", ".join(h["weatherDesc"][0]["value"] for h in day["hourly"][::3]))

print(json.dumps({"header": f"3-day forecast for {city}", "columns": ["DATE", "TEMP", "RAIN", ""],
                  "rows": rows, "details": details}))
