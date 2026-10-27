"""Building history from exact hotel IDs or an explicitly assigned contour."""
from __future__ import annotations

from shapely.geometry import Point, shape

from geo.coordinates import metric

FIELDS = ("start_date", "building:start_date", "construction_date", "heritage", "heritage:operator", "heritage:ref", "historic", "architect", "building:architecture", "old_name", "wikidata", "wikipedia")


def exact_history(hotel, features):
    ids = {hotel["id"], hotel.get("osm_id"), hotel.get("building_id"), hotel.get("tags", {}).get("building_id")}-{None}
    rows = []
    for feature in features:
        if feature["id"] not in ids:
            continue
        properties = feature["properties"]
        fields = {k: properties[k] for k in FIELDS if properties.get(k)}
        if feature["id"] != hotel["id"] and feature["id"] != hotel.get("osm_id"):
            geometry = metric(shape(feature["geometry"]))
            point = metric(Point(hotel["lon"], hotel["lat"]))
            if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.covers(point):
                continue
        if fields:
            rows.append({"osm_id": feature["id"], "identity_method": "exact_hotel_id" if feature["id"] in {hotel["id"], hotel.get("osm_id")} else "assigned_contour_id_and_contains", "tags": fields})
    historic = [r for r in rows if any(k in r["tags"] for k in FIELDS[:-2])]
    date_record = next(((r, k) for r in historic for k in FIELDS[:3] if k in r["tags"]), None)
    return {"available": True if historic else None, "records": rows,
            "inception": date_record[0]["tags"][date_record[1]] if date_record else None,
            "inception_osm_id": date_record[0]["osm_id"] if date_record else None,
            "inception_tag": date_record[1] if date_record else None,
            "date_semantics": "Original OSM tag verbatim: hotel start_date can describe opening, not building construction; no historical-age classification is inferred",
            "explicit_building_date": next((r["tags"][k] for r in historic for k in ("building:start_date", "construction_date") if k in r["tags"]), None),
            "architecture": next((r["tags"]["building:architecture"] for r in historic if "building:architecture" in r["tags"]), None),
            "wikidata_id": next((r["tags"]["wikidata"] for r in rows if "wikidata" in r["tags"]), None)}
