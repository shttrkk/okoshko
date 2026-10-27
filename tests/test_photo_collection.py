"""Collection guards: seed domain, explicit eye-review decisions, bounded linked-page discovery."""
import argparse
import io
import json

import httpx
import pytest

from scripts import collect_hotel_photos as collector
from scripts.photo_collection import linked_pages, reviewed_rows


@pytest.mark.parametrize("url", ["https://maps.google.ru/image.jpg", "https://google.co.uk/image.jpg",
                                  "https://avatars.mds.yandex.net/image.jpg", "https://yandex.kz/maps",
                                  "https://photo.2gis.ru/image.jpg", "https://cdn.bstatic.com/image.jpg"])
def test_map_ota_provider_families_are_never_collection_sources(url):
    assert collector.denied(url)


def test_seed_domain_excludes_builder_cdns_and_foreign_redirect_before_request(tmp_path):
    assert collector.image_host_ok("https://img.hotel.ru/room.jpg", "https://www.hotel.ru/")
    assert not collector.image_host_ok("https://static.tildacdn.com/room.jpg", "https://hotel.ru/")
    assert not collector.image_host_ok("https://hotel.ru.foreign.com/room.jpg", "https://hotel.ru/")
    net = collector.Net(tmp_path, True, False)
    net.site_domain = "hotel.ru"
    with pytest.raises(ValueError, match="outside seed"):
        net._pace("https://foreign.ru/robots.txt")
    assert net.requests == 0


def test_links_only_follow_actual_same_domain_content_pages():
    doc = collector.PageImages()
    doc.feed('''<a href="/rooms/">Номера</a><a href="/photo/">Фото</a>
    <a href="https://ota.example/gallery">Галерея</a><a href="/booking/rooms">Номера</a>
    <a href="#rooms">Номера</a><a href="/privacy">Об отеле</a><a href="/unrelated">Новости</a>''')
    assert linked_pages(doc, "https://hotel.ru/", "https://hotel.ru/") == ["https://hotel.ru/photo/", "https://hotel.ru/rooms/"]


def test_room_approval_is_exact_and_never_inferred_from_cover():
    rows = [{"url": "https://hotel.ru/room1.jpg"}, {"url": "https://hotel.ru/room2.jpg"}]
    review = {"accept": {"h": {"official_site": rows[0]["url"], "room": [rows[1]["url"]]}}}
    selected = reviewed_rows("h", rows, review)
    assert [(r["kind"], r["url"], r["verified_by_eye"]) for r in selected] == [
        ("official_site", rows[0]["url"], True), ("room", rows[1]["url"], True)]
    assert reviewed_rows("unknown", rows, review) == []
    review["accept"]["h"]["room"].append(rows[0]["url"])
    with pytest.raises(ValueError, match="Cover URL"):
        reviewed_rows("h", rows, review)


def test_preview_cache_never_writes_official_original(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    payload = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(payload, "PNG")
    response = httpx.Response(200, content=payload.getvalue(), headers={"content-type": "image/png"},
                              request=httpx.Request("GET", "https://hotel.ru/room.png"))
    monkeypatch.setattr(collector, "fetch", lambda *args: response)
    net = collector.Net(tmp_path, False, False, preview_only=True)
    record = net.get("https://hotel.ru/room.png", "site_images")
    assert "body" not in record and "preview" in record
    assert not list(tmp_path.rglob("*.bin"))
    size, note = collector.probe_image(net, "https://hotel.ru/room.png")
    assert note == "ok" and size["width"] == 640 and size["bytes"] == len(payload.getvalue())


def test_scoped_rebuild_preserves_building_and_counts_room_only_as_none(tmp_path, monkeypatch):
    hotels = [{"id": "h1", "website": "https://hotel.ru/"}, {"id": "h2", "website": "https://other.ru/"}]
    building = {"kind": "building", "url": "/local.webp", "local": True, "verified_by_eye": True}
    room = {"kind": "room", "url": "https://other.ru/room.jpg", "page_url": "https://other.ru/rooms/",
            "local": False, "verified_by_eye": True, "bytes": 100, "sha256": "a" * 64}
    (tmp_path / "hotels.json").write_text(json.dumps(hotels))
    (tmp_path / "index.json").write_text(json.dumps({"photos": {"h1": [building], "h2": [room]}}))
    (tmp_path / "review.json").write_text(json.dumps({"accept": {"h2": {"room": [room["url"]]}}, "reject": []}))
    args = argparse.Namespace(hotels=tmp_path / "hotels.json", output=tmp_path / "index.json",
                              review=tmp_path / "review.json", photo_dir=tmp_path / "photos", cache_dir=tmp_path / "cache",
                              only=["h1"], offline=True, refresh=False, read_cache_dir=None, expand_official=False,
                              skip_commons=True, skip_official=True, require_review=True, prune_qa_previews=False)
    collector.build(args)
    result = json.loads(args.output.read_text())
    assert result["photos"] == {"h1": [building], "h2": [room]}
    assert result["counts"] == {"building": 1, "official_site": 0, "street": 0, "none": 1}
    assert result["room_hotel_count"] == result["room_photo_count"] == 1
    # The original cover-only mode must not silently erase an already reviewed room gallery.
    args.only = None
    args.skip_official = False
    monkeypatch.setattr(collector, "official_candidates", lambda *args: ({}, []))
    collector.build(args)
    assert json.loads(args.output.read_text())["photos"]["h2"] == [room]


def test_fallback_cache_is_read_only(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    path = source / "pages" / (collector.key_of("https://hotel.ru/") + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"body": "page.bin"}))
    (path.parent / "page.bin").write_bytes(b"cached")
    net = collector.Net(output, True, False, read_cache=source)
    record = net.get("https://hotel.ru/", "pages")
    assert net.body(record, "pages") == b"cached"
    assert not output.exists()


def test_pruning_only_removes_explicitly_reviewed_local_qa(tmp_path):
    from scripts.photo_collection import prune_reviewed_previews

    local = tmp_path / 'site_images'
    local.mkdir()
    for name in ('accepted', 'rejected', 'pending'):
        (local / (name + '.json')).write_text(json.dumps({'url': 'https://hotel.ru/' + name,
                                                        'preview': name + '.qa.jpg', 'probe': {'width': 800}}))
        (local / (name + '.qa.jpg')).write_bytes(b'qa-preview')
    review = {'accept': {'h': {'room': ['https://hotel.ru/accepted']}},
              'reject': [{'ref': 'https://hotel.ru/rejected'}]}
    net = collector.Net(tmp_path, True, False)
    assert prune_reviewed_previews(net, review) == 2
    assert sorted(p.name for p in local.glob('*.qa.jpg')) == ['pending.qa.jpg']
    assert len(list(local.glob('*.json'))) == 3


def test_explicit_approved_urls_survive_candidate_caps_and_other_kind_rejection(tmp_path, monkeypatch):
    import hashlib

    from scripts import photo_collection

    doc = collector.PageImages()
    doc.feed('<title>Hotel Sunrise Sochi</title>')
    website = 'https://sunrise.ru/'
    cover, rooms = website + 'cover.jpg', [website + f'room{i}.jpg' for i in range(3)]
    review = {'accept': {'h': {'official_site': cover, 'room': rooms}},
              'reject': [{'hotel_id': 'h', 'kind': 'room', 'ref': cover}],
              'image_pages': {'h': dict.fromkeys(rooms, website)}}
    monkeypatch.setattr(photo_collection, 'document_from',
                        lambda *args: (doc, {'final_url': website}, None))
    monkeypatch.setattr(photo_collection, 'probe_image', lambda net, url: (
        {'width': 640, 'height': 480, 'bytes': 100, 'sha256': hashlib.sha256(url.encode()).hexdigest(),
         'final_url': url}, 'ok'))
    net = collector.Net(tmp_path, True, False)
    candidates, _ = photo_collection.collect([{'id': 'h', 'name': 'Sunrise Hotel', 'website': website,
                                              'lat': 43.5, 'lon': 39.8}], net, review, max_pages=1, max_images=2)
    assert {r['url'] for r in candidates['h']} == {cover, *rooms}


def test_retry_after_defers_following_host_requests(tmp_path, monkeypatch):
    response = httpx.Response(429, content=b'retry later', headers={'retry-after': '5'},
                              request=httpx.Request('GET', 'https://hotel.ru/page'))
    monkeypatch.setattr(collector, 'fetch', lambda *args: response)
    monkeypatch.setattr(collector.time, 'monotonic', lambda: 10.0)
    waits = []
    monkeypatch.setattr(collector.time, 'sleep', waits.append)
    net = collector.Net(tmp_path, False, False)
    net.get('https://hotel.ru/page', 'pages')
    net._pace('https://hotel.ru/next')
    assert waits == [5.0]
