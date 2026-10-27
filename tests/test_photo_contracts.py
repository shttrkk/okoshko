"""Photo contracts separate linked room interiors from attributed street views."""
import json

import pytest
from pydantic import ValidationError

from backend.api import catalog
from backend.contracts import HotelPhoto, HotelPhotoIndex
from backend.tests.test_api_integration import client, repo  # noqa: F401


def linked(**changes):
    return {"kind": "room", "url": "https://hotel.example/rooms/one.jpg", "local": False,
            "page_url": "https://hotel.example/rooms/", "title": "Synthetic room fixture",
            "width": 1000, "height": 700, "verified_by_eye": True, **changes}


def street(**changes):
    return {**linked(), "kind": "street", "url": "/data/seed/photos/hotels/test-street.webp",
            "local": True, "page_url": "https://www.mapillary.com/app/?pKey=123",
            "author": "Synthetic fixture author", "license": "CC BY-SA 4.0",
            "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "captured_year": 2025, "image_id": "123", "width": 640, "height": 480,
            "bytes": 50_000, "sha256": "a" * 64, **changes}


def test_linked_room_has_no_implied_position_or_free_licence():
    photo = HotelPhoto.model_validate(linked())
    assert photo.kind == "room" and not photo.local and photo.license is None
    for value in ({"local": True}, {"license": "CC BY-SA 4.0"}, {"author": "Invented author"},
                  {"url": "javascript:alert(1)"}, {"page_url": "https://user:password@hotel.example/"},
                  {"url": "https://hotel.example/room.jpg?access_token=fixture-token"},
                  {"verified_by_eye": False}, {"facade": "W"}, {"floor": 5}):
        with pytest.raises(ValidationError):
            HotelPhoto.model_validate(linked(**value))


@pytest.mark.parametrize("changes", [{"captured_year": None}, {"image_id": None}, {"author": None},
                         {"width": 641}, {"bytes": 60001}, {"bytes": None}, {"sha256": None},
                         {"license": "CC BY 4.0"}, {"page_url": "https://other.example/123"},
                         {"url": "/data/seed/photos/hotels/../wrong.webp"}])
def test_street_requires_saved_small_attributed_dated_frame(changes):
    with pytest.raises(ValidationError):
        HotelPhoto.model_validate(street(**changes))


def test_street_and_shipped_legacy_index_validate():
    assert HotelPhoto.model_validate(street()).captured_year == 2025
    index = HotelPhotoIndex.model_validate_json((catalog.DATA / "seed/hotel_photos.json").read_text())
    assert index.photos and all(p.verified_by_eye for rows in index.photos.values() for p in rows)


def test_photo_api_reads_only_local_index_and_isolates_hotel(client, repo, monkeypatch, tmp_path):  # noqa: F811
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "hotel_photos.json").write_text(json.dumps({"photos": {"test-2": [linked()],
                                                                   "different-hotel": [street()]}}))
    monkeypatch.setattr(catalog, "DATA", tmp_path)
    response = client.get("/api/hotels/test-2/photos")
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["kind"] == "room"
    assert client.get("/api/hotels/test-3/photos").json() == []
    assert client.get("/api/hotels/not-present/photos").status_code == 404
    (seed / "hotel_photos.json").unlink()
    assert client.get("/api/hotels/test-2/photos").json() == []
