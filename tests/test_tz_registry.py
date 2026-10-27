"""Prepared data are executable only through a bounded, read-only whitelist."""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError

from backend.contracts import CharacteristicSpec, ToolCall, ToolContext
from backend.tools.registry import Registry
from backend.tools.tz_observation import prepared_observations


def ledger(tmp_path, *, reference="/data/seed/input.json", params=None):
    seed = tmp_path / "seed"
    seed.mkdir()
    raw = seed / "input.json"
    raw.write_text('{"synthetic_fixture":true}')
    now = datetime.now(UTC)
    claim = {"id": "fixture", "hotel_id": "h", "characteristic_id": "airport_noise", "value": {"available": True, "calculation_parameters": params or {}},
             "source": "fixture", "method": "known_answer_fixture@1", "confidence": .5, "synthetic": True,
             "observed_at": now.isoformat(), "valid_until": (now+timedelta(days=1)).isoformat(),
             "evidence": [{"kind": "calculation", "label": "synthetic fixture", "source_ids": ["fixture"], "artifact": "/data/seed/input.json", "inputs": {"input_sha256": {reference: hashlib.sha256(raw.read_bytes()).hexdigest()}}}]}
    with gzip.open(seed / "tz_environment_claims.jsonl.gz", "wt") as stream:
        stream.write(json.dumps(claim)+"\n")
    return raw


def context():
    return ToolContext(hotel_id="h", characteristic_id="airport_noise", run_id="run")


def test_prepared_claim_keeps_provenance_and_synthetic_flag(tmp_path):
    ledger(tmp_path)
    result = prepared_observations(tmp_path, context(), {"kind": "airport_noise"})
    assert len(result.claims) == 1 and result.claims[0].synthetic is True
    assert result.claims[0].source == "fixture" and result.claims[0].method == "known_answer_fixture@1"


def test_modified_input_and_uncomputed_parameters_remain_unknown(tmp_path):
    raw = ledger(tmp_path, params={"nearby_m": 3000})
    assert not prepared_observations(tmp_path, context(), {"kind": "airport_noise", "nearby_m": 100}).claims
    raw.write_text('changed')
    result = prepared_observations(tmp_path, context(), {"kind": "airport_noise"})
    assert not result.claims and any("изменились" in warning for warning in result.warnings)


def test_traversal_in_untrusted_ledger_is_not_read(tmp_path):
    ledger(tmp_path, reference="/data/seed/../../secret")
    assert not prepared_observations(tmp_path, context(), {"kind": "airport_noise"}).claims


def test_missing_or_corrupt_ledger_does_not_invent_a_value(tmp_path):
    assert not prepared_observations(tmp_path, context(), {"kind": "airport_noise"}).claims
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed/tz_environment_claims.jsonl.gz").write_bytes(b'corrupt')
    assert not prepared_observations(tmp_path, context(), {"kind": "airport_noise"}).claims


def test_registry_rejects_llm_values_network_paths_and_unbounded_thresholds(tmp_path):
    registry = Registry(None, tmp_path)
    for params in [{"kind": "airport_noise", "value": True}, {"kind": "airport_noise", "url": "https://example.org"},
                   {"kind": "../../private"}, {"kind": "airport_noise", "nearby_m": -1}, {"kind": "landmark_view", "landmark_id": "../secret"}]:
        with pytest.raises(ValidationError):
            registry.validate(ToolCall(tool="prepared_observation", params=params))
    registry.validate(ToolCall(tool="prepared_observation", params={"kind": "airport_noise", "nearby_m": 3000}))


def test_landmark_viewshed_does_not_fall_back_to_sea_claims(tmp_path):
    result = Registry(None, tmp_path).execute(context(), ToolCall(tool="viewshed_facade", params={"target": "landmark", "landmark_id": "unknown_landmark"}))
    assert not result.claims


def test_all_new_yaml_use_registered_safe_methods():
    registry = Registry(None)
    new = ["airport_noise", "airport_transfer", "landmark_view", "night_open", "dog_walk", "veterinary_nearby", "pet_shop_nearby", "construction_nearby", "mobile_masts", "climate_days", "car_arrival"]
    for kind in new:
        spec = CharacteristicSpec.model_validate(yaml.safe_load((Path(__file__).parents[1] / "data/characteristics" / (kind+".yaml")).read_text()))
        assert spec.status == "active"
        for call in spec.methods:
            registry.validate(call)
