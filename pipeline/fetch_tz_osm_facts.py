"""Additional OSM computations, kept separate from the runtime seed loader."""
from __future__ import annotations

from datetime import datetime

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import load_features
from geo.tz_airport import airport_exposure, select_airport_runways
from geo.tz_construction import construction_nearby
from geo.tz_environment import car_arrival, mapped_nearby
from geo.tz_history import exact_history
from pipeline.common import CONFIG, SEED, sha
from pipeline.tz_common import baseline_sources, observation, parameters, spec_input, write_stage


def run():
    names = ["aeroways", "construction", "entrances", "vehicle_roads", "communication_masts", "history"]
    layers = {n: load_features(SEED / f"tz_{n}.geojson") for n in names}
    areas = load_features(SEED / "tz_areas.geojson")
    construction_metadata = read_json(SEED / "tz_osm_element_metadata.json")["elements"]
    airport_thresholds = parameters("airport_noise")
    runways = select_airport_runways([r for r in layers["aeroways"] if r["properties"].get("aeroway") == "runway"], areas, iata=airport_thresholds["airport_iata"])
    layers["construction"] += [f for f in areas if f["properties"].get("landuse") == "construction" or f["properties"].get("building") == "construction"]
    inputs = {"/data/seed/"+p.name: sha(p) for n in names for p in [SEED / f"tz_{n}.geojson.gz"]}
    inputs["/data/seed/hotels.json"] = sha(SEED / "hotels.json")
    inputs["/data/seed/pois.geojson.gz"] = sha(SEED / "pois.geojson.gz")
    inputs["/data/seed/tz_areas.geojson.gz"] = sha(SEED / "tz_areas.geojson.gz")
    for kind in ("airport_noise", "construction_nearby", "mobile_masts", "car_arrival"):
        inputs.update([spec_input(kind)])
    hotels, rows = read_json(SEED / "hotels.json"), []
    history = [f["feature"] for f in layers["history"]]
    pois = load_features(SEED / "pois.geojson")
    parking = [p for p in pois if p["properties"].get("amenity") == "parking"]
    for hotel in hotels:
        xy = TO_UTM.transform(hotel["lon"], hotel["lat"])
        path = SEED.parent / "geo" / (hotel["id"]+".json")
        snapshot = read_json(path) if path.exists() else {}
        facades = snapshot.get("facades", [])
        values = {
            "airport_noise": airport_exposure(xy, runways, facades=facades,
                extension_m=airport_thresholds["corridor_extension_m"], half_width_m=airport_thresholds["corridor_half_width_m"], nearby_m=airport_thresholds["nearby_m"]),
            "construction_nearby": construction_nearby(xy, layers["construction"], construction_metadata, facades,
                as_of=datetime.fromisoformat(CONFIG["osm"]["snapshot"].replace("Z", "+00:00")).date(), **parameters("construction_nearby")),
            "mobile_masts": mapped_nearby(xy, layers["communication_masts"], radius_m=parameters("mobile_masts")["radius_m"]),
            "car_arrival": car_arrival(hotel, layers["vehicle_roads"], layers["entrances"], parking, threshold_m=parameters("car_arrival")["arrival_threshold_m"]),
            "building_history": exact_history(hotel, history),
        }
        for kind, value in values.items():
            if value is not None and kind != "building_history":
                value["calculation_parameters"] = parameters(kind)
            rows.append(observation(hotel["id"], kind, value, source_ids=["tz_osm_geofabrik", "osm_geofabrik"],
                        observed_at=CONFIG["osm"]["snapshot"], ttl_days=14 if kind == "construction_nearby" else 180 if kind == "building_history" else 90,
                        emit_claim=not (kind == "building_history" and not value["records"]),
                        method="tz_construction_eligibility@2" if kind == "construction_nearby" else "tz_environment@1",
                        confidence=.35 if kind == "construction_nearby" else .55,
                        input_sha256={**({"/data/geo/"+path.name: sha(path)} if facades else {}),
                                      **({"/data/seed/tz_osm_element_metadata.json": sha(SEED / "tz_osm_element_metadata.json")} if kind == "construction_nearby" else {})},
                        evidence_kind="open_data" if kind == "building_history" else "calculation"))
    sources = read_json(SEED / "tz_osm_sources.json")+[baseline_sources()["osm_geofabrik"]]
    return write_stage("environment", rows, sources, inputs=inputs, limitations=[
        "OSM snapshot 2026-10-25: omissions do not establish absence; construction may be outdated",
        "Airport: runway-axis extensions 6 km at each end, half-width 500 m, nearby runway threshold 3 km are declared geometric assumptions; no dB, flight counts or night schedule",
        "Mast counts/radio tags do not establish coverage, LTE/5G or internet quality",
        "Car arrival is geometric proximity to a mapped entrance or assigned contour; it does not prove a drivable driveway or an open gate",
        "Private hotel parking requires exact operator/name linkage or customer parking strictly within the assigned hotel footprint; missing link is unknown",
        "History uses only exact hotel ID or assigned contour ID with point containment; dates and architecture are retained verbatim, never guessed"])


if __name__ == "__main__":
    run()
