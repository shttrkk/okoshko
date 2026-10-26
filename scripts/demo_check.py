"""Read-only REVIEW003 acceptance over HTTP; never creates demo hotel claims.

POST /api/search is the public search API and may append its ordinary trace log.
No simulator, confirmation, booking, change or administrative action is called.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "data/eval/persona_acceptance.json"
SUPPORTED = {"yes", "partial"}
FAMILY_GUARD_HOTEL_ID = "osm-way-201474330"  # Brevis: real computed route contains stairs.


class CheckFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def expected_ids(result: dict, expected: list[str]):
    parsed = result.get("parsed", {})
    actual = {row.get("characteristic_id") for row in [*parsed.get("must", []), *parsed.get("nice", [])]}
    require(set(expected) <= actual, f"Parser missing {sorted(set(expected)-actual)}; recognized={sorted(actual)}")


def validate_cards(result: dict) -> list[dict]:
    hits = result.get("results")
    require(isinstance(hits, list) and hits, "Search returned no result cards")
    seen = set()
    for hit in hits:
        hotel = hit.get("hotel", {})
        hid = hotel.get("id")
        require(hid and hotel.get("name"), "Result card has no hotel id/name")
        require(hid not in seen, f"Duplicate result card: {hid}")
        seen.add(hid)
        verdicts = hit.get("verdicts")
        require(verdicts and hit.get("summary"), f"Empty result card: {hid}")
        require(all(v.get("text") and v.get("status") in {"yes", "partial", "no", "unknown"} for v in verdicts), f"Empty/invalid verdict in {hid}")
        required = [v for v in verdicts if v.get("required")]
        if any(v.get("status") in {"unknown", "no"} for v in required):
            require(hit.get("group") != "all", f"{hid}: group=all contains an unknown/rejected required condition")
    return hits


def validate_masha(result: dict, wall_ms: float, spec: dict) -> dict:
    expected_ids(result, spec["expected_characteristic_ids"])
    require(result.get("parsed", {}).get("month") == spec["month"], "Masha month differs from the explicit September demo assumption")
    hits = validate_cards(result)
    api_ms = result.get("latency_ms")
    maximum = spec["maximum_latency_ms_exclusive"]
    require(number(api_ms) and 0 <= api_ms < maximum, f"Masha API latency {api_ms!r} ms; expected <{maximum} ms")
    require(number(wall_ms) and 0 <= wall_ms < maximum, f"Masha wall latency {wall_ms!r} ms; expected <{maximum} ms")
    selected = [hit for hit in hits if hit.get("group") == "all"]
    minimum = spec["minimum_all_results"]
    require(len(selected) >= minimum, f"Masha has {len(selected)} group=all results; expected >={minimum}")
    for hit in selected:
        required = [v for v in hit["verdicts"] if v.get("required")]
        require(required, f"{hit['hotel']['id']}: all card has no required verdicts")
        require(set(spec["expected_characteristic_ids"]) <= {v.get("characteristic_id") for v in required}, f"{hit['hotel']['id']}: missing required Masha verdict")
        for verdict in required:
            confidence = verdict.get("confidence")
            require(verdict.get("status") in SUPPORTED and number(confidence) and confidence >= spec["minimum_confidence"], f"{hit['hotel']['id']}: unsupported required verdict {verdict}")
            require(verdict.get("evidence_refs") and verdict.get("fact_ids"), f"{hit['hotel']['id']}: required verdict lacks provenance")
    caveated = [hit["hotel"]["id"] for hit in selected if re.search(
        r"фасад|сторон|этаж", " ".join([hit.get("room_recommendation") or "", *[str(v.get("text", "")) + " " + str(v.get("caveat") or "") for v in hit["verdicts"]]]), re.IGNORECASE)]
    require(caveated, "Masha has no facade/floor caveat in any all result")
    return {"all_hotel_ids": [h["hotel"]["id"] for h in selected], "caveated_hotel_ids": caveated, "api_latency_ms": api_ms, "wall_latency_ms": round(wall_ms, 3)}


def validate_persona(result: dict, spec: dict) -> dict:
    expected_ids(result, spec["expected_characteristic_ids"])
    hits = validate_cards(result)
    meaningful = [hit for hit in hits if any(v.get("status") in SUPPORTED and number(v.get("confidence")) and v["confidence"] > 0 and v.get("evidence_refs") and v.get("fact_ids") for v in hit["verdicts"])]
    require(len(meaningful) >= spec["minimum_meaningful_results"], f"{spec['id']}: only {len(meaningful)} meaningful cards, expected >={spec['minimum_meaningful_results']}")
    return {"query": spec["text"], "meaningful_count": len(meaningful), "sample_hotel_ids": [h["hotel"]["id"] for h in meaningful[:3]],
            "recognized_ids": sorted({c["characteristic_id"] for c in [*result["parsed"].get("must", []), *result["parsed"].get("nice", [])]}),
            "unmapped": result["parsed"].get("unmapped", []), "clarifications": result["parsed"].get("clarifications", []), "limitation": spec.get("limitation")}


def validate_family_route_guard(result: dict, detail: dict) -> dict:
    """Known negative: a real route with stairs cannot satisfy the family request."""
    must = result.get("parsed", {}).get("must", [])
    routes = [c for c in must if c.get("characteristic_id") == "beach_walk"]
    require(len(routes) == 1, "Family request must have exactly one mandatory beach_walk criterion")
    require(routes[0].get("field") == "minutes" and routes[0].get("op") == "lte" and number(routes[0].get("value")) and routes[0]["value"] > 0 and routes[0].get("no_stairs") is True
            and routes[0].get("no_crossings") is True, "Family beach criterion lost its numeric/AND route constraints")
    playground = [c for c in must if c.get("characteristic_id") == "family_nearby"]
    require(any(c.get("field") == "playground_distance_m" and c.get("op") == "lte" and c.get("value") == 500
                and "500" in c.get("label", "") and "м" in c.get("label", "") for c in playground), "Family 'nearby playground' has no explicit 500 m threshold")
    require(not any(re.search(r"\bрядом\b", fragment, re.I) for fragment in result.get("parsed", {}).get("unmapped", [])), "Mapped proximity remains unmapped")
    require(detail.get("hotel", {}).get("id") == FAMILY_GUARD_HOTEL_ID, "Family guard returned the wrong hotel")
    claims = {c["id"]: c for c in detail.get("claims", [])}
    real_routes = [f for f in detail.get("facts", []) if f.get("characteristic_id") == "beach_walk"
                   and f.get("synthetic") is False and f.get("value", {}).get("has_steps") is True]
    require(real_routes, "Brevis known-negative guard has no real route with stairs")
    for fact in real_routes:
        supporting = [claims[cid] for cid in fact.get("supporting_claim_ids", []) if cid in claims]
        require(supporting and len(supporting) == len(fact.get("supporting_claim_ids", [])) and all(c.get("source") == "geo_engine" and c.get("synthetic") is False for c in supporting)
                and fact.get("evidence"), "Brevis stairs lack real geo provenance")
    hits = validate_cards(result)
    hit = next((h for h in hits if h.get("hotel", {}).get("id") == FAMILY_GUARD_HOTEL_ID), None)
    require(hit is not None, "Brevis is absent from the full family search response")
    require(hit.get("group") != "all", "Brevis route with stairs incorrectly satisfies all family conditions")
    verdicts = [v for v in hit.get("verdicts", []) if v.get("required") and v.get("characteristic_id") == "beach_walk"]
    require(len(verdicts) == 1 and verdicts[0].get("status") in {"no", "unknown"}, "Brevis stairs incorrectly support a no-stairs route")
    route = verdicts[0]
    require(set(route.get("fact_ids", [])) & {f["id"] for f in real_routes} and route.get("evidence_refs"), "Family rejection lost the real route evidence")
    require(set(route["evidence_refs"]) & {e["id"] for f in real_routes if f["id"] in route["fact_ids"] for e in f["evidence"]}, "Family route evidence reference does not resolve")
    require("лестниц" in (route.get("text", "") + " " + str(route.get("caveat") or "")).lower(), "Family rejection does not explain stairs")
    return {"hotel_id": FAMILY_GUARD_HOTEL_ID, "group": hit["group"], "route_status": route["status"],
            "fact_ids": route["fact_ids"], "criterion": routes[0]}


def fact_scope(fact: dict) -> dict:
    result = dict(fact.get("scope", {}))
    value = fact.get("value") if isinstance(fact.get("value"), dict) else {}
    result["facade"] = result.get("facade") or value.get("facade")
    if value.get("available") is True:
        result["floor_min"] = result.get("floor_min") or value.get("min_floor")
        result["floor_max"] = result.get("floor_max") or value.get("max_floor")
    result["month"] = result.get("month") or value.get("month")
    return result


def validate_hit_scope(hit: dict, detail: dict) -> dict:
    facts = {fact["id"]: fact for fact in detail.get("facts", [])}
    scopes = []
    for verdict in hit["verdicts"]:
        if not verdict.get("required"):
            continue
        for fid in verdict.get("fact_ids", []):
            require(fid in facts, f"{hit['hotel']['id']}: verdict references missing fact {fid}")
            fact = facts[fid]
            require(fact.get("characteristic_id") == verdict.get("characteristic_id"), f"{fid}: wrong characteristic in verdict reference")
            evidence_ids = {e.get("id") for e in fact.get("evidence", [])}
            require(set(verdict.get("evidence_refs", [])) & evidence_ids, f"{fid}: verdict evidence does not resolve to its fact")
            scopes.append(fact_scope(fact))
    facades = {s.get("facade") for s in scopes if s.get("facade")}
    room_types = {s.get("room_type") for s in scopes if s.get("room_type")}
    months = {s.get("month") for s in scopes if s.get("month")}
    low = max([s["floor_min"] for s in scopes if s.get("floor_min")] or [1])
    high = min([s["floor_max"] for s in scopes if s.get("floor_max")] or [100])
    require(len(facades) <= 1 and len(room_types) <= 1 and len(months) <= 1 and low <= high, f"{hit['hotel']['id']}: incompatible required room scopes: {scopes}")
    return {"facade": next(iter(facades), None), "floor_min": low, "floor_max": high, "month": next(iter(months), None)}


def scope_subset(inner: dict, outer: dict) -> bool:
    return all(outer.get(key) is None or inner.get(key) == outer[key] for key in ("facade", "month", "room_type")) and (
        (inner.get("floor_min") or 1) >= (outer.get("floor_min") or 1) and
        (inner.get("floor_max") or 100) <= (outer.get("floor_max") or 100))


def validate_synthetic_detail(detail: dict, expected: dict) -> dict:
    hid = expected["hotel_id"]
    require(detail.get("hotel", {}).get("id") == hid, f"Wrong hotel detail returned for {hid}")
    facts = detail.get("facts", [])
    claims = {c["id"]: c for c in detail.get("claims", [])}
    synthetic = [f for f in facts if f.get("characteristic_id") == "quiet_night" and f.get("synthetic") is True
                 and scope_subset(f.get("scope", {}), expected)]
    require(synthetic, f"{hid}: missing synthetic quiet fact for the dedicated floor/facade/month scope")
    fact = max(synthetic, key=lambda f: f.get("confidence", 0))
    require(fact.get("scope", {}).get("type") == "floor_range", f"{hid}: synthetic quiet scope is not floor_range")
    require(fact.get("value", {}).get("available") is True and number(fact.get("confidence")) and fact["confidence"] >= .6
            and fact.get("status") in {"computed", "confirmed"}, f"{hid}: unsupported synthetic quiet value/confidence/status")
    require(not any("db" in key for key in fact.get("value", {})), f"{hid}: synthetic quiet incorrectly contains measured dB fields")
    supporting = [claims[cid] for cid in fact.get("supporting_claim_ids", []) if cid in claims]
    require(supporting and len(supporting) == len(fact.get("supporting_claim_ids", [])), f"{hid}: synthetic quiet has no complete supporting claims")
    require(all(scope_subset(fact.get("scope", {}), c.get("scope", {})) for c in supporting), f"{hid}: synthetic fact scope exceeds its supporting claim")
    fixtures = [c for c in supporting if c.get("synthetic") is True]
    require(fixtures, f"{hid}: synthetic quiet lost its immutable synthetic fixture")
    artifacts = set()
    for claim in fixtures:
        require(claim.get("source") == "hotelier_simulator" and claim.get("synthetic") is True, f"{hid}: synthetic quiet is attributed as a real hotel claim")
        require(all(claim.get("scope", {}).get(k) == expected[k] for k in ("facade", "floor_min", "floor_max", "month")), f"{hid}: supporting synthetic claim scope differs from its fact")
        require(abs(claim.get("confidence", 0)-.85) < 1e-6, f"{hid}: synthetic claim confidence differs from .85")
        require(claim.get("method") == "simulated_hotelier_quote@1", f"{hid}: unexpected synthetic fixture method")
        for ev in claim.get("evidence", []):
            require(ev.get("kind") == "simulation", f"{hid}: synthetic quote lacks simulation evidence kind")
            require(str(ev.get("quote", "")).startswith("Синтетический ответ для демонстрации:") and ev["quote"] == ev.get("inputs", {}).get("raw_text"), f"{hid}: missing or changed exact synthetic answer quote")
            require(ev.get("limitations"), f"{hid}: synthetic quote has no limitations")
            require(ev.get("artifact") == "/data/seed/demo_hotelier_claims.jsonl", f"{hid}: missing public synthetic ledger artifact")
            artifacts.add(ev["artifact"])
    require(all(c.get("source") == "geo_engine" and c.get("synthetic") is False and abs(c.get("confidence", 0)-.38) < 1e-6
                for c in supporting if c not in fixtures), f"{hid}: merged real exterior-noise provenance changed")
    require(artifacts, f"{hid}: synthetic claim has no accessible evidence artifact")
    native = [f for f in facts if f.get("characteristic_id") == "quiet_night" and f.get("synthetic") is False and f.get("scope", {}).get("type") == "facade" and f["scope"].get("facade") == expected["facade"]]
    require(native, f"{hid}: original real exterior-noise fact disappeared")
    for original in native:
        require(abs(original.get("confidence", 0)-.38) < 1e-6, f"{hid}: original exterior-noise confidence changed from 0.38")
        underlying = [claims[cid] for cid in original.get("supporting_claim_ids", []) if cid in claims]
        require(underlying and all(c.get("source") == "geo_engine" and c.get("synthetic") is False and abs(c.get("confidence", 0)-.38) < 1e-6 for c in underlying), f"{hid}: original .38 noise provenance was replaced")
    return {"hotel_id": hid, "synthetic_fact_id": fact["id"], "synthetic_confidence": fact["confidence"], "real_noise_confidence": .38, "artifacts": sorted(artifacts), "scope": fact["scope"]}


def validate_geo(geo: dict, hotel_id: str) -> dict:
    require(geo.get("hotel_id") == hotel_id, "Hero /geo has the wrong hotel id")
    require(geo.get("artifact") and geo.get("calculation_version") and geo.get("computed_at"), "Hero /geo is missing calculation artifact/version/time metadata")
    require(geo.get("inputs") and geo.get("limitations"), "Hero /geo lacks input provenance or model limitations")
    facades, horizon = geo.get("facades"), geo.get("horizon")
    require(facades and all(f.get("code") and number(f.get("azimuth_deg")) and f.get("geometry", {}).get("coordinates") for f in facades), "Hero /geo has no usable facade geometry")
    require(horizon and all(number(row.get("azimuth_deg")) and "terrain_deg" in row and "buildings_deg" in row for row in horizon), "Hero /geo horizon metadata is missing")
    route = geo.get("route", {})
    require(route.get("type") == "FeatureCollection" and any(f.get("geometry", {}).get("type") in {"LineString", "MultiLineString"} and f["geometry"].get("coordinates") for f in route.get("features", [])), "Hero /geo has no routed beach geometry")
    details = geo.get("route_details", {})
    require(details.get("available") is True and number(details.get("minutes")) and details["minutes"] > 0 and number(details.get("distance_m")) and details["distance_m"] > 0, "Hero /geo has no usable route metrics")
    require(any(row.get("month") == 9 and row.get("points") for row in geo.get("sun_paths", [])), "Hero /geo has no September sun path")
    require(geo.get("monte_carlo", {}).get("iterations", 0) > 0, "Hero /geo lacks Monte Carlo sensitivity metadata")
    return {"facades": len(facades), "horizon_samples": len(horizon), "route_minutes": details["minutes"], "artifact": geo["artifact"], "calculation_version": geo["calculation_version"]}


class Runner:
    def __init__(self, client, config):
        self.client, self.config = client, config
        self.checks, self.requests = [], []
        self.details, self.artifacts = {}, {}
        self.masha = None

    def request(self, path, payload=None):
        started = time.perf_counter()
        response = self.client.request("GET" if payload is None else "POST", path, json=payload)
        elapsed = 1000*(time.perf_counter()-started)
        self.requests.append({"method": "GET" if payload is None else "POST", "path": path, "status": response.status_code, "wall_ms": round(elapsed, 3)})
        response.raise_for_status()
        return response, elapsed

    def detail(self, hotel_id, text=None):
        key = hotel_id, text
        if key not in self.details:
            suffix = "?text=" + quote(text) if text else ""
            response, _ = self.request("/api/hotels/" + quote(hotel_id, safe="") + suffix)
            self.details[key] = response.json()
        return self.details[key]

    def artifact(self, path):
        require(isinstance(path, str) and path.startswith("/data/") and not path.startswith("//"), f"Expected a local public artifact, got {path!r}")
        if path not in self.artifacts:
            response, _ = self.request(path)
            require(response.status_code == 200 and response.content and "text/html" not in response.headers.get("content-type", ""), f"Artifact unavailable or SPA fallback: {path}")
            self.artifacts[path] = len(response.content)
        return self.artifacts[path]

    def check(self, name, operation):
        try:
            detail = operation()
            record = {"id": name, "passed": True, "details": detail}
        except (CheckFailure, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            record = {"id": name, "passed": False, "error": str(error)}
        self.checks.append(record)
        print(("PASS " if record["passed"] else "FAIL ") + name + ("" if record["passed"] else ": " + record["error"]), flush=True)

    def masha_search(self):
        response, elapsed = self.request("/api/search", {"text": self.config["masha"]["text"], "limit": 20})
        self.masha = response.json()
        result = validate_masha(self.masha, elapsed, self.config["masha"])
        result["selected_room_scopes"] = {hit["hotel"]["id"]: validate_hit_scope(hit, self.detail(hit["hotel"]["id"]))
                                          for hit in self.masha["results"] if hit.get("group") == "all"}
        return result

    def synthetic_hotel(self, expected):
        detail = self.detail(expected["hotel_id"])
        result = validate_synthetic_detail(detail, expected)
        for artifact in result["artifacts"]:
            self.artifact(artifact)
        require(self.masha is not None, "Masha response unavailable")
        hit = next((h for h in self.masha.get("results", []) if h.get("hotel", {}).get("id") == expected["hotel_id"] and h.get("group") == "all"), None)
        require(hit is not None, f"{expected['hotel_id']}: dedicated synthetic hotel absent from Masha group=all")
        result["selected_room_scope"] = validate_hit_scope(hit, detail)
        quiet = next((v for v in hit["verdicts"] if v.get("characteristic_id") == "quiet_night" and v.get("required")), None)
        require(quiet is not None, f"{expected['hotel_id']}: Masha quiet verdict is missing")
        require(result["synthetic_fact_id"] in quiet.get("fact_ids", []) and quiet.get("synthetic") is True, f"{expected['hotel_id']}: Masha quiet verdict hides/does not use synthetic fixture")
        return result

    def hero(self):
        hid = self.config["hero_hotel_id"]
        detail = self.detail(hid, "вид на море, закат из окна, тихий номер, до пляжа не больше 10 минут")
        facts = {f["id"]: f for f in detail.get("facts", [])}
        claims = {c["id"]: c for c in detail.get("claims", [])}
        verified = {}
        for cid in self.config["hero_characteristic_ids"]:
            verdicts = [v for v in detail.get("verdicts", []) if v.get("characteristic_id") == cid]
            require(verdicts and any(v.get("evidence_refs") and v.get("fact_ids") for v in verdicts), f"Hero {cid}: no verdict evidence references")
            linked = [facts[fid] for v in verdicts for fid in v.get("fact_ids", []) if fid in facts]
            require(linked and all(f.get("supporting_claim_ids") and f.get("evidence") for f in linked), f"Hero {cid}: missing fact-to-claim provenance")
            evidence = [e for f in linked for e in f.get("evidence", []) if e.get("id") in {eid for v in verdicts for eid in v.get("evidence_refs", [])}]
            require(evidence, f"Hero {cid}: unresolved evidence ids")
            for fact in linked:
                require(all(claim_id in claims for claim_id in fact["supporting_claim_ids"]), f"Hero {cid}: supporting claim missing from detail")
            local = {e["artifact"] for e in evidence if str(e.get("artifact", "")).startswith("/data/")}
            require(local, f"Hero {cid}: no local calculation artifact")
            for path in local:
                self.artifact(path)
            verified[cid] = {"fact_ids": [f["id"] for f in linked], "artifacts": sorted(local)}
        response, _ = self.request("/api/hotels/" + quote(hid, safe="") + "/geo")
        geometry = validate_geo(response.json(), hid)
        self.artifact(geometry["artifact"])
        return {"hotel_id": hid, "characteristics": verified, "geo": geometry}

    def persona(self, spec):
        response, _ = self.request("/api/search", {"text": spec["text"], "limit": 20})
        return validate_persona(response.json(), spec)

    def family_route_guard(self):
        spec = next(p for p in self.config["personas"] if p["id"] == "family_stroller")
        response, _ = self.request("/api/search", {"text": spec["text"], "limit": 250})
        return validate_family_route_guard(response.json(), self.detail(FAMILY_GUARD_HOTEL_ID))

    def run(self):
        self.check("health", lambda: self._health())
        self.check("masha", self.masha_search)
        for expected in self.config["synthetic_hotels"]:
            self.check("synthetic_provenance:" + expected["hotel_id"], lambda expected=expected: self.synthetic_hotel(expected))
        self.check("hero_geo_evidence", self.hero)
        for spec in self.config["personas"]:
            self.check("persona:" + spec["id"], lambda spec=spec: self.persona(spec))
        self.check("family_route_known_negative", self.family_route_guard)
        return {"mode": "synthetic-demo", "passed": all(c["passed"] for c in self.checks), "checked_at": datetime.now(UTC).isoformat(),
                "checks": self.checks, "requests": self.requests, "limitations": self.config["limitations"]}

    def _health(self):
        response, _ = self.request("/health")
        require(response.json() == {"status": "ok"}, "Health is not ready")
        response, _ = self.request("/api/status")
        return response.json()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only REVIEW003 synthetic-demo acceptance")
    parser.add_argument("--url", default=os.getenv("OKNO_URL", "http://localhost:8080"))
    parser.add_argument("--report", type=Path, default=Path(os.getenv("OKNO_DEMO_REPORT", "/tmp/okno-demo-check.json")))
    args = parser.parse_args(argv)
    print("О!кно acceptance mode: synthetic-demo (fixtures are invented; real noise confidence stays 0.38)", flush=True)
    config = json.loads(CONFIG.read_text())
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=10, follow_redirects=False) as client:
        report = Runner(client, config).run()
    report["base_url"] = args.url
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(("PASS" if report["passed"] else "FAIL") + f" synthetic-demo; report: {args.report}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
