"""Conservative geographical/name conflation; preserve every input identity."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from pyproj import Geod
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from backend.contracts.models import Hotel
from pipeline.common import CONFIG, RAW, SEED, ZONE_A, ZONE_B, dump, read, table

GEOD = Geod(ellps="WGS84")
CYR = str.maketrans(dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a","b","v","g","d","e","e","zh","z","i","i","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","shch","","y","","e","yu","ya"], strict=False)))


def norm(name):
    s = name.lower().translate(CYR)
    s = re.sub(r"\b(hotel|otel|gostinitsa|gostevoi|dom|guest|house|resort|spa|sochi|sanatorii|sanatorium|apart|apartments)\b", " ", s)
    return "".join(re.findall(r"[a-z0-9]+", s))


def host(url):
    if not url:
        return None
    h = urlparse(url if "://" in url else "https://" + url).netloc.lower().removeprefix("www.")
    return h if h and not any(x in h for x in ["booking.com", "instagram.", "vk.com", "facebook.", "ostrovok.", "yandex.", "google."]) else None


def full_site(url):
    if not url or not host(url):
        return None
    p = urlparse(url if "://" in url else "https://" + url)
    return host(url) + p.path.rstrip("/").lower()


def matches(a, b):
    distance = GEOD.inv(a["lon"], a["lat"], b["lon"], b["lat"])[2]
    rules = CONFIG["merge"]
    if distance > rules["max_distance_m"]:
        return None
    n1, n2 = norm(a["name"]), norm(b["name"])
    similarity = SequenceMatcher(None, n1, n2).ratio()
    exact_site = host(a.get("website")) is not None and host(a.get("website")) == host(b.get("website"))
    same_url = full_site(a.get("website")) is not None and full_site(a.get("website")) == full_site(b.get("website"))
    if (len(n1) >= 3 and n1 == n2 and distance <= rules["exact_name_distance_m"]) or (min(len(n1), len(n2)) >= 4 and similarity >= rules["name_ratio"] and distance <= rules["fuzzy_distance_m"]) or (same_url and similarity >= rules["same_full_website_min_name_ratio"]):
        return {"distance_m": round(distance, 2), "name_similarity": round(similarity, 4), "same_website_host": exact_site, "same_full_website": same_url}
    return None


def stars(value):
    try:
        v = int(value)
        return v if 0 <= v <= 5 else None
    except (TypeError, ValueError):
        return None


def run():
    old_hotels = read(SEED / "hotels.json") if (SEED / "hotels.json").exists() else []
    old_audit = read(SEED / "hotel_merge_audit.json") if (SEED / "hotel_merge_audit.json").exists() else {}
    osm = read(RAW / "osm_hotels.json")
    overture = read(RAW / "overture_places.json")
    merged, decisions = [], []
    for row in sorted(osm, key=lambda r: (0 if r.get("footprint") and r["tags"].get("building") else 1, r["id"])):
        tags = row["tags"]
        candidate = {**row, "website": tags.get("website") or tags.get("contact:website"), "osm_id": row["id"], "overture_id": None,
                     "source_ids": ["osm_geofabrik"], "source_records": [{"source_id": "osm_geofabrik", "id": row["id"], "name": row["name"], "lon": row["lon"], "lat": row["lat"]}]}
        eligible = [(matches(m, candidate), m) for m in merged]
        eligible = [(score, m) for score, m in eligible if score]
        if eligible:
            score, target = max(eligible, key=lambda e: (e[0]["name_similarity"], -e[0]["distance_m"]))
            target["source_records"].extend(candidate["source_records"])
            decisions.append({"from": row["id"], "to": target["id"], "method": "name_geography", **score})
        else:
            merged.append(candidate)
    for row in sorted(overture, key=lambda r: (-(r.get("confidence") or 0), r["id"])):
        name = (row.get("names") or {}).get("primary")
        if not name or row.get("operating_status") in {"permanently_closed", "closed"}:
            continue
        lon, lat = row["geometry"]["coordinates"]
        candidate = {"id": "overture-" + row["id"], "name": name, "lon": lon, "lat": lat, "tags": {}, "footprint": None,
                     "website": (row.get("websites") or [None])[0], "osm_id": None, "overture_id": row["id"], "source_ids": ["overture_places"],
                     "source_records": [{"source_id": "overture_places", "id": row["id"], "name": name, "lon": lon, "lat": lat,
                                         "confidence": row.get("confidence"), "category": row.get("basic_category"), "upstream_sources": row.get("sources")}]}
        eligible = [(matches(m, candidate), m) for m in merged]
        eligible = [(score, m) for score, m in eligible if score]
        if eligible:
            score, target = max(eligible, key=lambda e: (e[0]["name_similarity"], -e[0]["distance_m"]))
            target["source_records"].extend(candidate["source_records"])
            target["source_ids"] = sorted(set(target["source_ids"] + ["overture_places"]))
            target["overture_id"] = target.get("overture_id") or row["id"]
            target["website"] = target.get("website") or candidate["website"]
            decisions.append({"from": candidate["id"], "to": target["id"], "method": "name_geography", **score})
        else:
            merged.append(candidate)
    explicit_exclusions = CONFIG.get("exclude_ids", {})
    reviewed_removed = set(explicit_exclusions)
    for rule in CONFIG.get("identity_merges", []):
        by_id = {h["id"]: h for h in merged}
        target = by_id[rule["canonical"]]
        for member in rule["members"]:
            if member not in by_id:
                continue
            original = by_id[member]
            target["source_records"].extend(original["source_records"])
            target["source_ids"] = sorted(set(target["source_ids"] + original["source_ids"]))
            target["overture_id"] = target.get("overture_id") or original.get("overture_id")
            reviewed_removed.add(member)
            decisions.append({"from": member, "to": target["id"], "method": "reviewed_identity_merge", "reason": rule["reason"]})
    merged = [h for h in merged if h["id"] not in reviewed_removed]
    # Bounded demo portfolio, favouring OSM hotel geometry and corroborated identities.
    def priority(h):
        category = h["tags"].get("tourism") or h["source_records"][-1].get("category")
        return (0 if category == "hotel" else 1, 0 if len(h["source_ids"]) > 1 else 1, 0 if h["osm_id"] else 1, h["id"])
    ordered = sorted(merged, key=priority)
    def inside(h, b):
        return b[0] <= h["lon"] <= b[2] and b[1] <= h["lat"] <= b[3]
    central = [h for h in ordered if inside(h, ZONE_A)]
    adler = [h for h in ordered if inside(h, ZONE_B)]
    limit_a, limit_b = CONFIG["zones"]["A"]["hotel_limit"], CONFIG["zones"]["B"]["hotel_limit"]
    total_limit = limit_a + limit_b
    selected = central[:limit_a] + adler[:limit_b]
    lock_path = SEED / "portfolio_ids.json"
    locked = read(lock_path) if lock_path.exists() else None
    if locked and locked.get("region") == CONFIG["id"]:
        by_id = {h["id"]: h for h in merged}
        assert set(locked["ids"]) - by_id.keys() <= reviewed_removed, "Pinned hotel identity disappeared; explicit portfolio review required"
        selected = [by_id[id] for id in locked["ids"] if id in by_id]
    if len(selected) < total_limit:
        chosen = {h["id"] for h in selected}
        for zone, bbox in [("A", ZONE_A), ("B", ZONE_B)]:
            missing = CONFIG["zones"][zone]["hotel_limit"] - sum(inside(h, bbox) for h in selected)
            extra = [h for h in ordered if h["id"] not in chosen and inside(h, bbox)][:max(0, missing)]
            selected.extend(extra)
            chosen.update(h["id"] for h in extra)
    buildings = read(SEED / "buildings.geojson")["features"]
    geoms = [shape(f["geometry"]) for f in buildings]
    tree = STRtree(geoms)
    hotels = []
    for h in selected:
        tags = h["tags"]
        p = Point(h["lon"], h["lat"])
        containing = [int(i) for i in tree.query(p) if geoms[int(i)].covers(p)]
        b = buildings[min(containing, key=lambda i: geoms[i].area)] if containing else None
        footprint = h.get("footprint") if tags.get("building") else b["geometry"] if b else None
        levels = (b["properties"].get("levels") if b else None) or tags.get("building:levels")
        try:
            floors = int(float(levels)) if levels is not None else None
        except ValueError:
            floors = None
        address = ", ".join(str(tags[k]) for k in ["addr:city", "addr:street", "addr:housenumber"] if tags.get(k)) or CONFIG["name"]
        obj = Hotel(id=h["id"], name=h["name"], lon=h["lon"], lat=h["lat"], address=address, stars=stars(tags.get("stars")),
                    price_rub=None, basic_fields_source="open_data", source_ids=h["source_ids"], osm_id=h["osm_id"], overture_id=h["overture_id"],
                    website=h["website"], floors=floors, footprint=footprint,
                    tags={**tags, "building_id": b["id"] if b else h["id"] if footprint else None,
                          "building_match_method": "point_containment" if b else "osm_hotel_way" if footprint else "unknown",
                          "source_records": h["source_records"], "demo_zone": "B" if inside(h, ZONE_B) else "A", "price_status": "unknown", "room_types_status": "unknown"})
        hotels.append(obj.model_dump(mode="json"))
    hotels.sort(key=lambda h: h["id"])
    dump(SEED / "hotels.json", hotels)
    table("hotels", hotels)
    dump(SEED / "hotels_b.json", [h for h in hotels if h["tags"]["demo_zone"] == "B"])
    selected_ids = {h["id"] for h in hotels}
    dump(lock_path, {"region": CONFIG["id"], "ids": sorted(selected_ids), "selection": "Reviewed region portfolio; real source IDs; explicit identity merges/exclusions in region config"})
    history = old_audit.get("selection_history", [])
    previous = old_audit.get("previous_selection")
    if previous and previous.get("ids_removed") and previous not in history:
        history.append(previous)
    dump(SEED / "hotel_merge_audit.json", {"method": "name_geography_or_same_full_website_with_name_similarity", "thresholds": CONFIG["merge"],
          "osm_inputs": len(osm), "overture_inputs": len(overture), "merged_candidates": len(merged), "selected": len(hotels), "portfolio_limit": total_limit,
          "selection": f"{limit_a} A + {limit_b} B; pinned portfolio_ids.json; hotel category, corroborated identities, OSM identity, stable ID", "merges": decisions,
          "selection_history": history,
          "reviewed_identity_merges": CONFIG.get("identity_merges", []), "explicit_exclusions": explicit_exclusions,
          "zone_counts": {"A": sum(h["tags"]["demo_zone"] == "A" for h in hotels), "B": sum(h["tags"]["demo_zone"] == "B" for h in hotels)},
          "previous_selection": {"ids_retained": sorted({h["id"] for h in old_hotels} & selected_ids),
                                 "ids_removed": sorted({h["id"] for h in old_hotels} - selected_ids),
                                 "ids_added": sorted(selected_ids - {h["id"] for h in old_hotels}),
                                 "reason": "Reviewed regional portfolio and explicit identity merges/exclusions; retained entity IDs do not change"},
          "excluded_by_portfolio_limit": [{k: h[k] for k in ["id", "name", "lat", "lon", "source_ids"]} for h in ordered if h["id"] not in selected_ids]})
    print(f"Hotels: {len(hotels)} selected from {len(merged)} unique candidates; {len(decisions)} identity merges", flush=True)


if __name__ == "__main__":
    run()
