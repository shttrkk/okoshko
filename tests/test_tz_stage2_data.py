"""Known answers for exact metadata, partial observations and overlay invalidation."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shapely.geometry import Point, box, shape

from backend.contracts import ToolContext
from backend.tools.tz_observation import active_landmark_overlay, prepared_observations
from geo.tz_metadata import commons_features, explicit_osm_features, mobile_sample
from geo.tz_overlay import landmark_overlay
from pipeline.fetch_tz_catalog import historic_designation
from pipeline.fetch_tz_location import containing_district


def tile(kind='mobile'):
    return {'kind': kind, 'tile': 'POLYGON((0 0,1 0,1 1,0 1,0 0))', 'avg_d_kbps': 25000, 'avg_u_kbps': 5000,
            'avg_lat_ms': 20, 'tests': 4, 'devices': 2, 'quarter': '2026Q3', 'quadkey': 'fixture'}


def test_mobile_exact_containment_does_not_substitute_wifi_or_nearby_tile():
    sample = mobile_sample(.5, .5, [tile()])
    assert sample['download_mbps'] == 25 and sample['hotel_wifi'] is False and sample['radio_generation'] is None
    assert mobile_sample(.5, .5, [tile('fixed')])['available'] is None
    assert mobile_sample(1.01, .5, [tile()])['download_mbps'] is None


def test_mobile_ambiguous_boundary_is_unknown_not_guessed_average():
    assert mobile_sample(.5, .5, [tile(), tile()])['available'] is None


def test_osm_name_levels_or_hotel_category_do_not_infer_balcony_pool():
    value = explicit_osm_features([{'osm_id': 'h', 'tags': {'name': 'Hotel Pool Balcony', 'building:levels': '20', 'tourism': 'hotel'}}])
    assert value['balcony'] is None and value['pool'] is None and value['terrace'] is None


def test_osm_explicit_yes_no_and_conflicts_remain_distinct():
    rows = [{'osm_id': 'h', 'tags': {'balcony': 'yes', 'swimming_pool': 'no'}}, {'osm_id': 'b', 'tags': {'balcony': 'no'}}]
    result = explicit_osm_features(rows)
    assert result['balcony'] is None and result['pool'] is False
    assert len(result['evidence_records']) == 3


def commons(description='Hotel terrace and swimming pool', licensed=True):
    ext = {'Artist': {'value': 'Author'}, 'ImageDescription': {'value': description}, 'DateTimeOriginal': {'value': '2018-06-01'}}
    if licensed:
        ext.update(LicenseUrl={'value': 'https://creativecommons.org/licenses/by/4.0'}, LicenseShortName={'value': 'CC BY 4.0'})
    page = {'pageid': 1, 'title': 'File:Hotel.jpg', 'imageinfo': [{'extmetadata': ext, 'timestamp': '2026-01-01T00:00:00Z', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Hotel.jpg'}]}
    media = {'statements': {'P180': [{'mainsnak': {'datavalue': {'value': {'id': 'Q1'}}}}]}}
    return page, media


def test_commons_name_or_proximity_is_not_exact_identity():
    assert commons_features(*commons(), {'Q2'}) == []
    assert {r['feature'] for r in commons_features(*commons(), {'Q1'})} == {'terrace', 'pool'}


def test_commons_missing_license_cannot_be_a_feature_source():
    assert commons_features(*commons(licensed=False), {'Q1'}) == []


def test_commons_negative_description_does_not_create_positive_feature():
    assert commons_features(*commons('Hotel without balcony'), {'Q1'}) == []


def test_commons_capture_and_upload_dates_are_not_confused():
    record = commons_features(*commons(), {'Q1'})[0]
    assert record['capture_date'] == '2018-06-01'
    assert record['upload_timestamp'] == '2026-01-01T00:00:00Z'


def test_history_date_or_architecture_information_does_not_make_old_building():
    assert historic_designation({'records': [{'tags': {'start_date': '1900', 'architect': 'A'}}]}) is None
    assert historic_designation({'records': [{'tags': {'heritage': '2'}}]}) is True


def test_ambiguous_district_boundary_does_not_select_first_polygon():
    district = {'shape': box(0, 0, 1, 1), 'id': 'one'}
    assert containing_district((.5, .5), [district])['id'] == 'one'
    assert containing_district((.5, .5), [district, district]) is None
    assert containing_district((2, 2), [district]) is None


def test_overlay_requires_matching_artifact_before_reusing_prepared_landmark(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    raw = seed / 'input.json'
    raw.write_text('{}')
    now = datetime.now(UTC)
    claim = {'id': 'fixture', 'hotel_id': 'h', 'characteristic_id': 'landmark_view', 'scope': {'type': 'hotel', 'landmark_id': 'target'},
             'value': {'available': True, 'landmark_id': 'target'}, 'source': 'fixture', 'method': 'fixture@1', 'confidence': .5,
             'observed_at': now.isoformat(), 'valid_until': (now+timedelta(days=1)).isoformat(),
             'evidence': [{'kind': 'calculation', 'label': 'fixture', 'source_ids': ['fixture'], 'artifact': '/data/seed/input.json', 'inputs': {'input_sha256': {'/data/seed/input.json': hashlib.sha256(raw.read_bytes()).hexdigest()}}}]}
    path = seed / 'tz_landmarks_claims.jsonl.gz'
    with gzip.open(path, 'wt') as stream:
        stream.write(json.dumps(claim)+'\n')
    context = ToolContext(hotel_id='h', characteristic_id='landmark_view', run_id='r')
    event = {'id': 'event', 'kind': 'osm_building_new', 'affected_hotel_ids': ['h'], 'payload': {'geo_artifact': '/data/runtime/geo/new.json'}}
    assert prepared_observations(tmp_path, context, {'kind': 'landmark_view'}).claims
    assert not prepared_observations(tmp_path, context, {'kind': 'landmark_view'}, events=[event]).claims
    assert active_landmark_overlay('other', [event]) is None
    assert active_landmark_overlay('h', [{**event, 'payload': {}}]) == '__missing_active_artifact__'
    assert prepared_observations(tmp_path, context, {'kind': 'landmark_view'}, events=[{**event, 'status': 'reverted'}]).claims


def test_map_uses_saved_rays_only_at_selected_floor_and_blocks_active_overlay(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    (seed / 'tz_landmarks_manifest.json').write_text(json.dumps({'landmarks': []}))
    with gzip.open(seed / 'tz_landmark_rays.json.gz', 'wt') as stream:
        json.dump({'inputs': {}, 'rays': {'h': [{'floor': 2, 'facade': 'S', 'start': [39, 43], 'target': [39.1, 43.1], 'available': False}]}}, stream)
    assert not landmark_overlay('h', data_dir=tmp_path)['landmark_rays']['features']
    ray = landmark_overlay('h', floor=2, data_dir=tmp_path)['landmark_rays']['features'][0]
    assert ray['geometry']['coordinates'] == [[39, 43], [39.1, 43.1]] and ray['properties']['available'] is False
    event = {'kind': 'osm_building_new', 'affected_hotel_ids': ['h']}
    assert not landmark_overlay('h', floor=2, data_dir=tmp_path, events=[event])['landmark_rays']['features']


def test_materialized_targets_have_identity_scopes_and_fisht_is_information_only():
    root = Path(__file__).parents[1]
    with gzip.open(root / 'data/seed/tz_landmarks_claims.jsonl.gz', 'rt') as stream:
        claims = [json.loads(line) for line in stream]
    assert all(c['scope'].get('landmark_id') == c['value']['landmark_id'] for c in claims)
    fisht = [c for c in claims if c['value']['landmark_id'] == 'fisht']
    assert len(fisht) == 250
    assert all(c['value']['available'] is None and c['value']['height_available'] is False and c['value']['unknown_reason'] for c in fisht)


def test_overlay_cache_detects_same_size_input_edit_and_replaced_rays(tmp_path):
    seed = tmp_path / 'seed'
    seed.mkdir()
    source = seed / 'input.json'
    source.write_text('{}')
    original = source.stat()
    (seed / 'tz_landmarks_manifest.json').write_text(json.dumps({'landmarks': []}))
    ray_path = seed / 'tz_landmark_rays.json.gz'
    saved = {'inputs': {'/data/seed/input.json': hashlib.sha256(source.read_bytes()).hexdigest()},
             'rays': {'h': [{'floor': 2, 'facade': 'S', 'start': [39, 43], 'target': [39.1, 43.1], 'available': True}]}}
    with gzip.open(ray_path, 'wt') as stream:
        json.dump(saved, stream)
    for _ in range(2):
        assert landmark_overlay('h', floor=2, data_dir=tmp_path)['landmark_rays']['features'][0]['properties']['available'] is True
    source.write_text('[]')
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert not landmark_overlay('h', floor=2, data_dir=tmp_path)['landmark_rays']['features']
    assert landmark_overlay('h', floor=2, data_dir=tmp_path)['warnings']
    source.write_text('{}')
    saved['rays']['h'][0]['available'] = False
    replacement = seed / 'replacement.gz'
    with gzip.open(replacement, 'wt') as stream:
        json.dump(saved, stream)
    replacement.replace(ray_path)
    assert landmark_overlay('h', floor=2, data_dir=tmp_path)['landmark_rays']['features'][0]['properties']['available'] is False


def test_civic_center_reference_is_predeclared_and_original_diagnostic_is_retained():
    seed = Path(__file__).parents[1] / 'data/seed'
    extracted = json.loads((seed / 'tz_place_hotel_tags.json').read_text())
    civic = next(row for row in extracted['public_center_references'] if row['osm_id'] == 'osm-node-3771836366')
    square = next(row for row in extracted['public_center_areas'] if row['osm_id'] == 'osm-relation-416831')
    assert shape(square['geometry']).covers(Point(civic['lon'], civic['lat']))
    with gzip.open(seed / 'tz_location_diagnostics.json.gz', 'rt') as stream:
        diagnostic = json.load(stream)
    assert diagnostic['parameters']['snap_limit_m'] == 120
    assert diagnostic['summary']['original_place_routes'] == 1
    assert diagnostic['summary']['selected_reference_id'] == civic['osm_id']
    assert len(diagnostic['summary']['public_reference_routes']) == 3
    assert diagnostic['summary']['public_reference_routes'][civic['osm_id']] < max(diagnostic['summary']['public_reference_routes'].values())
