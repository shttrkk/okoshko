"""Small map points for places already linked by current local fact evidence."""
from __future__ import annotations

import gzip
import json
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from shapely.errors import GEOSException
from shapely.geometry import shape

# These fields carry exact IDs. A distance or name alone cannot locate a place.
LINKS = (
    ("beach_walk", "beach_id", "beach", "beaches", None),
    ("night_pharmacy", "osm_id", "pharmacy", "pois", "distance_m"),
    ("car_access", "parking_osm_id", "parking", "pois", "parking_distance_m"),
    ("family_nearby", "playground_osm_id", "playground", "pois", "playground_distance_m"),
    ("green_nearby", "osm_id", "park", "pois", "distance_m"),
)
NAMES = {"beach": "Пляж", "pharmacy": "Аптека", "parking": "Парковка",
         "playground": "Детская площадка", "park": "Парк или сад"}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _coordinates_valid(coordinates, depth=0):
    if not isinstance(coordinates, list | tuple) or not coordinates or depth > 3:
        return False
    if isinstance(coordinates[0], list | tuple):
        return all(_coordinates_valid(part, depth+1) for part in coordinates)
    return (2 <= len(coordinates) <= 3 and all(_number(n) is not None for n in coordinates)
            and -180 <= coordinates[0] <= 180 and -90 <= coordinates[1] <= 90)


def _snapshot(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


@lru_cache(maxsize=8)
def _index(path: Path, snapshot: tuple):
    """Index the chosen physical file, including atomic/in-place replacements."""
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
        if _snapshot(path) != snapshot:
            return {}  # A concurrent replacement has its own key on the next call.
    except (OSError, EOFError, UnicodeError, ValueError):
        return {}
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        return {}
    features = document.get("features")
    if not isinstance(features, list):
        return {}
    records, ambiguous = {}, set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            continue
        properties, geometry = feature.get("properties"), feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            continue
        identity = feature.get("id") or properties.get("osm_id")
        if not isinstance(identity, str) or not identity or identity in ambiguous:
            continue
        if properties.get("osm_id") is not None and properties["osm_id"] != identity:
            continue
        if identity in records:
            records.pop(identity)
            ambiguous.add(identity)
            continue
        if (geometry.get("type") not in ("Point", "Polygon", "MultiPolygon")
                or not _coordinates_valid(geometry.get("coordinates"))):
            continue
        try:
            mapped = shape(geometry)
            if mapped.is_empty or not mapped.is_valid:
                continue
            point = mapped if mapped.geom_type == "Point" else mapped.representative_point()
        except (GEOSException, ValueError, KeyError, TypeError, AttributeError):
            continue
        records[identity] = {
            "coordinates": (point.x, point.y),
            "coordinate_source": "osm_point" if mapped.geom_type == "Point" else "osm_geometry_representative_point",
            "properties": {key: properties.get(key) for key in
                           ("name", "amenity", "leisure", "natural", "access", "source_id", "synthetic")},
        }
    return records


def _current(fact, at, hotel_id):
    if (not isinstance(fact, dict) or fact.get("status") not in ("computed", "confirmed")
            or not isinstance(fact.get("id"), str) or not fact["id"]
            or not isinstance(fact.get("value"), dict)
            or hotel_id and fact.get("hotel_id") != hotel_id):
        return False
    deadline = fact.get("valid_until")
    try:
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return (isinstance(deadline, datetime) and deadline.tzinfo is not None
                and deadline.utcoffset() is not None and deadline > at)
    except (ValueError, TypeError, OverflowError):
        return False


def _matches(properties, kind):
    if kind == "pharmacy":
        return properties.get("amenity") == "pharmacy"
    if kind == "parking":
        return properties.get("amenity") == "parking" and properties.get("access") not in ("private", "no")
    if kind == "playground":
        return properties.get("leisure") == "playground"
    if kind == "park":
        return properties.get("leisure") in ("park", "garden") and properties.get("access") not in ("private", "no")
    return kind == "beach" and properties.get("natural") == "beach"


def _source_ids(fact, properties):
    source_ids = set()
    evidence = fact.get("evidence", [])
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("source_ids"), list):
            continue
        source_ids.update(sid for sid in item["source_ids"] if isinstance(sid, str) and sid)
    source = properties.get("source_id")
    if isinstance(source, str) and source:
        source_ids.add(source)
    return sorted(source_ids)


def linked_places(facts: list[dict], geo: dict, data_dir: Path) -> dict:
    """Return up to one point per kind, never inferred nearest places or entrances.

    A current fact with an explicit link is required even for the saved beach
    route fallback. Invalid/missing TTLs and broken snapshots yield no marker.
    Names remain plain strings for the UI's normal text escaping.
    """
    current = datetime.now(UTC)
    geo = geo if isinstance(geo, dict) else {}
    hotel_id = geo.get("hotel_id")
    facts = [fact for fact in facts if _current(fact, current, hotel_id)]
    facts.sort(key=lambda fact: (-(_number(fact.get("confidence")) or 0), fact["id"]))
    features, seen = [], set()
    for cid, field, kind, dataset, distance_field in LINKS:
        for fact in facts:
            if fact.get("characteristic_id") != cid:
                continue
            value = fact["value"]
            identity = value.get(field)
            if cid == "beach_walk" and identity is None:
                route = geo.get("route_details")
                identity = route.get("beach_id") if isinstance(route, dict) else None
            if not isinstance(identity, str) or not identity or identity in seen:
                continue
            path = Path(data_dir)/"seed"/(dataset+".geojson")
            if not path.is_file():
                path = Path(str(path)+".gz")
            try:
                mapped = _index(path.resolve(), _snapshot(path)).get(identity)
            except OSError:
                continue
            if not mapped or not _matches(mapped["properties"], kind):
                continue
            properties = mapped["properties"]
            source_ids = _source_ids(fact, properties)
            if not source_ids:
                continue
            name = properties.get("name")
            props = {"kind": kind, "name": name if isinstance(name, str) and name else NAMES[kind],
                     "osm_id": identity, "characteristic_id": cid, "fact_id": fact["id"],
                     "source_ids": source_ids, "coordinate_source": mapped["coordinate_source"],
                     "coordinate_note": "Картированная точка; вход не подтверждён" if mapped["coordinate_source"] == "osm_point" else "Точка геометрии, не вход",
                     "synthetic": fact.get("synthetic") is True or properties.get("synthetic") is True,
                     "entrance_verified": False, "access_verified": False, "opening_hours_verified": False}
            distance = _number(value.get(distance_field)) if distance_field else None
            if (distance is not None and distance >= 0
                    and value.get("distance_kind") in (None, "straight_to_geometry", "straight_line_to_mapped_geometry")):
                props.update(distance_m=distance, distance_kind="straight_to_geometry")
            features.append({"type": "Feature", "id": kind+":"+identity,
                             "geometry": {"type": "Point", "coordinates": list(mapped["coordinates"])},
                             "properties": props})
            seen.add(identity)
            break
    return {"type": "FeatureCollection", "features": features}
