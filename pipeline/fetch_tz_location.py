"""Administrative containment and real directed routes to explicit OSM places."""
from __future__ import annotations

import gzip
import json
from collections import Counter

import yaml
from shapely.geometry import Point, shape

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import load_features
from geo.tz_routes import NearbyRoutes
from geo.walk import WalkGraph
from pipeline.common import CONFIG, SEED, dump, gzip_json, sha, source
from pipeline.fetch_tz_places import PUBLIC_CENTER_IDS
from pipeline.tz_common import baseline_sources, observation, spec_input, write_stage

CENTERS = {'osm-relation-1116490': 'osm-node-34043670', 'osm-relation-907728': 'osm-node-10919477142',
           'osm-relation-5650614': 'osm-node-207721468', 'osm-relation-11865346': 'osm-node-8097262896'}
METRO_URL = 'https://rosstat.gov.ru/storage/mediabank/Ejegodnik_2025.pdf'
CENTRAL_DISTRICT = 'osm-relation-1116490'
PUBLIC_REFERENCE_ROLES = {
    'osm-node-3771836366': 'Ориентир на площади Флага у городской администрации; основная публичная точка центра',
    'osm-node-2493934195': 'Публичный арт-ориентир на прогулочной Навагинской; диагностическая альтернатива',
    'osm-node-2493935121': 'Публичный арт-ориентир у морвокзала; диагностическая альтернатива',
}


def containing_district(xy, districts):
    matches = [d for d in districts if d['shape'].covers(Point(xy))]
    return matches[0] if len(matches) == 1 else None


def run():
    data = read_json(SEED / 'tz_place_hotel_tags.json')
    places = {p['osm_id']: p for p in data['places']}
    public_references = {p['osm_id']: p for p in data['public_center_references']}
    square = shape(next(p['geometry'] for p in data['public_center_areas'] if p['osm_id'] == 'osm-relation-416831'))
    civic = public_references[PUBLIC_CENTER_IDS[0]]
    if not square.covers(Point(civic['lon'], civic['lat'])):
        raise ValueError('Declared civic reference is outside the exact Flag Square polygon')
    selected = {**places, **public_references}
    center_ids = {**CENTERS, CENTRAL_DISTRICT: PUBLIC_CENTER_IDS[0]}
    catalog = {'policy': 'Central Sochi: fixed public civic landmark on Flag Square, selected for meaning before routing diagnostics; other districts: named OSM place node. Declared reference destinations, not universal geographic centres',
               'snap_limit_m': 120, 'route_search_minutes': 90,
               'centers': [{'district_id': district, **selected[oid]} for district, oid in center_ids.items()],
               'original_place_centers': [{'district_id': district, **places[oid]} for district, oid in CENTERS.items()],
               'central_reference_candidates': [{**public_references[oid], 'selection_reason': PUBLIC_REFERENCE_ROLES[oid],
                                                 'production_reference': oid == PUBLIC_CENTER_IDS[0]} for oid in PUBLIC_CENTER_IDS]}
    catalog_path = SEED.parent / 'landmarks/location_centers.yaml'
    catalog_path.write_text(yaml.safe_dump(catalog, allow_unicode=True, sort_keys=False))
    districts = load_features(SEED / 'admin_districts.geojson')
    planner = NearbyRoutes(WalkGraph(read_json(SEED / 'walk_graph.json')), load_features(SEED / 'roads.geojson'),
                           load_features(SEED / 'railways.geojson'), snap_limit_m=120)
    with gzip.open(SEED / 'tz_routes_observations.json.gz', 'rt') as stream:
        prior = {r['hotel_id']: r['value'] for r in json.load(stream)['observations'] if r['kind'] == 'airport_transfer'}
    rows, diagnostic_rows = [], []
    def routed(origin, center):
        point = Point(TO_UTM.transform(center['lon'], center['lat']))
        return planner.nearest(origin, [{'id': center['osm_id'], 'shape': point, 'properties': center['tags']}], kind=None, max_minutes=90)

    for hotel in read_json(SEED / 'hotels.json'):
        xy = TO_UTM.transform(hotel['lon'], hotel['lat'])
        district = containing_district(xy, districts)
        center = selected[center_ids[district['id']]] if district and district['id'] in center_ids else None
        origin = planner.from_hotel(xy)
        route = routed(origin, center) if center else None
        original_center = places[CENTERS[district['id']]] if district and district['id'] in CENTERS else None
        diagnostic_rows.append({'hotel_id': hotel['id'], 'district_id': district['id'] if district else None,
                                'original_place_route': routed(origin, original_center) if original_center else None,
                                'public_reference_routes': {oid: routed(origin, public_references[oid]) for oid in PUBLIC_CENTER_IDS}
                                if district and district['id'] == CENTRAL_DISTRICT else {}})
        previous = prior.get(hotel['id'], {})
        value = {'available': True if district else None, 'in_central_sochi': district['id'] == 'osm-relation-1116490' if district else None,
                 'district_id': district['id'] if district else None, 'district_name': district['properties']['display_name'] if district else None,
                 'zone_center': {'id': center['osm_id'], 'name': 'Площадь Флага · Олимпийские кольца' if center['osm_id'] == PUBLIC_CENTER_IDS[0] else center['tags'].get('name'),
                                 'osm_name': center['tags'].get('name'), 'lon': center['lon'], 'lat': center['lat'],
                                 'selection': catalog['policy']} if center else None,
                 'center_route': route, 'center_unknown_reason': None if route else 'No directed graph route within 90 minutes with both snaps <=120m; missing path is unknown',
                 'station_route': previous.get('nearest_station'), 'bus_stop_route': previous.get('nearest_bus_stop'),
                 'metro': {'available': False, 'observed_at': '2024-12-31', 'source_url': METRO_URL,
                           'basis': 'Rosstat Russian Statistical Yearbook 2025 table 20.13 lists seven metro cities at end-2024, excluding Sochi; not a 2026 census'},
                 'calculation_parameters': {}, 'route_semantics': 'Graph distance/minutes/ascent/crossings only; <=120m unmodelled snap connectors excluded, no straight-line minutes'}
        rows.append(observation(hotel['id'], 'location_access', value, source_ids=['tz_osm_geofabrik', 'osm_admin_districts', 'osm_geofabrik', 'copernicus_dem', 'tz_rosstat_metro'],
                                observed_at=CONFIG['osm']['snapshot'], ttl_days=90, confidence=.6))
    sources = baseline_sources()
    admin = read_json(SEED / 'manifest.json')['supplemental_sources'][0]
    metro = source('tz_rosstat_metro', 'Росстат, Российский статистический ежегодник 2025, таблица 20.13', METRO_URL,
                   'Rosstat open-data terms, attribution required', 'Росстат, Российский статистический ежегодник 2025, табл.20.13',
                   '2024-12-31T00:00:00Z', '2026-10-27T19:30:00Z', 'Dated official statistical facts; no PDF reproduction. End-2024 coverage. Rosstat reuse terms: https://rosstat.gov.ru/opendata/ and https://rosstat.gov.ru/storage/2018/08-23/6RswQtD5/usl-od.pdf')
    names = ['hotels.json', 'admin_districts.geojson.gz', 'walk_graph.json.gz', 'roads.geojson.gz', 'railways.geojson.gz', 'tz_place_hotel_tags.json', 'tz_routes_observations.json.gz']
    inputs = {'/data/seed/'+name: sha(SEED / name) for name in names}
    inputs['/data/landmarks/location_centers.yaml'] = sha(catalog_path)
    inputs.update([spec_input('location_access')])
    central_rows = [row for row in diagnostic_rows if row['district_id'] == CENTRAL_DISTRICT]
    summary = {'central_district_hotels': len(central_rows),
               'original_place_routes': sum(row['original_place_route'] is not None for row in central_rows),
               'public_reference_routes': {oid: sum(row['public_reference_routes'][oid] is not None for row in central_rows) for oid in PUBLIC_CENTER_IDS},
               'selected_reference_id': PUBLIC_CENTER_IDS[0]}
    destinations = {}
    for oid in [CENTERS[CENTRAL_DISTRICT], *PUBLIC_CENTER_IDS]:
        point = selected[oid]
        node, snap = planner.snap(TO_UTM.transform(point['lon'], point['lat']))
        destinations[oid] = {'snap_node_id': node, 'snap_m': round(snap, 2) if snap is not None else None,
                             'out_degree': len(planner.graph.adj.get(node, [])), 'in_degree': len(planner.reverse.get(node, []))}
    diagnostic_path = SEED / 'tz_location_diagnostics.json.gz'
    diagnostic_inputs = {reference: value for reference, value in inputs.items() if not reference.endswith('tz_routes_observations.json.gz')}
    gzip_json(diagnostic_path, {'method': 'tz_location_diagnostics@1', 'inputs': diagnostic_inputs, 'summary': summary,
                               'destinations': destinations, 'district_counts': dict(Counter(row['district_id'] or 'unknown' for row in diagnostic_rows)),
                               'hotels': diagnostic_rows, 'parameters': {'snap_limit_m': 120, 'route_search_minutes': 90},
                               'limitations': ['Original place-node routing retained for all hotels', 'Public references predeclared for civic square, pedestrian street and marina; route counts never select the destination',
                                               'Only directed graph minutes; snap connectors remain unmodelled, missing route is unknown']})
    inputs['/data/seed/'+diagnostic_path.name] = sha(diagnostic_path)
    manifest = write_stage('location', rows, [*read_json(SEED / 'tz_osm_sources.json'), sources['osm_geofabrik'], sources['copernicus_dem'], admin, metro], inputs=inputs,
                       limitations=['Administrative centre criterion is exact Central Sochi polygon; the walking destination is a separately declared public civic reference on Flag Square',
                                    'Graphs cover two separate regions; Khosta reference point may be outside the graph, yielding unknown',
                                    'Metro absence evidence is dated end-2024; later registry requires ESIA and was not accessed'])
    manifest['center_diagnostics'] = summary
    dump(SEED / 'tz_location_manifest.json', manifest)
    print(summary, flush=True)
    return manifest


if __name__ == '__main__':
    run()
