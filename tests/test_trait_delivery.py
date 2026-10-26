"""The shipped optional ledger and geometry updates must preserve evidence meaning."""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from backend.agents.freshness import simulate_change
from backend.agents.resolver import resolve_hotel
from backend.contracts import Claim, ToolCall, ToolContext
from backend.contracts.validation import validate_value
from backend.tests.test_api_integration import ApiRepo
from backend.tests.test_freshness import observation, write_result
from backend.tools.registry import Registry
from geo.traits import METHODS

ROOT = Path(__file__).resolve().parents[1]


def test_delivered_trait_ledger_is_typed_unique_and_matches_manifest():
    path = ROOT / 'data/seed/geo_trait_claims.jsonl.gz'
    manifest = json.loads(path.with_name('geo_trait_manifest.json').read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest['ledger']['sha256']
    hotels = {row['id'] for row in json.loads((ROOT/'data/seed/hotels.json').read_text())}
    ids, counts, solar_hotels = set(), Counter(), set()
    with gzip.open(path, 'rt') as stream:
        for line in stream:
            claim = Claim.model_validate_json(line)
            assert claim.id not in ids and claim.hotel_id in hotels
            ids.add(claim.id)
            counts[claim.characteristic_id] += 1
            assert claim.method == METHODS[claim.characteristic_id]
            if claim.characteristic_id in {'morning_sun', 'evening_heat'}:
                solar_hotels.add(claim.hotel_id)
                assert claim.confidence <= .54
                association = claim.evidence[0].inputs['building_association']
                if 'conditional' in str(association):
                    assert any('контур' in note and 'не подтвержден' in note for note in claim.evidence[0].limitations)
    assert counts == Counter({cid: row['claims'] for cid, row in manifest['counts'].items()})
    assert len(ids) == 30460 and len(solar_hotels) == 199


def dependent(id, *, artifact='/data/geo/test-hotel.json'):
    claim = observation(id, 1, 8, True)
    return claim.model_copy(update={'characteristic_id': 'morning_sun', 'method': METHODS['morning_sun'],
                                   'evidence': [claim.evidence[0].model_copy(update={'artifact': artifact})]})


def test_new_building_retires_solar_values_from_old_geometry(tmp_path):
    repo = ApiRepo()
    repo.add_claim(observation('sea-before', 1, 8, True))
    repo.add_claim(dependent('sun-before'))
    resolve_hotel('test-hotel', repo)
    result = write_result(tmp_path/'data', [observation('sea-after', 1, 8, False)])
    with patch('geo.service.compute_hotel', return_value=result):
        change = simulate_change(repo, 'test-hotel', data_dir=tmp_path/'data', runtime=tmp_path/'runtime')
    solar = repo.find('facts', characteristic_id='morning_sun')
    assert solar and all(row['status'] == 'unknown' and row['confidence'] == 0 and row['value']['available'] is None for row in solar)
    retirements = [row for row in repo.find('claims', characteristic_id='morning_sun') if row['method'] == 'geometry_dependency_invalidation_v1']
    assert len(retirements) == 1 and retirements[0]['supersedes_ids'] == ['sun-before']
    assert retirements[0]['synthetic'] and retirements[0]['confidence'] == 0
    validate_value(retirements[0]['value'], repo.get('characteristics', 'morning_sun')['value_schema'])
    assert repo.get('claims', 'sun-before')['value']['available'] is True
    assert any(fact.characteristic_id == 'morning_sun' and fact.value['available'] is None for fact in change.after)


def test_precomputed_changed_variant_survives_copy_to_runtime(tmp_path):
    repo = ApiRepo()
    repo.add_claim(observation('sea-before', 1, 8, True))
    result = write_result(tmp_path/'data', [observation('sea-after', 1, 8, False)])
    repo.add_claim(dependent('sun-already-changed', artifact=result['geo']['artifact']))
    with patch('geo.service.compute_hotel', return_value=result):
        simulate_change(repo, 'test-hotel', data_dir=tmp_path/'data', runtime=tmp_path/'runtime')
    solar = repo.find('facts', characteristic_id='morning_sun')
    assert solar and solar[0]['status'] == 'computed' and solar[0]['value']['available'] is True


def test_trait_registry_uses_authoritative_change_artifact(tmp_path):
    repo = ApiRepo()
    reference = '/data/geo/test-hotel-change-current.json'
    repo.insert('change_events', {'id': 'synthetic', 'affected_hotel_ids': ['test-hotel'],
                                 'payload': {'geo_artifact': reference}, 'detected_at': '2026-10-26T00:00:00Z'})
    registry = Registry(repo, data_dir=tmp_path/'data')
    with patch('geo.traits.compute_trait', return_value={'claims': [], 'warnings': ['unknown'], 'artifacts': {}}) as compute:
        result = registry.execute(ToolContext(hotel_id='test-hotel', characteristic_id='morning_sun', run_id='test'),
                                  ToolCall(tool='geo_trait', params={'kind': 'morning_sun'}))
    assert not result.claims
    assert compute.call_args.kwargs['geo_artifact'] == reference


def test_trait_registry_does_not_reuse_old_method(tmp_path):
    repo = ApiRepo()
    repo.add_claim(dependent('old-method').model_copy(update={'method': 'astral_morning_horizon_proxy_v1'}))
    registry = Registry(repo, data_dir=tmp_path/'data')
    with patch('geo.traits.compute_trait', return_value={'claims': [], 'warnings': ['unknown'], 'artifacts': {}}) as compute:
        result = registry.execute(ToolContext(hotel_id='test-hotel', characteristic_id='morning_sun', run_id='test'),
                                  ToolCall(tool='geo_trait', params={'kind': 'morning_sun'}))
    assert compute.called and not result.claims
