"""Field-specific additions to existing CIDs, without changing their available."""
from __future__ import annotations

import gzip
import json

from geo.io import read_json
from pipeline.common import SEED, sha
from pipeline.tz_common import observation, spec_input, write_stage


def records(stage):
    with gzip.open(SEED / f'tz_{stage}_observations.json.gz', 'rt') as stream:
        return json.load(stream)['observations']


def run():
    routes = {r['hotel_id']: r for r in records('routes') if r['kind'] == 'airport_transfer'}
    arrival = {r['hotel_id']: r for r in records('environment') if r['kind'] == 'car_arrival'}
    climate = records('climate')
    rows = []
    for hotel in read_json(SEED / 'hotels.json'):
        route, access = routes[hotel['id']]['value'], arrival[hotel['id']]['value']
        value = {'walk_parking': route.get('parking_walk'), 'walk_ev': route.get('ev_walk'), 'mapped_road_access': access['available'],
                 'road_distance_m': access['road_distance_m'], 'arrival_anchor': access['arrival_anchor']}
        rows.append(observation(hotel['id'], 'car_access', value, method='tz_supplement:car_access@1', supplementary_observation=True,
                                source_ids=['tz_osm_geofabrik', 'osm_geofabrik', 'copernicus_dem']))
        parking = access.get('private_parking')
        rows.append(observation(hotel['id'], 'hotel_parking', {'available': True if parking is True else None,
                                'private_parking': True if parking is True else None, 'private_parking_osm_ids': access.get('private_parking_osm_ids', []),
                                'fee_rub_per_day': None, 'reservation_required': None, 'ev_charging': None,
                                'unknown_reason': None if parking is True else 'No exact positive OSM hotel-parking relation; ask owner, absence is unknown'},
                                source_ids=['tz_osm_geofabrik', 'osm_geofabrik'], ttl_days=180,
                                limitations=['Only exact positive OSM linkage; parking fees, reservation and EV equipment require owner evidence']))
    for row in climate:
        value = row['value']
        fields = {key: value.get(key) for key in ('sunny_days', 'rainy_days', 'wet_month_proxy', 'complete_sun_years', 'complete_rain_years',
                                                'daily_sample_ref', 'daily_sample_days', 'annual_samples', 'period', 'zone')}
        rows.append(observation(row['hotel_id'], 'climate', fields, method='tz_supplement:climate@1', supplementary_observation=True,
                                source=row['source'], source_ids=row['source_ids'], scope=row['scope'], observed_at=row['observed_at'],
                                ttl_days=365, confidence=row['confidence'], related_artifacts=row.get('related_artifacts', []), input_sha256=row.get('input_sha256', {})))
    names = ['hotels.json', 'tz_environment_observations.json.gz', 'tz_routes_observations.json.gz', 'tz_climate_observations.json.gz']
    inputs = {'/data/seed/'+name: sha(SEED / name) for name in names}
    inputs.update(spec_input(kind) for kind in ('car_access', 'hotel_parking', 'climate'))
    sources = {}
    for stage in ('environment', 'routes', 'climate'):
        for record in read_json(SEED / f'tz_{stage}_sources.json'):
            sources[record['id']] = record
    return write_stage('supplements', rows, list(sources.values()), inputs=inputs, limitations=[
        'Supplemental fields retain separate source/confidence; baseline available and straight-line parking distances are not overwritten',
        'Walking results use the directed graph, not minutes inferred from direct distance',
        'Climate uses 2 shared zonal daily samples, not independent observations for each hotel; missing days are not zero'])


if __name__ == '__main__':
    run()
