"""Example custom tab: a TableView subclass in ~20 lines.

Point a tab at it:
    [[tab]]
    name = "Weather"
    type = "python"
    path = "~/.config/herdeck/views/weather.py"
    city = "Toronto"

fetch() runs in a background thread, may block, and returns (columns, rows).
Cells can be plain strings, Text(value, tone), Bar(value, max) or Spark(values).
Everything in the [[tab]] table is available as self.cfg.
"""
import json
import urllib.parse
import urllib.request

from herdeck.views import Bar, TableView, Text


class View(TableView):
    default_interval = 600  # seconds

    def fetch(self):
        city = self.cfg.get("city", "Toronto")
        url = "https://wttr.in/" + urllib.parse.quote(city) + "?format=j1"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
        rows = []
        for day in data["weather"]:
            hi, lo = float(day["maxtempC"]), float(day["mintempC"])
            rain = max(float(h["chanceofrain"]) for h in day["hourly"])
            rows.append([day["date"], f"{lo:.0f}..{hi:.0f} C", Bar(rain, 100),
                         Text("rain", "warn") if rain > 50 else Text("dry", "ok")])
        return ["DATE", "TEMP", "RAIN CHANCE", ""], rows

    def header(self):
        return f"3-day forecast for {self.cfg.get('city', 'Toronto')}"
