"""Offline deployment smoke. Expanded by orchestrator as each wave lands."""
import json
import os
from urllib.request import Request, urlopen

base = os.getenv("OKNO_URL", "http://localhost:8080")


def request(path, payload=None, token=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urlopen(req, timeout=10) as response:
        return json.load(response)


assert request("/health") == {"status": "ok"}
status = request("/api/status")
assert status["mode"] in {"offline", "cache", "live"}
print("PASS health + API status; phase:", status.get("phase", "integrated"))
result = request("/api/search", {"text": "тихий номер с видом на закат недалеко от моря", "limit": 3})
assert len(result["results"]) >= 3
hotel_id = result["results"][0]["hotel"]["id"]
detail = request("/api/hotels/" + hotel_id + "/evaluate", result["parsed"])
assert detail["facts"] and detail["verdicts"]
passport = request("/api/passports", {"hotel_id": hotel_id, "guest_name": "Демо smoke: синтетический гость", "parsed": result["parsed"]})
assert passport["items"]
token = request("/api/hotelier/login", {"username": "hotelier", "password": "hotelier123"})["token"]
cabinet = request("/api/hotelier/" + hotel_id, token=token)
assert cabinet["hotel"]["id"] == hotel_id and any(p["id"] == passport["id"] for p in cabinet["passports"])
assert request("/api/passports/" + passport["id"])["id"] == passport["id"]
print("PASS offline search → hotel → persistent passport → authenticated cabinet (synthetic guest)")

