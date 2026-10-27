"""Read saved target coordinates and actual LOS checks for an optional map layer."""
from __future__ import annotations

import gzip
import hashlib
import json
import stat
from functools import lru_cache
from pathlib import Path

from geo.coordinates import TO_WGS


def file_identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Overlay inputs must be regular files')
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@lru_cache(maxsize=16)
def cached_document(path, identity, compressed=False):
    path = Path(path)
    content = gzip.decompress(path.read_bytes()) if compressed else path.read_bytes()
    if file_identity(path) != identity:
        raise ValueError('Overlay artifact changed while reading')
    return json.loads(content)


@lru_cache(maxsize=128)
def cached_digest(path, identity):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if file_identity(path) != identity:
        raise ValueError('Overlay input changed while hashing')
    return digest


def checked_document(path, compressed=False):
    return cached_document(str(path.absolute()), file_identity(path), compressed)


def collection(features=()):
    return {'type': 'FeatureCollection', 'features': list(features)}


def landmark_overlay(hotel_id, month=9, facade=None, floor=None, *, data_dir=Path('data'), events=()):
    """No guessed ray: a floor must be selected and all saved inputs must match."""
    if not isinstance(hotel_id, str) or '/' in hotel_id or '\\' in hotel_id or hotel_id in {'.', '..'}:
        raise ValueError('Unsafe hotel ID')
    data_dir = Path(data_dir)
    empty = {'landmark_targets': collection(), 'landmark_rays': collection(), 'landmark_metadata': [], 'warnings': []}
    try:
        manifest = checked_document(data_dir / 'seed/tz_landmarks_manifest.json')
        saved = checked_document(data_dir / 'seed/tz_landmark_rays.json.gz', compressed=True)
        for reference, expected in saved['inputs'].items():
            relative = Path(reference.removeprefix('/data/'))
            if not reference.startswith('/data/') or relative.is_absolute() or '..' in relative.parts:
                return {**empty, 'warnings': ['Некорректная ссылка исходных данных']}
            path = data_dir / relative
            if cached_digest(str(path.absolute()), file_identity(path)) != expected:
                return {**empty, 'warnings': ['Исходные данные изменились; требуется пересчёт лучей']}
    except (OSError, ValueError, KeyError):
        return {**empty, 'warnings': ['Сохранённые лучи недоступны']}
    targets, metadata = [], []
    for landmark in manifest.get('landmarks', []):
        target = landmark['target']
        targets.append({'type': 'Feature', 'id': landmark['id'], 'geometry': {'type': 'Point', 'coordinates': list(TO_WGS.transform(*target['xy']))},
                        'properties': {'landmark_id': landmark['id'], 'name': landmark['name'], 'height_m': target.get('height_m'),
                                       'height_source': target['height_source'], 'target_z_m': target['z_m'], 'semantics': 'Exact contour DSM target; model height/elevation, not surveyed absolute height'}})
    try:
        fisht = checked_document(data_dir / 'seed/tz_fisht_manifest.json')
        unknown = {'landmark_id': 'fisht', 'name': 'Фишт', 'height_available': False, 'available': None,
                   'unknown_reason': 'Нет подтверждённой высоты OSM/Wikidata; видимость не рассчитана', 'osm_id': fisht['osm_id'], 'wikidata_id': fisht['wikidata_id']}
        metadata.append(unknown)
        fisht_geometry = data_dir / 'seed/tz_fisht.geojson'
        if not fisht_geometry.is_file():
            fisht_geometry = Path(str(fisht_geometry)+'.gz')
        for feature in checked_document(fisht_geometry, compressed=fisht_geometry.suffix == '.gz')['features']:
            targets.append({**feature, 'id': 'fisht', 'properties': {**unknown, 'height_source': None}})
    except (OSError, ValueError, KeyError):
        pass
    active = [event for event in events if event.get('kind') == 'osm_building_new' and hotel_id in event.get('affected_hotel_ids', [])
              and event.get('status', 'applied') == 'applied']
    if active:
        return {'landmark_targets': collection(targets), 'landmark_rays': collection(), 'landmark_metadata': metadata,
                'warnings': ['После изменения окружения сохранённые лучи требуют пересчёта']}
    features = []
    if floor is not None:
        for index, ray in enumerate(saved.get('rays', {}).get(hotel_id, [])):
            if ray['floor'] == floor and (facade is None or ray['facade'] == facade):
                properties = {k: v for k, v in ray.items() if k not in {'start', 'target'}}
                properties['month_independent'] = True
                features.append({'type': 'Feature', 'id': f'landmark-ray-{index}', 'geometry': {'type': 'LineString', 'coordinates': [ray['start'], ray['target']]}, 'properties': properties})
    return {'landmark_targets': collection(targets), 'landmark_rays': collection(features), 'landmark_metadata': metadata,
            'warnings': [] if floor is not None else ['Для лучей выберите известный этаж']}
