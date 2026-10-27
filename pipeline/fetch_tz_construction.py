"""Reissue only TZ environment ledger with conservative construction eligibility."""
from __future__ import annotations

import gzip
import json
from datetime import datetime

from geo.coordinates import TO_UTM
from geo.io import read_json
from geo.region import load_features
from geo.tz_construction import construction_nearby
from pipeline.common import CONFIG, SEED, sha
from pipeline.tz_common import observation, parameters, spec_input, write_stage


def run():
    meta_path = SEED / "tz_osm_element_metadata.json"
    elements = read_json(meta_path)["elements"]
    features = load_features(SEED / "tz_construction.geojson")
    features += [f for f in load_features(SEED / "tz_areas.geojson") if f["properties"].get("landuse") == "construction" or f["properties"].get("building") == "construction"]
    hotels = {h["id"]: h for h in read_json(SEED / "hotels.json")}
    old_manifest = read_json(SEED / "tz_environment_manifest.json")
    with gzip.open(SEED / "tz_environment_observations.json.gz", "rt") as stream:
        content = json.load(stream)
    rows, thresholds = [], parameters("construction_nearby")
    for row in content["observations"]:
        if row["kind"] != "construction_nearby":
            rows.append(row)
            continue
        hotel = hotels[row["hotel_id"]]
        snapshot_path = SEED.parent / "geo" / (hotel["id"]+".json")
        snapshot = read_json(snapshot_path) if snapshot_path.exists() else {}
        value = construction_nearby(TO_UTM.transform(hotel["lon"], hotel["lat"]), features, elements, snapshot.get("facades", []),
                                    as_of=datetime.fromisoformat(CONFIG["osm"]["snapshot"].replace("Z", "+00:00")).date(), **thresholds)
        value["calculation_parameters"] = thresholds
        row.update(observation(row["hotel_id"], row["kind"], value), method="tz_construction_eligibility@2", confidence=.35,
                   limitations=["Only a recent construction-specific check or explicit survey can pass temporal eligibility; generic element edits and download dates cannot",
                                "Facing facade is a direction/distance proxy; unobstructed visibility, work activity and noise are not measured"])
        row["input_sha256"] = {**row.get("input_sha256", {}), "/data/seed/"+meta_path.name: sha(meta_path)}
        rows.append(row)
    inputs = dict(old_manifest["inputs"])
    inputs.update([spec_input("construction_nearby")])
    return write_stage("environment", rows, read_json(SEED / "tz_environment_sources.json"), inputs=inputs,
                       limitations=[*content["limitations"], "Archived construction tags do not establish current works and must not penalize quiet-night requests"])


if __name__ == "__main__":
    run()
