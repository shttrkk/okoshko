"""Negative acceptance boundaries; no server, DB or external network is used."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from scripts.demo_check import (
    FAMILY_GUARD_HOTEL_ID,
    CheckFailure,
    Runner,
    validate_family_route_guard,
    validate_geo,
    validate_hit_scope,
    validate_masha,
    validate_persona,
    validate_synthetic_detail,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "data/eval/persona_acceptance.json").read_text())


def search_response():
    cids = ["quiet_night", "sunset_view", "beach_walk"]
    return {"parsed": {"month": 9, "must": [{"characteristic_id": cid} for cid in cids], "nice": []},
            "latency_ms": 1000, "results": [
                {"hotel": {"id": str(index), "name": "Synthetic test"}, "group": "all", "summary": "Предварительное совпадение",
                 "room_recommendation": "Западная сторона, 5 этаж", "verdicts": [
                     {"characteristic_id": cid, "required": True, "status": "partial", "confidence": .6,
                      "text": "Предварительно", "fact_ids": [cid], "evidence_refs": ["e-"+cid]} for cid in cids]}
                for index in range(3)]}


def test_masha_accepts_exact_confidence_boundary_and_rejects_lower_value():
    response = search_response()
    assert len(validate_masha(response, 1999.9, CONFIG["masha"])["all_hotel_ids"]) == 3
    response["results"][0]["verdicts"][0]["confidence"] = .599999
    with pytest.raises(CheckFailure, match="unsupported required"):
        validate_masha(response, 1000, CONFIG["masha"])


@pytest.mark.parametrize("api_ms,wall_ms", [(2000, 1000), (1000, 2000), (float("nan"), 1000)])
def test_masha_latency_is_strict_and_finite(api_ms, wall_ms):
    response = search_response()
    response["latency_ms"] = api_ms
    with pytest.raises(CheckFailure, match="latency"):
        validate_masha(response, wall_ms, CONFIG["masha"])


def test_masha_cannot_pass_with_only_two_hotels_or_no_room_qualification():
    response = search_response()
    response["results"].pop()
    with pytest.raises(CheckFailure, match="2 group=all"):
        validate_masha(response, 1000, CONFIG["masha"])
    response = search_response()
    for card in response["results"]:
        card["room_recommendation"] = "Уточните пожелания"
    with pytest.raises(CheckFailure, match="facade/floor caveat"):
        validate_masha(response, 1000, CONFIG["masha"])


def test_persona_unknown_cards_are_not_meaningful_or_all():
    response = search_response()
    spec = {"id": "test", "text": "Тест", "expected_characteristic_ids": ["quiet_night"], "minimum_meaningful_results": 3}
    for hit in response["results"]:
        hit["group"] = "unknown"
        for verdict in hit["verdicts"]:
            verdict.update(status="unknown", confidence=0, evidence_refs=[])
    with pytest.raises(CheckFailure, match="0 meaningful"):
        validate_persona(response, spec)
    response["results"][0]["group"] = "all"
    with pytest.raises(CheckFailure, match="unknown/rejected"):
        validate_persona(response, spec)


def test_masha_room_evidence_cannot_combine_opposite_facades():
    hit = {"hotel": {"id": "test"}, "verdicts": [
        {"required": True, "characteristic_id": "quiet_night", "fact_ids": ["q"], "evidence_refs": ["eq"]},
        {"required": True, "characteristic_id": "sunset_view", "fact_ids": ["s"], "evidence_refs": ["es"]}]}
    detail = {"facts": [
        {"id": "q", "characteristic_id": "quiet_night", "scope": {"facade": "E"}, "evidence": [{"id": "eq"}]},
        {"id": "s", "characteristic_id": "sunset_view", "scope": {"facade": "W"}, "evidence": [{"id": "es"}]}]}
    with pytest.raises(CheckFailure, match="incompatible required room scopes"):
        validate_hit_scope(hit, detail)
    detail["facts"][0]["scope"] = {"facade": "W", "floor_max": 3}
    detail["facts"][1]["scope"] = {"facade": "W", "floor_min": 5}
    with pytest.raises(CheckFailure, match="incompatible required room scopes"):
        validate_hit_scope(hit, detail)


def family_guard_response():
    result = {"parsed": {"must": [
        {"characteristic_id": "beach_walk", "field": "minutes", "op": "lte", "value": 15, "no_stairs": True, "no_crossings": True},
        {"characteristic_id": "family_nearby", "field": "playground_distance_m", "op": "lte", "value": 500, "label": "Детская площадка ≤ 500 м"}], "unmapped": []},
        "results": [{"hotel": {"id": FAMILY_GUARD_HOTEL_ID, "name": "Brevis apartments"}, "group": "unknown", "summary": "Без лестниц не подтверждено",
                     "verdicts": [{"characteristic_id": "beach_walk", "required": True, "status": "unknown", "confidence": .66,
                                   "text": "Не подтверждено: на маршруте есть лестницы", "fact_ids": ["real-route"], "evidence_refs": ["ev-route"]}]}]}
    detail = {"hotel": {"id": FAMILY_GUARD_HOTEL_ID}, "facts": [
        {"id": "real-route", "characteristic_id": "beach_walk", "value": {"minutes": 8, "has_steps": True}, "synthetic": False,
         "supporting_claim_ids": ["geo-route"], "evidence": [{"id": "ev-route"}]}],
        "claims": [{"id": "geo-route", "source": "geo_engine", "synthetic": False}]}
    return result, detail


def test_family_known_stairs_rejection_is_an_acceptance_requirement():
    result, detail = family_guard_response()
    assert validate_family_route_guard(result, detail)["route_status"] == "unknown"
    result["results"][0]["verdicts"][0]["status"] = "no"
    assert validate_family_route_guard(result, detail)["route_status"] == "no"


@pytest.mark.parametrize("defect", ["duplicate", "no_stairs", "no_crossings", "nearby", "unmapped", "all", "partial", "synthetic", "missing_stairs", "evidence", "evidence_reference"])
def test_family_guard_rejects_parser_ranking_or_provenance_regressions(defect):
    result, detail = family_guard_response()
    if defect == "duplicate":
        result["parsed"]["must"].append(result["parsed"]["must"][0].copy())
    elif defect in {"no_stairs", "no_crossings"}:
        result["parsed"]["must"][0][defect] = False
    elif defect == "nearby":
        result["parsed"]["must"][1]["field"] = "available"
    elif defect == "unmapped":
        result["parsed"]["unmapped"] = ["рядом"]
    elif defect == "all":
        result["results"][0]["group"] = "all"
    elif defect == "partial":
        result["results"][0]["verdicts"][0]["status"] = "partial"
    elif defect == "synthetic":
        detail["facts"][0]["synthetic"] = True
    elif defect == "missing_stairs":
        del detail["facts"][0]["value"]["has_steps"]
    elif defect == "evidence_reference":
        result["results"][0]["verdicts"][0]["evidence_refs"] = ["invented-reference"]
    else:
        result["results"][0]["verdicts"][0]["fact_ids"] = []
    with pytest.raises(CheckFailure):
        validate_family_route_guard(result, detail)


def synthetic_detail():
    expected = CONFIG["synthetic_hotels"][0]
    fixture = next(json.loads(line) for line in (Path(__file__).resolve().parents[1] / "data/seed/demo_hotelier_claims.jsonl").read_text().splitlines()
                   if json.loads(line)["hotel_id"] == expected["hotel_id"])
    return {"hotel": {"id": expected["hotel_id"]}, "facts": [
        {"id": "fixture-fact", "characteristic_id": "quiet_night", "scope": fixture["scope"], "value": fixture["value"], "synthetic": True,
         "confidence": .85, "supporting_claim_ids": [fixture["id"]]},
        {"id": "native-fact", "characteristic_id": "quiet_night", "scope": {"type": "facade", "facade": expected["facade"]},
         "synthetic": False, "confidence": .38, "supporting_claim_ids": ["geo-original"]}],
        "claims": [fixture, {"id": "geo-original", "source": "geo_engine", "synthetic": False, "confidence": .38}]}


@pytest.mark.parametrize("defect", ["synthetic_flag", "kind", "scope", "real_confidence"])
def test_fixture_provenance_and_real_noise_are_not_optional(defect):
    detail = synthetic_detail()
    validate_synthetic_detail(detail, CONFIG["synthetic_hotels"][0])
    if defect == "synthetic_flag":
        detail["claims"][0]["synthetic"] = False
    elif defect == "kind":
        detail["claims"][0]["evidence"][0]["kind"] = "hotelier"
    elif defect == "scope":
        detail["claims"][0]["scope"] = {**detail["claims"][0]["scope"], "facade": "E"}
    else:
        detail["facts"][1]["confidence"] = .85
    with pytest.raises(CheckFailure):
        validate_synthetic_detail(detail, CONFIG["synthetic_hotels"][0])


def test_geo_without_route_or_horizon_cannot_pass():
    geo = {"hotel_id": "hero", "artifact": "/data/geo/hero.json", "calculation_version": "test", "computed_at": "2026-10-26T00:00:00Z",
           "inputs": {"/data/input": "sha"}, "limitations": ["test"], "facades": [{"code": "W", "azimuth_deg": 270, "geometry": {"coordinates": [[0, 0], [1, 1]]}}],
           "horizon": [{"azimuth_deg": 270, "terrain_deg": 1, "buildings_deg": 2}],
           "route": {"type": "FeatureCollection", "features": [{"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}]},
           "route_details": {"available": True, "minutes": 7, "distance_m": 400}, "sun_paths": [{"month": 9, "points": [{"azimuth_deg": 270}]}],
           "monte_carlo": {"iterations": 64}}
    validate_geo(geo, "hero")
    for key in ("horizon", "route"):
        bad = copy.deepcopy(geo)
        bad[key] = [] if key == "horizon" else {}
        with pytest.raises(CheckFailure):
            validate_geo(bad, "hero")


def test_artifact_html_fallback_is_not_evidence_and_timeout_is_reported():
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="SPA", headers={"content-type": "text/html"}))) as client:
        with pytest.raises(CheckFailure, match="SPA fallback"):
            Runner(client, CONFIG).artifact("/data/missing.json")

    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(timeout)) as client:
        runner = Runner(client, CONFIG)
        runner.check("timeout", lambda: runner.request("/health"))
        assert runner.checks == [{"id": "timeout", "passed": False, "error": "synthetic timeout"}]
