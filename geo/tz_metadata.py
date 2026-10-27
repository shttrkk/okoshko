"""Explicit licensed metadata only: no visual classification or network inference."""
from __future__ import annotations

import html
import re

from shapely import wkt
from shapely.geometry import Point


def mobile_sample(lon, lat, tiles):
    matches = [tile for tile in tiles if tile.get('kind') == 'mobile' and tile.get('tile') and wkt.loads(tile['tile']).covers(Point(lon, lat))]
    if len(matches) != 1:
        return {'available': None, 'download_mbps': None, 'upload_mbps': None, 'latency_ms': None, 'tests': None, 'devices': None,
                'quarter': None, 'tile_quadkey': None, 'radio_generation': None, 'hotel_wifi': False,
                'unknown_reason': 'No exact containing mobile tile' if not matches else 'Point lies in multiple tile boundaries; no guessed aggregate'}
    tile = matches[0]
    return {'available': True, 'download_mbps': tile['avg_d_kbps']/1000, 'upload_mbps': tile['avg_u_kbps']/1000,
            'latency_ms': tile['avg_lat_ms'], 'tests': tile['tests'], 'devices': tile['devices'], 'quarter': tile['quarter'],
            'tile_quadkey': tile['quadkey'], 'radio_generation': None, 'hotel_wifi': False, 'unknown_reason': None,
            'semantics': 'Area aggregate of cellular tests; not hotel Wi-Fi, room coverage or proof of 4G/5G generation'}


def explicit_osm_features(elements):
    records = []
    values = {key: None for key in ('balcony', 'terrace', 'pool')}
    for element in elements:
        tags = element['tags']
        for feature, keys in {'balcony': ['balcony', 'building:balconies'], 'terrace': ['terrace'], 'pool': ['swimming_pool']}.items():
            for key in keys:
                if tags.get(key) in {'yes', 'no'}:
                    result = tags[key] == 'yes'
                    records.append({'feature': feature, 'value': result, 'osm_id': element['osm_id'], 'tag': key, 'quote': tags[key],
                                    'element_timestamp': element.get('element_timestamp'), 'semantics': 'Exact object tag; not a room/floor/window attribution'})
    for key in values:
        answers = {r['value'] for r in records if r['feature'] == key}
        values[key] = next(iter(answers)) if len(answers) == 1 else None
    return {**values, 'evidence_records': records}


def commons_features(page, media, exact_qids=(), exact_file_titles=()):
    """Require exact entity/file linkage, license and explicit description words."""
    depicts = {statement.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
               for statement in media.get('statements', {}).get('P180', []) if statement.get('rank') != 'deprecated'}
    if not (depicts.intersection(exact_qids) or page.get('title') in exact_file_titles):
        return []
    info = (page.get('imageinfo') or [{}])[0]
    metadata = info.get('extmetadata', {})
    def text(key):
        return html.unescape(re.sub('<[^>]+>', ' ', metadata.get(key, {}).get('value', ''))).strip()
    if not text('Artist') or not text('LicenseUrl') or not text('LicenseShortName'):
        return []
    description = text('ImageDescription')
    # Only explicit nouns, with local negation blocked. A file title/name is
    # never the feature source and proximity is never hotel identity.
    patterns = {'balcony': r'\b(?:balcon(?:y|ies)|балкон(?:ы|ом|ами|ов)?)\b',
                'terrace': r'\b(?:terraces?|террас(?:а|ы|ой|у)?)\b',
                'pool': r'\b(?:swimming pool|бассейн(?:ы|ом|ами|ов)?)\b'}
    records = []
    for feature, pattern in patterns.items():
        for match in re.finditer(pattern, description, re.I):
            prefix = description[max(0, match.start()-25):match.start()].lower()
            if re.search(r'\b(?:no|not|without|нет|без|не)\b', prefix):
                continue
            records.append({'feature': feature, 'value': True, 'page_id': page.get('pageid'), 'title': page['title'],
                            'quote': description[max(0, match.start()-40):match.end()+40], 'author': text('Artist'),
                            'license': text('LicenseShortName'), 'license_url': text('LicenseUrl'), 'page_url': info.get('descriptionurl'),
                            'capture_date': text('DateTimeOriginal') or None, 'upload_timestamp': info.get('timestamp'),
                            'identity': 'exact_depicts_qid' if depicts.intersection(exact_qids) else 'exact_osm_file_tag',
                            'semantics': 'Explicit Commons description, not image recognition or current room inventory'})
    return records
