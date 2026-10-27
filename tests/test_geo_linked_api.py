"""Map transport exposes linked points without shipping private region indexes."""
import json
from unittest.mock import patch

from backend.api import catalog
from backend.tests.test_api_integration import ApiRepo


def test_geo_uses_current_facts_and_excludes_private_indexes(tmp_path, monkeypatch):
    repo = ApiRepo()
    hotel_id = 'test-hotel'
    directory = tmp_path / 'geo'
    directory.mkdir()
    (directory / (hotel_id + '.json')).write_text(json.dumps({'geo': {
        'hotel_id': hotel_id, 'center': [39.7, 43.5], 'facades': [],
        'building_index': {'large': [1, 2, 3]}, 'spatial_index': [42], 'claims': [42],
    }}))
    monkeypatch.setattr(catalog, 'DATA', tmp_path)
    monkeypatch.setattr(catalog, 'runtime_dir', lambda: tmp_path / 'runtime')
    facts = [{'id': 'current', 'hotel_id': hotel_id}]
    with patch('backend.nearby.linked_places', return_value={'type': 'FeatureCollection', 'features': []}) as points:
        result = catalog.read_geo(hotel_id, repo, fact_rows=facts)
    assert result['nearby'] == {'type': 'FeatureCollection', 'features': []}
    assert not {'building_index', 'spatial_index', 'claims'} & result.keys()
    assert points.call_args.args == (facts, result, tmp_path)


def test_geo_reads_only_requested_hotel_facts(tmp_path, monkeypatch):
    repo = ApiRepo()
    directory = tmp_path / 'geo'
    directory.mkdir()
    (directory / 'test-hotel.json').write_text(json.dumps({'hotel_id': 'test-hotel', 'facades': []}))
    repo.insert('facts', {'id': 'ours', 'hotel_id': 'test-hotel'})
    repo.insert('facts', {'id': 'other', 'hotel_id': 'another-hotel'})
    monkeypatch.setattr(catalog, 'DATA', tmp_path)
    monkeypatch.setattr(catalog, 'runtime_dir', lambda: tmp_path / 'runtime')
    with patch('backend.nearby.linked_places', return_value={'type': 'FeatureCollection', 'features': []}) as points:
        catalog.read_geo('test-hotel', repo)
    assert [fact['id'] for fact in points.call_args.args[0]] == ['ours']


def test_geo_passes_selected_room_and_durable_changes_to_saved_landmark_layer(tmp_path, monkeypatch):
    repo = ApiRepo()
    directory = tmp_path / 'geo'
    directory.mkdir()
    (directory / 'test-hotel.json').write_text(json.dumps({'hotel_id': 'test-hotel', 'facades': []}))
    monkeypatch.setattr(catalog, 'DATA', tmp_path)
    monkeypatch.setattr(catalog, 'runtime_dir', lambda: tmp_path / 'runtime')
    changes = [{'id': 'event', 'kind': 'osm_building_new', 'affected_hotel_ids': ['test-hotel'],
                'payload': {'geo_artifact': '/data/geo/test-hotel.json'}}]
    layer = {'landmark_rays': {'type': 'FeatureCollection', 'features': []},
             'landmark_metadata': [{'landmark_id': 'fisht', 'height_available': False}]}
    with patch('geo.tz_overlay.landmark_overlay', return_value=layer) as overlay:
        result = catalog.read_geo('test-hotel', repo, month=10, facade='W', floor=5, events=changes)
    assert overlay.call_args.args == ('test-hotel', 10, 'W', 5)
    assert overlay.call_args.kwargs == {'data_dir': tmp_path, 'events': changes}
    assert result['landmark_metadata'][0]['height_available'] is False
