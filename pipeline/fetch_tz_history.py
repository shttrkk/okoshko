"""Exact OSM-building Wikidata links only; never a name/proximity search."""
from __future__ import annotations

import re

from geo.io import read_json
from geo.tz_history import exact_history
from pipeline.common import SEED, fetch, sha, source, utcnow
from pipeline.tz_common import observation, write_stage

PROPERTIES = {"P571": "inception", "P1435": "heritage", "P149": "architecture", "P84": "architect"}


def entity_history(entity):
    values = {}
    for property_id, field in PROPERTIES.items():
        rows = []
        for claim in entity.get("claims", {}).get(property_id, []):
            if claim.get("rank") == "deprecated":
                continue
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") == "value":
                rows.append({"value": snak.get("datavalue", {}).get("value"), "statement_id": claim.get("id"),
                             "rank": claim.get("rank"), "references": claim.get("references", [])})
        values[field] = rows
    return values


def run():
    hotels = read_json(SEED / "hotels.json")
    features = read_json(SEED / "tz_history.geojson")["features"]
    matches = [(h, exact_history(h, features)) for h in hotels]
    qids = sorted({r["tags"]["wikidata"] for _, v in matches for r in v["records"] if re.fullmatch(r"Q[1-9][0-9]*", r["tags"].get("wikidata", ""))})
    downloads, entities, errors = [], {}, []
    if qids:
        query = "SELECT ?item ?inception ?heritage ?architecture ?architect WHERE { VALUES ?item { "+" ".join("wd:"+qid for qid in qids)+" } OPTIONAL { ?item wdt:P571 ?inception } OPTIONAL { ?item wdt:P1435 ?heritage } OPTIONAL { ?item wdt:P149 ?architecture } OPTIONAL { ?item wdt:P84 ?architect } }"
        try:
            _, meta = fetch("https://query.wikidata.org/sparql", "tz_building_history_sparql.json", params={"query": query, "format": "json"})
            downloads.append(meta)
        except Exception as error:
            errors.append({"endpoint": "WDQS", "error": str(error)[:200]})
        # Entity JSON keeps statement identifiers, ranks and reference snaks that
        # a truthy SPARQL binding would lose, without widening identity matching.
        for qid in qids:
            try:
                path, meta = fetch(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", f"tz_wikidata_{qid}.json")
                entity = read_json(path).get("entities", {}).get(qid, {})
                if entity:
                    entities[qid] = {"properties": entity_history(entity), "url": meta["url"], "sha256": meta["sha256"], "retrieved_at": meta["retrieved_at"], "lastrevid": entity.get("lastrevid")}
                    downloads.append(meta)
            except Exception as error:
                errors.append({"qid": qid, "error": str(error)[:200]})
    rows = []
    for hotel, match in matches:
        linked = [{"wikidata_id": r["tags"]["wikidata"], **entities[r["tags"]["wikidata"]], "identity_osm_id": r["osm_id"], "identity_method": r["identity_method"]}
                  for r in match["records"] if r["tags"].get("wikidata") in entities]
        known = any(any(e["properties"].values()) for e in linked)
        rows.append(observation(hotel["id"], "building_history", {"available": True if known else None, "wikidata": linked},
                                source="tz_wikidata_history", source_ids=["tz_wikidata_history", "tz_osm_geofabrik"],
                                evidence_kind="open_data", emit_claim=known, ttl_days=180,
                                limitations=["Wikidata P571 is inception of the linked entity; it is not automatically the building's construction date"]))
    sources = []
    if downloads:
        sources = [source("tz_wikidata_history", "Wikidata exact OSM-linked history", "https://www.wikidata.org/", "CC0-1.0", "Wikidata contributors, structured data CC0", utcnow(), max(d["retrieved_at"] for d in downloads), "Verified https://www.wikidata.org/wiki/Wikidata:Licensing; no fuzzy/name/nearby identity matching. WDQS plus exact entity JSON with statement references.")]
        sources += read_json(SEED / "tz_osm_sources.json")
    manifest = write_stage("history", rows, sources, inputs={"/data/seed/tz_history.geojson.gz": sha(SEED / "tz_history.geojson.gz"), "/data/seed/hotels.json": sha(SEED / "hotels.json")},
                           limitations=["No matching/licensed Ministry of Culture building dataset was verified; it is not used", "No Wikidata history statement does not mean the building has no history"])
    from pipeline.common import dump
    manifest.update(downloads=downloads, errors=errors, exact_qids=qids)
    dump(SEED / "tz_history_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    run()
