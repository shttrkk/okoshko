"""Conservative current-enough construction evidence; archived tags stay unknown."""
from __future__ import annotations

import calendar
from datetime import date, datetime

from shapely.geometry import Point, shape
from shapely.ops import nearest_points

from geo.coordinates import angle_difference, azimuth, metric


def date_interval(value):
    """Preserve imprecise OSM dates as intervals; do not invent a day."""
    try:
        text = str(value)
        if len(text) == 4 and text.isdigit():
            year = int(text)
            return date(year, 1, 1), date(year, 12, 31)
        if len(text) == 7 and text[4] == "-":
            year, month = map(int, text.split("-"))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        if len(text) == 10:
            parsed = date.fromisoformat(text)
            return parsed, parsed
    except (ValueError, TypeError):
        pass
    return None


def current_evidence(tags, metadata, as_of, *, max_verification_age_days=90):
    reasons, strong, dates = [], [], {}
    for key in ("check_date:construction", "construction:check_date", "survey:date", "check_date", "start_date", "end_date", "opening_date"):
        interval = date_interval(tags.get(key))
        if tags.get(key):
            dates[key] = {"raw": tags[key], "interval": [d.isoformat() for d in interval] if interval else None}
        if key in {"check_date:construction", "construction:check_date", "survey:date"} and interval:
            if interval[1] > as_of:
                reasons.append("verification_date_future_or_imprecise")
            elif (as_of-interval[0]).days <= max_verification_age_days:
                strong.append(key)
            else:
                reasons.append("verification_too_old")
    ended = date_interval(tags.get("end_date"))
    opening = date_interval(tags.get("opening_date"))
    start = date_interval(tags.get("start_date"))
    if ended and ended[1] < as_of:
        reasons.append("explicit_end_date_passed")
    if opening and opening[1] < as_of:
        reasons.append("expected_opening_date_passed_not_proof_of_work")
    if start and start[0] > as_of:
        reasons.append("start_date_in_future")
    if not strong:
        reasons.append("no_recent_construction_survey")
    timestamp = metadata.get("element_timestamp")
    try:
        edited = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
        age = (as_of-edited).days
        if age < 0:
            reasons.append("element_timestamp_after_snapshot")
        if any(date_interval(tags[key])[1] > edited for key in strong):
            reasons.append("verification_date_after_element_edit")
    except (ValueError, TypeError, AttributeError):
        age = None
        reasons.append("element_metadata_missing")
    return {"eligible_temporally": bool(strong) and not reasons, "verification_keys": strong, "dates": dates,
            "reasons": sorted(set(reasons)), "element_timestamp": timestamp, "element_version": metadata.get("version"),
            "element_edit_age_days": age, "construction_tag_changed_at": None,
            "weak_generic_check_date": tags.get("check_date"), "weak_element_edit_recent": age is not None and 0 <= age <= max_verification_age_days,
            "semantics": "A generic check_date, last element edit, future schedule or fresh download alone never establishes current construction"}


def construction_nearby(xy, features, metadata, facades, *, as_of, radius_m=500, facade_distance_m=150, facade_sector_deg=60, max_verification_age_days=90):
    point, candidates = Point(xy), []
    for feature in features:
        geometry = feature["shape"]
        distance = geometry.distance(point)
        if distance > radius_m:
            continue
        temporal = current_evidence(feature["properties"], metadata.get(feature["id"], {}), as_of,
                                    max_verification_age_days=max_verification_age_days)
        sides = []
        for facade in facades:
            line = metric(shape(facade["geometry"]))
            origin, target = nearest_points(line, geometry)
            separation = origin.distance(target)
            delta = angle_difference(facade["azimuth_deg"], azimuth(target.x-origin.x, target.y-origin.y)) if separation > 0 else None
            facing = bool(delta is not None and delta <= facade_sector_deg)
            sides.append({"facade": facade["code"], "distance_m": round(float(separation), 2), "normal_delta_deg": round(float(delta), 2) if delta is not None else None,
                          "facing": facing, "close": separation <= facade_distance_m,
                          "visibility": "not_measured_direction_only", "eligible_geometry": facing and separation <= facade_distance_m and not geometry.covers(point)})
        geometry_ok = any(side["eligible_geometry"] for side in sides)
        reasons = temporal["reasons"][:]
        if not facades:
            reasons.append("hotel_facades_unknown")
        elif not geometry_ok:
            reasons.append("no_close_facing_facade")
        if geometry.covers(point):
            reasons.append("construction_polygon_contains_hotel_not_a_localized_neighbour")
        eligible = temporal["eligible_temporally"] and geometry_ok
        candidates.append({"osm_id": feature["id"], "hotel_distance_m": round(distance, 2), "temporal": temporal,
                           "facades": sides, "eligible_current": eligible, "exclusion_reasons": sorted(set(reasons)),
                           "geometry_ref": "/data/seed/tz_areas.geojson.gz" if feature["id"].startswith("osm-relation-") else "/data/seed/tz_construction.geojson.gz", "geometry_type": geometry.geom_type})
    current = [row for row in candidates if row["eligible_current"]]
    return {"available": True if current else None, "as_of": as_of.isoformat(), "mapped_count": len(candidates), "current_eligible_count": len(current),
            "archive_only_count": len(candidates)-len(current), "current_osm_ids": [r["osm_id"] for r in current],
            "radius_m": radius_m, "facade_distance_m": facade_distance_m, "facade_sector_deg": facade_sector_deg,
            "max_verification_age_days": max_verification_age_days, "candidates": sorted(candidates, key=lambda r: (r["hotel_distance_m"], r["osm_id"])),
            "absence_semantics": "No current-enough evidence means unknown; archived objects do not imply current work/noise",
            "visibility_semantics": "Facade bearing and distance only; no measured site activity, noise or unobstructed view is claimed"}
