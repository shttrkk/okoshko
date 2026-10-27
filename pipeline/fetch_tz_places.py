"""Small exact hotel-tag and regional place-node extracts from the existing PBF."""
from __future__ import annotations

import json

from geo.io import read_json
from pipeline.common import CONFIG, SEED, dump, fetch

PUBLIC_CENTER_IDS = ('osm-node-3771836366', 'osm-node-2493934195', 'osm-node-2493935121')


def run():
    import osmium

    hotels = read_json(SEED / 'hotels.json')
    ids = {value for hotel in hotels for value in [hotel.get('id'), hotel.get('osm_id'), hotel.get('building_id'), hotel.get('tags', {}).get('building_id')]
           if isinstance(value, str) and value.startswith('osm-')}
    path, metadata = fetch(CONFIG['osm']['url'], CONFIG['osm']['url'].rsplit('/', 1)[-1])
    elements, public_references = [], []
    processor = osmium.FileProcessor(str(path)).with_filter(osmium.filter.IdFilter([int(oid.rsplit('-', 1)[1]) for oid in ids | set(PUBLIC_CENTER_IDS)]))
    if processor.header.get('osmosis_replication_timestamp') != CONFIG['osm']['snapshot']:
        raise ValueError('PBF snapshot mismatch')
    for item in processor:
        kind = {"n": "node", "w": "way", "r": "relation"}[item.type_str()]
        oid = f"osm-{kind}-{item.id}"
        if oid in ids:
            elements.append({'osm_id': oid, 'tags': dict(item.tags), 'version': item.version, 'element_timestamp': item.timestamp.isoformat()})
        if oid in PUBLIC_CENTER_IDS and kind == 'node' and item.location.valid():
            public_references.append({'osm_id': oid, 'tags': dict(item.tags), 'lon': item.location.lon, 'lat': item.location.lat,
                                      'version': item.version, 'element_timestamp': item.timestamp.isoformat()})
    places = []
    for item in (osmium.FileProcessor(str(path)).with_filter(osmium.filter.EntityFilter(osmium.osm.NODE))
                 .with_filter(osmium.filter.KeyFilter('place'))):
        if item.location.valid() and 39.6 <= item.location.lon <= 40.1 and 43.3 <= item.location.lat <= 43.8:
            places.append({'osm_id': f'osm-node-{item.id}', 'tags': dict(item.tags), 'lon': item.location.lon, 'lat': item.location.lat,
                           'version': item.version, 'element_timestamp': item.timestamp.isoformat()})
    squares = []
    factory = osmium.geom.GeoJSONFactory()
    processor = (osmium.FileProcessor(str(path)).with_areas(osmium.filter.TagFilter(('place', 'square')))
                 .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
    for item in processor:
        if not item.from_way() and item.orig_id() == 416831:
            squares.append({'osm_id': 'osm-relation-416831', 'tags': dict(item.tags),
                            'geometry': json.loads(factory.create_multipolygon(item))})
    if {row['osm_id'] for row in public_references} != set(PUBLIC_CENTER_IDS) or len(squares) != 1:
        raise ValueError('Public centre references are missing from the fixed PBF')
    output = {'places': places, 'hotel_elements': elements, 'public_center_references': public_references,
              'public_center_areas': squares, 'snapshot': CONFIG['osm']['snapshot'], 'download': metadata,
              'limitations': ['Exact IDs only; no hotel-name or nearby-structure identity inference', 'Place nodes define an explicit destination, not a geometric or universal city centre']}
    dump(SEED / 'tz_place_hotel_tags.json', output)
    print({'places': len(places), 'hotel_elements': len(elements), 'public_references': len(public_references)}, flush=True)
    return output


if __name__ == '__main__':
    run()
