"""Offline deployment smoke. Expanded by orchestrator as each wave lands."""
import json
import os
from urllib.request import Request, urlopen

base = os.getenv("OKNO_URL", "http://localhost:8080")


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as response:
        return json.load(response)


assert request("/health") == {"status": "ok"}
status = request("/api/status")
assert status["mode"] in {"offline", "cache", "live"}
print("PASS health + API status; phase:", status.get("phase", "integrated"))

