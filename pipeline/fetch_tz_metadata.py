"""Explicit OSM/Commons features and separately licensed mobile tile samples."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from shapely.geometry import Point, shape

from geo.io import read_json
from geo.tz_metadata import commons_features, explicit_osm_features, mobile_sample
from pipeline.common import CONFIG, SEED, dump, fetch, gzip_json, sha, source, utcnow
from pipeline.tz_common import baseline_sources, observation, spec_input, write_stage


def run():
    hotels = read_json(SEED / 'hotels.json')
    extracted = read_json(SEED / 'tz_place_hotel_tags.json')
    elements = {e['osm_id']: e for e in extracted['hotel_elements']}
    photo_index = read_json(SEED / 'hotel_photos.json')['photos']
    titles = sorted({unquote(p['page_url'].split('/wiki/', 1)[1]).replace('_', ' ')
                     for photos in photo_index.values() for p in photos if p.get('page_url', '').startswith('https://commons.wikimedia.org/wiki/File:')})
    pages, media, downloads, errors = [], {}, [], []
    try:
        path, meta = fetch('https://commons.wikimedia.org/w/api.php', 'tz_commons_metadata.json', params={
            'action': 'query', 'format': 'json', 'titles': '|'.join(titles), 'prop': 'imageinfo', 'iiprop': 'url|timestamp|extmetadata'})
        downloads.append(meta)
        pages = list(read_json(path).get('query', {}).get('pages', {}).values())
        media_ids = ['M'+str(p['pageid']) for p in pages if p.get('pageid', 0) > 0]
        if media_ids:
            path, meta = fetch('https://commons.wikimedia.org/w/api.php', 'tz_commons_depicts.json', params={
                'action': 'wbgetentities', 'format': 'json', 'ids': '|'.join(media_ids), 'props': 'claims'})
            downloads.append(meta)
            media = read_json(path).get('entities', {})
    except Exception as error:
        errors.append({'source': 'Commons API', 'reason': type(error).__name__})
    commons_path = SEED / 'tz_commons_metadata.json.gz'
    gzip_json(commons_path, {'pages': pages, 'media': media, 'downloads': downloads, 'errors': errors,
                            'rights': 'Structured data CC0; descriptions CC BY-SA; individual file licenses retained in extmetadata',
                            'identity_policy': 'Exact P180 QID or exact OSM File tag only; name/proximity matches from photo index are not accepted'})
    probes = Path('/tmp/okno-tz-culture-probe.json')
    checks = read_json(probes) if probes.exists() else []
    check_path = SEED / 'tz_public_source_checks.json'
    dump(check_path, {'checked_at': utcnow(), 'checks': checks, 'events_reason': 'Current events bulk unavailable; public passport marks download unavailable and directs live export to integration-key API',
                      'heritage_reason': 'Public EGRKN passport accessible; first advertised bulk format probe returned HTTP404; no exact contour/registry-ID dataset acquired within bounded research'})
    connectivity = read_json(SEED / 'connectivity.json')
    rows = []
    for hotel in hotels:
        exact_ids = {hotel['id'], hotel.get('osm_id')}-{None}
        building_id = hotel.get('tags', {}).get('building_id')
        if building_id and hotel.get('footprint') and shape(hotel['footprint']).covers(Point(hotel['lon'], hotel['lat'])):
            exact_ids.add(building_id)
        exact = [elements[oid] for oid in exact_ids if oid in elements]
        value = explicit_osm_features(exact)
        qids = {e['tags']['wikidata'] for e in exact if e['tags'].get('wikidata')}
        exact_titles = {e['tags']['wikimedia_commons'].replace('_', ' ') for e in exact if e['tags'].get('wikimedia_commons', '').startswith('File:')}
        for page in pages:
            value['evidence_records'].extend(commons_features(page, media.get('M'+str(page.get('pageid')), {}), qids, exact_titles))
        for key in ('balcony', 'terrace', 'pool'):
            answers = {r['value'] for r in value['evidence_records'] if r['feature'] == key}
            value[key] = next(iter(answers)) if len(answers) == 1 else None
        value['available'] = True if any(value[k] is not None for k in ('balcony', 'terrace', 'pool')) else None
        value['unknown_reason'] = None if value['available'] else 'No explicit exact-linked OSM tag or licensed Commons description; image appearance/name/proximity is not evidence'
        rows.append(observation(hotel['id'], 'photo_features', value, source_ids=['tz_osm_geofabrik']+(['tz_commons_metadata'] if downloads else []),
                                observed_at=CONFIG['osm']['snapshot'], ttl_days=180, confidence=.5))
        mobile = mobile_sample(hotel['lon'], hotel['lat'], connectivity['tiles'])
        mobile['opencellid'] = {'status': 'not_used', 'reason': 'No read-only OpenCellID key file configured; no coverage or generation inferred'}
        rows.append(observation(hotel['id'], 'mobile_internet', mobile, source='ookla', source_ids=['ookla'], observed_at='2026-09-30T00:00:00Z', ttl_days=365, confidence=.4))
        rows.append(observation(hotel['id'], 'events_nearby', {'available': None, 'event_count': None, 'events': [],
                                'unknown_reason': read_json(check_path)['events_reason'], 'checked_at': read_json(check_path)['checked_at']},
                                source='tz_mincult_availability', source_ids=['tz_mincult_availability'], observed_at=read_json(check_path)['checked_at'], ttl_days=14, confidence=.5))
    sources = [*read_json(SEED / 'tz_osm_sources.json'), baseline_sources()['ookla'],
               source('tz_mincult_availability', 'Минкультуры: проверка доступности открытого набора событий', 'https://opendata.mkrf.ru/opendata/7705851331-events',
                      'Russian open-data standard terms', 'Минкультуры России; https://data.gov.ru/information-usage', utcnow(), utcnow(),
                      'Only dataset availability metadata checked; no event records or unlicensed public-page scraping')]
    if downloads:
        sources.append(source('tz_commons_metadata', 'Commons exact file metadata', downloads[0]['url'], 'CC0-1.0 structured; CC-BY-SA-4.0 descriptions; per-file image licenses',
                              'Wikimedia Commons contributors; author and per-file rights in metadata', downloads[0]['retrieved_at'], downloads[0]['retrieved_at'],
                              'https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia ; no image ML, photo index writes or nearby/name identity inference'))
    inputs = {'/data/seed/'+name: sha(SEED / name) for name in ['hotels.json', 'hotel_photos.json', 'connectivity.json', 'tz_place_hotel_tags.json', commons_path.name, check_path.name]}
    inputs.update(spec_input(k) for k in ('photo_features', 'mobile_internet', 'events_nearby'))
    manifest = write_stage('metadata', rows, sources, inputs=inputs, limitations=[
        'Mobile samples are independent from fixed/Wi-Fi and masts; unmapped tile is unknown, never zero Mbps',
        'OSM explicit features do not prove room inventory or a particular balcony/floor; Commons text is attributed, never image inference',
        'No current licensed event dataset was acquired; no stale event is substituted', 'OpenCellID key absent: no requests, no cells, no 4G/5G claim'])
    manifest.update(downloads=downloads, errors=errors)
    dump(SEED / 'tz_metadata_manifest.json', manifest)
    return manifest


if __name__ == '__main__':
    run()
