"""External photo policy: exact byte budget, conservative HTTPS upgrades, no new approval."""
import argparse
import copy
import hashlib
import io
import json

import httpx
import pytest

from scripts import collect_hotel_photos as collector
from scripts.repair_hotel_photos import HTTPSVerifier, cached_metadata, repair


def photo(**updates):
    return {"kind": "room", "url": "https://hotel.ru/room.png", "page_url": "https://hotel.ru/",
            "local": False, "bytes": 100, "sha256": "a" * 64, "width": 640, "height": 480,
            "verified_by_eye": True, **updates}


@pytest.mark.parametrize("size,valid", [(1, True), (2_000_000, True), (2_000_001, False),
                                       (0, False), (None, False), (True, False)])
def test_external_budget_is_exact_decimal_bytes(size, valid):
    assert (collector.external_photo_issue(photo(bytes=size), "https://hotel.ru/") is None) is valid


@pytest.mark.parametrize("changes", [{"sha256": None}, {"sha256": "a" * 63},
                                     {"url": "http://hotel.ru/room.png"}, {"page_url": "http://hotel.ru/"},
                                     {"url": "https://foreign.ru/room.png"}, {"local": True}])
def test_final_guard_rejects_legacy_incomplete_or_insecure_records(changes):
    assert collector.external_photo_issue(photo(**changes), "https://hotel.ru/")


def png():
    from PIL import Image
    payload = io.BytesIO()
    Image.new("RGB", (640, 480), "blue").save(payload, "PNG")
    return payload.getvalue()


def test_old_body_cache_recovers_exact_metadata_without_mutating_cache(tmp_path):
    payload = png()
    folder = tmp_path / "site_images"
    folder.mkdir()
    record = {"url": "http://hotel.ru/room.png", "status": 200, "content_type": "image/png",
              "bytes": len(payload), "body": "original.bin"}
    (folder / "record.json").write_text(json.dumps(record))
    (folder / "original.bin").write_bytes(payload)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    result = cached_metadata([tmp_path])[record["url"]]
    assert result == {"width": 640, "height": 480, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_probe_rejects_cached_size_digest_and_redirect_disagreement(tmp_path):
    net = collector.Net(tmp_path, True, False)
    payload = png()
    (tmp_path / "site_images").mkdir()
    (tmp_path / "site_images/original.bin").write_bytes(payload)
    record = {"status": 200, "content_type": "image/png", "body": "original.bin", "bytes": len(payload) + 1}
    net.get = lambda *args: record
    assert "mismatch" in collector.probe_image(net, "https://hotel.ru/room.png")[1]
    record["bytes"] = len(payload)
    record["final_url"] = "https://foreign.ru/room.png"
    assert "redirect" in collector.probe_image(net, "https://hotel.ru/room.png")[1]
    record["final_url"] = "https://hotel.ru/room.png"
    record["probe"] = {"width": 640, "height": 480, "bytes": len(payload), "sha256": "a" * 64}
    assert "digest" in collector.probe_image(net, "https://hotel.ru/room.png")[1]


@pytest.mark.parametrize("size,accepted", [(2_000_000, True), (2_000_001, False)])
def test_probe_checks_actual_body_bytes_at_the_budget_boundary(tmp_path, size, accepted):
    payload = png()
    payload += b"\0" * (size - len(payload))
    folder = tmp_path / "site_images"
    folder.mkdir()
    (folder / "original.bin").write_bytes(payload)
    net = collector.Net(tmp_path, True, False)
    net.get = lambda *args: {"status": 200, "content_type": "image/png", "body": "original.bin", "bytes": size}
    metadata, _ = collector.probe_image(net, "https://hotel.ru/room.png")
    assert bool(metadata) is accepted
    if accepted:
        assert metadata["bytes"] == size and metadata["sha256"] == hashlib.sha256(payload).hexdigest()


def test_scoped_assembly_also_filters_unselected_legacy_rows(tmp_path):
    hotels = [{"id": "selected", "website": "https://hotel.ru/"}, {"id": "legacy", "website": "https://hotel.ru/"}]
    invalid = photo(bytes=2_000_001)
    (tmp_path / "hotels.json").write_text(json.dumps(hotels))
    (tmp_path / "index.json").write_text(json.dumps({"photos": {"legacy": [invalid]}}))
    (tmp_path / "review.json").write_text(json.dumps({"accept": {"legacy": {"room": [invalid["url"]]}}, "reject": []}))
    args = argparse.Namespace(hotels=tmp_path / "hotels.json", output=tmp_path / "index.json", review=tmp_path / "review.json",
                              photo_dir=tmp_path / "photos", cache_dir=tmp_path / "cache", only=["selected"],
                              offline=True, refresh=False, read_cache_dir=None, expand_official=False,
                              skip_commons=True, skip_official=True, require_review=True, prune_qa_previews=False)
    collector.build(args)
    assert json.loads(args.output.read_text())["photos"] == {}
    assert json.loads((args.cache_dir / "report.json").read_text())["official_log"][0]["status"] == "removed"


def verifier(tmp_path, handler, *, online=True):
    result = HTTPSVerifier(client=httpx.Client(transport=httpx.MockTransport(handler)),
                           verification_cache=tmp_path / "verified", online=online)
    result.pacer._pace = lambda *args: None
    return result


def handler_for(payload, *, image_redirect=None, robots="User-agent: *\nAllow: /\n"):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == "/":
            return httpx.Response(200, text="<html>Official property</html>", headers={"content-type": "text/html"})
        if image_redirect:
            return httpx.Response(302, headers={"location": image_redirect})
        return httpx.Response(200, content=payload, headers={"content-type": "image/png"})
    return handler


def test_https_upgrade_requires_same_sha_and_replays_offline_without_storing_original(tmp_path):
    payload = png()
    expected = {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    live = verifier(tmp_path, handler_for(payload))
    result = live.verify("http://hotel.ru/room.png", "http://hotel.ru/", expected, "http://hotel.ru/")
    assert result["url"] == "https://hotel.ru/room.png" and result["page_url"] == "https://hotel.ru/"
    assert [r["role"] for r in live.requests] == ["robots", "source_page", "image"]
    assert all(r["url"].startswith("https://") for r in live.requests)
    assert [p.suffix for p in (tmp_path / "verified").iterdir()] == [".json"]
    offline = verifier(tmp_path, lambda request: pytest.fail("offline verifier made a request"), online=False)
    assert offline.verify("http://hotel.ru/room.png", "http://hotel.ru/", expected, "http://hotel.ru/") == result
    assert not offline.requests


def test_http_is_not_upgraded_by_replacing_the_scheme(tmp_path):
    offline = verifier(tmp_path, lambda request: pytest.fail("unexpected request"), online=False)
    with pytest.raises(ValueError, match="not been verified"):
        offline.verify("http://hotel.ru/room.png", "http://hotel.ru/", photo(), "http://hotel.ru/")
    live = verifier(tmp_path, handler_for(b"different image"))
    with pytest.raises(ValueError, match="SHA-256 differ"):
        live.verify("http://hotel.ru/room.png", "http://hotel.ru/", photo(), "http://hotel.ru/")
    assert not (tmp_path / "verified").exists()


@pytest.mark.parametrize("destination", ["http://hotel.ru/room.png", "https://foreign.ru/room.png",
                                         "https://booking.com/room.png", "https://yandex.ru/image"])
def test_redirect_is_rejected_before_requesting_foreign_or_insecure_host(tmp_path, destination):
    live = verifier(tmp_path, handler_for(b"image", image_redirect=destination))
    with pytest.raises(ValueError, match="redirect"):
        live.verify("http://hotel.ru/room.png", "http://hotel.ru/", photo(), "http://hotel.ru/")
    assert destination not in [r["url"] for r in live.requests]


def test_robots_disallow_prevents_the_image_get(tmp_path):
    live = verifier(tmp_path, handler_for(b"image", robots="User-agent: *\nDisallow: /room.png\n"))
    with pytest.raises(ValueError, match="robots policy forbids"):
        live.verify("http://hotel.ru/room.png", "http://hotel.ru/", photo(), "http://hotel.ru/")
    assert [r["role"] for r in live.requests] == ["robots", "source_page"]


def test_unknown_content_length_is_still_bounded_by_actual_received_bytes(tmp_path):
    live = verifier(tmp_path, handler_for(b"x" * 2_000_001))
    with pytest.raises(ValueError, match="byte budget"):
        live.verify("http://hotel.ru/room.png", "http://hotel.ru/", photo(), "http://hotel.ru/")
    assert not (tmp_path / "verified").exists()


def test_pacer_shares_a_budget_between_subdomains(monkeypatch, tmp_path):
    clock = [10.0]
    waits = []
    monkeypatch.setattr(collector.time, "monotonic", lambda: clock[0])
    def sleep(amount):
        waits.append(amount)
        clock[0] += amount
    monkeypatch.setattr(collector.time, "sleep", sleep)
    net = collector.Net(tmp_path, True, False)
    net._pace("https://www.hotel.ru/robots.txt")
    net._pace("https://images.hotel.ru/room.png")
    assert waits == [1.0] and net.requests == 2


def test_repair_drops_heavy_cover_preserves_room_kind_and_is_idempotent(tmp_path):
    cover = photo(kind="official_site", url="https://hotel.ru/large.png", bytes=2_000_001)
    room = photo()
    index = {"counts": {}, "room_hotel_count": 1, "room_photo_count": 1, "photos": {"h": [cover, room]}}
    review = {"accept": {"h": {"official_site": cover["url"], "room": [room["url"]]}}, "reject": []}
    hotels = [{"id": "h", "website": "https://hotel.ru/"}]
    before = copy.deepcopy((index, review))
    offline = verifier(tmp_path, lambda request: pytest.fail("unexpected request"), online=False)
    fixed, decisions, report = repair(index, review, hotels, {}, offline)
    assert (index, review) == before
    assert fixed["photos"] == {"h": [room]}
    assert fixed["counts"] == {"building": 0, "official_site": 0, "street": 0, "none": 1}
    assert decisions["accept"]["h"] == {"room": [room["url"]]}
    assert len(report["removed"]) == 1 and report["get_count"] == 0
    again, same_review, report2 = repair(fixed, decisions, hotels, {}, offline)
    assert again == fixed and same_review == decisions and not report2["removed"]


def test_repair_keeps_visual_history_and_updates_exact_kind_pins_after_verified_upgrade(tmp_path):
    payload = png()
    row = photo(url="http://hotel.ru/room.png", page_url="http://hotel.ru/", bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest())
    index = {"counts": {}, "room_hotel_count": 1, "room_photo_count": 1, "photos": {"h": [row]}}
    review = {"accept": {"h": {"room": [row["url"]]}}, "reject": [], "visual_batches": [{"artifact": "original-review"}]}
    fixed, decisions, report = repair(index, review, [{"id": "h", "website": "http://hotel.ru/"}], {},
                                       verifier(tmp_path, handler_for(payload)))
    assert fixed["photos"]["h"][0]["kind"] == "room"
    assert decisions["accept"]["h"] == {"room": ["https://hotel.ru/room.png"]}
    assert decisions["visual_batches"] == review["visual_batches"]
    assert decisions["https_upgrades"][0]["sha256"] == row["sha256"]
    assert len(report["upgraded"]) == 1
