"""Hotel photo index invariants: provenance, free licences, size budget, official images only linked."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from scripts.collect_hotel_photos import ASPECT, DENYLIST, FREE_LICENSE, MAX_WEBP_BYTES, MIN_SITE_WIDTH

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data/seed/hotel_photos.json"
PHOTO_DIR = ROOT / "data/seed/photos/hotels"
KINDS = ["building", "official_site"]


def test_hotel_photo_index_is_honest_and_self_consistent():
    data = json.loads(INDEX.read_text())
    hotels = {h["id"] for h in json.loads((ROOT / "data/seed/hotels.json").read_text())}
    photos = data["photos"]
    assert set(photos) <= hotels and data["method"] and data["generated_at"]
    best = [rows[0]["kind"] for rows in photos.values()]
    assert data["counts"] == {"building": best.count("building"), "official_site": best.count("official_site"),
                              "none": len(hotels) - len(photos)}
    local_total, local_files = 0, set()
    for hotel_id, rows in photos.items():
        assert 1 <= len(rows) <= 3
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
            else:
                # Copyrighted hotel images are linked, never stored or relicensed.
                assert row["local"] is False and row["url"].startswith(("https://", "http://"))
                assert row["license"] is None and row["license_url"] is None and row["author"] is None
                assert row["match"] in {"og_image", "hero_img"} and row["distance_m"] is None
                assert row["width"] >= MIN_SITE_WIDTH and ASPECT[0] <= row["width"] / row["height"] <= ASPECT[1]
                for url in (row["url"], row["page_url"]):
                    host = (urlsplit(url).hostname or "").removeprefix("www.")
                    assert not any(host == d or host.endswith("." + d) for d in DENYLIST), url
                assert not re.search(r"logo|icon|favicon|sprite|placeholder", row["url"], re.I)
    assert {p.name for p in PHOTO_DIR.iterdir()} == local_files
    assert local_total <= 12_000_000
