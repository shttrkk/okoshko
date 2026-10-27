"""Refresh only TZ ledgers after catalog promotion; preserve baseline data bytes."""
from __future__ import annotations

import gzip
import json

from geo.io import read_json
from pipeline.common import ROOT, SEED, dump, sha
from pipeline.tz_common import observation, spec_input, write_stage


def refresh_inputs(inputs):
    updated = {}
    for reference in inputs:
        reference = reference.replace('/characteristics/tz_draft/', '/characteristics/')
        path = ROOT / reference.removeprefix('/')
        if not path.is_file():
            raise ValueError(f'Missing promoted input: {reference}')
        updated[reference] = sha(path)
    return updated


def historic_designation(value):
    for record in value.get('records', []):
        tags = record.get('tags', {})
        if tags.get('heritage') not in {None, '', 'no', '0'} or tags.get('historic') in {'yes', 'building', 'manor', 'castle', 'palace'}:
            return True
    if any(item.get('properties', {}).get('heritage') for item in value.get('wikidata', [])):
        return True
    return None


def run():
    for stage in ('environment', 'routes', 'climate', 'history', 'landmarks', 'supplements', 'location', 'metadata'):
        path = SEED / f'tz_{stage}_observations.json.gz'
        if not path.exists():
            continue
        with gzip.open(path, 'rt') as stream:
            content = json.load(stream)
        old_manifest = read_json(SEED / f'tz_{stage}_manifest.json')
        inputs = refresh_inputs(old_manifest['inputs'])
        rows = []
        for row in content['observations']:
            row['input_sha256'] = refresh_inputs(row.get('input_sha256', {}))
            value = row.get('value')
            if value is None:
                value = {'available': None, 'unknown_reason': 'Нет подтверждённых данных или достижимого пути в исходном графе; отсутствие записи не означает отсутствия объекта'}
                row['value'] = value
                row['emit_claim'] = True
            if row['kind'] == 'family_routes':
                row['kind'] = 'family_nearby'
                value = {'walk_'+key: value.get(key) for key in ('playground', 'hospital', 'children_hospital', 'promenade')}
                row['value'] = value
            if value is not None:
                if row['kind'] in {'building_history', 'family_nearby', 'car_access', 'climate'}:
                    row['supplementary_observation'] = True
                    row['method'] = 'tz_supplement:'+row['kind']+'@1'
                if row['kind'] == 'landmark_view':
                    row['scope']['landmark_id'] = value['landmark_id']
                elif row['kind'] == 'mobile_masts':
                    value['quality_unknown'] = True
                elif row['kind'] == 'building_history':
                    value['history_information'] = value.pop('available', None) or value.get('history_information')
                    value['historic_designation'] = historic_designation(value)
                    if stage == 'environment':
                        row['emit_claim'] = True
                    if value['history_information'] is None:
                        value['unknown_reason'] = 'Нет исторических сведений с точной связью OSM/контура/Wikidata; название и близость не используются'
                elif value.get('available') is None and 'unknown_reason' not in value and row['kind'] not in {'family_nearby', 'car_access', 'climate'}:
                    value['unknown_reason'] = {'construction_nearby': 'Нет недавнего явного survey/check_date:construction близкого фасаду объекта; архивная отметка не доказывает текущие работы',
                                               'mobile_masts': 'Нет отмеченной мачты в радиусе; качество связи и отсутствие покрытия не установлены',
                                               'dog_walk': 'Нет подтверждённого близкого маршрута к площадке выгула; обычный парк не заменяет её',
                                               'night_open': 'Нет подтверждённого близкого ночного магазина/еды по распознанным часам и графу; часы unknown не означают closed',
                                               'airport_transfer': 'Не установлена картографическая цепочка с 0–1 пересадкой; см. primary_unknown_reason',
                                               'car_arrival': 'Нет точно связанного контура/входа для оценки подъезда'}.get(row['kind'], 'Нет подтверждённого значения по исходным открытым данным')
                inputs.update([spec_input('family_nearby' if row['kind'] == 'family_routes' else row['kind'])])
            row['id'] = observation(row['hotel_id'], row['kind'], value, scope=row.get('scope'))['id']
            rows.append(row)
        manifest = write_stage(stage, rows, read_json(SEED / f'tz_{stage}_sources.json'), inputs=inputs,
                               limitations=content['limitations'])
        for key in ('landmarks', 'rejected', 'excluded_candidates', 'downloads', 'errors', 'exact_qids', 'center_diagnostics'):
            if key in old_manifest:
                manifest[key] = old_manifest[key]
        dump(SEED / f'tz_{stage}_manifest.json', manifest)


if __name__ == '__main__':
    run()
