"""Hotel photo index invariants: provenance, free licences, size budget, official images only linked."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from scripts.collect_hotel_photos import (
    ASPECT,
    DENYLIST,
    FREE_LICENSE,
    MAX_WEBP_BYTES,
    MIN_SITE_WIDTH,
    image_host_ok,
    normalize_site,
)

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data/seed/hotel_photos.json"
PHOTO_DIR = ROOT / "data/seed/photos/hotels"
KINDS = ["building", "official_site", "street", "room"]


def test_hotel_photo_index_is_honest_and_self_consistent():
    data = json.loads(INDEX.read_text())
    hotel_rows = json.loads((ROOT / "data/seed/hotels.json").read_text())
    hotels = {h["id"] for h in hotel_rows}
    websites = {h["id"]: normalize_site(h.get("website")) for h in hotel_rows}
    photos = data["photos"]
    assert set(photos) <= hotels and data["method"] and data["generated_at"]
    best = [next((r["kind"] for r in rows if r["kind"] != "room"), "none") for rows in photos.values()]
    counts = {kind: best.count(kind) for kind in KINDS if kind != "room"}
    counts["none"] = len(hotels) - sum(counts.values())
    assert data["counts"] == counts
    assert data["room_hotel_count"] == sum(any(r["kind"] == "room" for r in rs) for rs in photos.values())
    assert data["room_photo_count"] == sum(r["kind"] == "room" for rs in photos.values() for r in rs)
    review = json.loads((ROOT / "data/eval/hotel_photos_review.json").read_text())
    local_total, local_files = 0, set()
    for hotel_id, rows in photos.items():
        assert 1 <= len(rows) <= 11
        assert sum(r["kind"] == "room" for r in rows) <= 8
        assert len({r["url"] for r in rows}) == len(rows)
        assert [r["kind"] for r in rows] == sorted((r["kind"] for r in rows), key=KINDS.index), "best tier first"
        for row in rows:
            assert row["verified_by_eye"] is True, (hotel_id, row["url"])
            assert row["page_url"].startswith(("https://", "http://")) and row["title"]
            assert row["width"] > 0 and row["height"] > 0
            if row["kind"] == "building":
                assert row["local"] is True and row["url"] == "/data/seed/photos/hotels/" + hotel_id + ".webp"
                assert row["page_url"].startswith("https://commons.wikimedia.org/wiki/File:")
                assert row["match"] in {"wikidata_p18", "osm_tag", "commons_name_match"}
                assert FREE_LICENSE.match(row["license"])
                assert row["author"] or re.match(r"(?i)cc0|public domain|pd", row["license"])
                blob = (ROOT / row["url"].lstrip("/")).read_bytes()
                assert blob[:4] == b"RIFF" and blob[8:12] == b"WEBP" and len(blob) <= MAX_WEBP_BYTES
                assert hashlib.sha256(blob).hexdigest() == row["sha256"] and len(blob) == row["bytes"]
                local_total += len(blob)
                local_files.add(hotel_id + ".webp")
            elif row["kind"] in {"official_site", "room"}:
                assert image_host_ok(row["url"], websites[hotel_id])
                assert image_host_ok(row["page_url"], websites[hotel_id])
                # Copyrighted hotel images are linked, never stored or relicensed.
                assert row["local"] is False and row["url"].startswith(("https://", "http://"))
                assert row["license"] is None and row["license_url"] is None and row["author"] is None
                assert row["match"] in {"og_image", "hero_img"} and row["distance_m"] is None
                if row["kind"] == "room":
                    assert row["url"] in review["accept"][hotel_id]["room"]
                    assert row["bytes"] > 0 and re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                assert row["width"] >= MIN_SITE_WIDTH and ASPECT[0] <= row["width"] / row["height"] <= ASPECT[1]
                for url in (row["url"], row["page_url"]):
                    host = (urlsplit(url).hostname or "").removeprefix("www.")
                    assert not any(host == d or host.endswith("." + d) for d in DENYLIST), url
                assert not re.search(r"logo|icon|favicon|sprite|placeholder", row["url"], re.I)
    assert {p.name for p in PHOTO_DIR.iterdir()} == local_files
    assert local_total <= 12_000_000
