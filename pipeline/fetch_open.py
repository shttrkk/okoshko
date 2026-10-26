"""Observed/model open data, file-specific photo rights, and exact Wikidata links."""
from __future__ import annotations

import html
import re
from collections import defaultdict

from pipeline.common import CONFIG, RAW, SEED, dump, fetch, read, source


def clean(s):
    return html.unescape(re.sub("<[^>]+>", "", s or "")).strip()


def meteo(region="A"):
    lat, lon, sea_lon = CONFIG["zones"][region]["meteo"]
    params = {"latitude": lat, "longitude": lon, "start_date": CONFIG["meteo"]["archive_start"], "end_date": CONFIG["meteo"]["archive_end"], "timezone": CONFIG["meteo"]["timezone"]}
    specs = [
        ("open_meteo_archive", "https://archive-api.open-meteo.com/v1/archive", {**params, "daily": "temperature_2m_mean,precipitation_sum,sunshine_duration", "models": "era5"}),
        ("open_meteo_marine", "https://marine-api.open-meteo.com/v1/marine", {**params, "longitude": sea_lon, "daily": "sea_surface_temperature_mean"}),
        ("open_meteo_climate", "https://climate-api.open-meteo.com/v1/climate", {**params, "start_date": "2025-01-01", "models": "EC_Earth3P_HR", "daily": "temperature_2m_mean"}),
        ("open_meteo_aq", "https://air-quality-api.open-meteo.com/v1/air-quality", {"latitude": lat, "longitude": lon, "hourly": "pm2_5,european_aqi", "domains": "cams_europe", "start_date": CONFIG["meteo"]["aq_start"], "end_date": CONFIG["meteo"]["aq_end"], "timezone": CONFIG["meteo"]["timezone"]}),
    ]
    sources, downloads, failures, values = [], [], [], {}
    for id, url, p in specs:
        try:
            path, meta = fetch(url, id + ("_b" if region == "B" else "") + ".json", params=p)
            data = read(path)
            if data.get("error"):
                raise ValueError(data)
            downloads.append(meta)
            values[id] = data
            observed = "2025-12-31T00:00:00Z" if id != "open_meteo_aq" else meta["retrieved_at"]
            sources.append(source(id, id.replace("_", " ").title(), meta["url"], "CC-BY-4.0",
                                  "Weather data by Open-Meteo.com; ERA5/Copernicus, CMIP6 or CAMS as identified in dataset", observed, meta["retrieved_at"],
                                  "Noncommercial free API. Model grid estimate, not a hotel sensor; preserve returned grid coordinate and exact temporal coverage."))
            print(id, "downloaded", flush=True)
        except Exception as e:
            failures.append({"source": id, "error": str(e)[:500]})
    monthly = []
    archive = values.get("open_meteo_archive", {}).get("daily", {})
    marine = values.get("open_meteo_marine", {}).get("daily", {})
    for month in range(1, 13):
        record = {"month": month, "period": "2016–2025", "source_ids": [s["id"] for s in sources if s["id"] in {"open_meteo_archive", "open_meteo_marine"}],
                  "air_c": None, "water_c": None, "sunshine_hours": None, "precipitation_mm": None}
        for data, field, target, mode in [(archive, "temperature_2m_mean", "air_c", "mean"), (marine, "sea_surface_temperature_mean", "water_c", "mean"),
                                          (archive, "sunshine_duration", "sunshine_hours", "year_sum_hours"), (archive, "precipitation_sum", "precipitation_mm", "year_sum")]:
            pairs = [(day, v) for day, v in zip(data.get("time", []), data.get(field, []), strict=False) if int(day[5:7]) == month and v is not None]
            if not pairs:
                continue
            record[target + "_period"] = [pairs[0][0], pairs[-1][0]]
            record[target + "_years"] = sorted({int(day[:4]) for day, _ in pairs})
            if mode == "mean":
                value = sum(v for _, v in pairs) / len(pairs)
            else:
                annual = defaultdict(float)
                for day, v in pairs:
                    annual[day[:4]] += v
                value = sum(annual.values()) / len(annual)
                if mode == "year_sum_hours":
                    value /= 3600
            record[target] = round(value, 2)
            record[target + "_sample_days"] = len(pairs)
        monthly.append(record)
        air_years, water_years = record.get("air_c_years", []), record.get("water_c_years", [])
        record["period"] = "; ".join(label + " " + str(years[0]) + "–" + str(years[-1]) for label, years in [("air", air_years), ("water", water_years)] if years)
    aq = values.get("open_meteo_aq", {}).get("hourly", {})
    aq_summary = {"model": "CAMS Europe", "period": [aq.get("time", [None])[0], aq.get("time", [None])[-1]], "source_id": "open_meteo_aq"}
    for field in ["pm2_5", "european_aqi"]:
        v = [x for x in aq.get(field, []) if x is not None]
        aq_summary[field] = round(sum(v) / len(v), 3) if v else None
        aq_summary[field + "_samples"] = len(v)
    projection = values.get("open_meteo_climate", {}).get("daily", {})
    projection_months = []
    for month in range(1, 13):
        vals = [v for d, v in zip(projection.get("time", []), projection.get("temperature_2m_mean", []), strict=False) if int(d[5:7]) == month and v is not None]
        projection_months.append({"month": month, "air_c": round(sum(vals) / len(vals), 2) if vals else None, "sample_days": len(vals)})
    dump(SEED / ("open_meteo_b.json" if region == "B" else "open_meteo.json"), {"region": region, "climate_months": monthly, "air_quality": aq_summary,
        "grids": {k: {field: v.get(field) for field in ["latitude", "longitude", "elevation", "timezone"]} for k, v in values.items()},
        "climate_projection": {"model": "EC_Earth3P_HR", "period": "2025", "kind": "CMIP6 model projection, not observed climate", "available": "open_meteo_climate" in values, "months": projection_months},
        "limitations": ["2016–2025 monthly averages are a 10-year sample, not the WMO 30-year climate normal.", "City-scale model grids do not measure hotel conditions.", "AQ window is a recent model sample, not seasonal air quality."], "failures": failures})
    return sources, downloads, failures


def commons(region="A"):
    url = "https://commons.wikimedia.org/w/api.php"
    coord = CONFIG["zones"][region]["commons"]
    params = {"action": "query", "format": "json", "generator": "geosearch", "ggscoord": str(coord[0]) + "|" + str(coord[1]), "ggsradius": 10000,
              "ggsnamespace": 6, "ggslimit": 50, "prop": "imageinfo|coordinates", "iiprop": "url|extmetadata", "iiurlwidth": 640, "colimit": "max"}
    path, meta = fetch(url, "commons_geosearch" + ("_b" if region == "B" else "") + ".json", params=params)
    photos = []
    for p in read(path).get("query", {}).get("pages", {}).values():
        info = (p.get("imageinfo") or [{}])[0]
        ext = info.get("extmetadata", {})
        def val(k, metadata=ext):
            return clean(metadata.get(k, {}).get("value", ""))
        license, author = val("LicenseShortName"), val("Artist")
        if not license or not author or not info.get("thumburl"):
            continue
        coord = (p.get("coordinates") or [{}])[0]
        photo = {"id": "commons-" + str(p["pageid"]), "title": p["title"], "author": author, "license": license,
                 "license_url": val("LicenseUrl"), "source_url": info.get("descriptionurl"), "url": info.get("url"), "thumbnail": info.get("thumburl"),
                 "lat": coord.get("lat"), "lon": coord.get("lon"), "source_id": "wikimedia_commons", "local_thumbnail": None,
                 "attribution_required": val("AttributionRequired"), "credit": val("Credit"), "description": val("ImageDescription")}
        photos.append(photo)
    # Offline assets with individual attribution; image contents are not used as hotel evidence.
    for photo in sorted(photos, key=lambda p: (0 if re.search("sochi|сочи|порт|морск|олимп|adler|адлер", p["title"], flags=re.I) else 1, p["id"]))[:CONFIG["photos"]["offline_thumbnails_per_zone"]]:
        try:
            thumb, _ = fetch(photo["thumbnail"], photo["id"] + ".jpg")
            destination = SEED / "photos" / thumb.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(thumb.read_bytes())
            photo["local_thumbnail"] = "/data/seed/photos/" + thumb.name
        except Exception as e:
            photo["thumbnail_download_error"] = str(e)[:200]
    dump(SEED / ("nearby_photos_a.json" if region == "A" else "nearby_photos_b.json"), {"photos": sorted(photos, key=lambda p: p["id"]), "limitations": ["Nearby images are contextual photographs, not a verified view from a hotel room."]})
    if not photos:
        raise ValueError("Commons returned no photos with author and file-specific license")
    return source("wikimedia_commons", "Wikimedia Commons geotagged files", meta["url"], "Per-file license (see nearby_photos.json)",
                  "Wikimedia Commons photographers; file-specific author/license/source_url preserved", meta["retrieved_at"], meta["retrieved_at"],
                  "Photos selected by geographic coordinates. License and author fetched from each file imageinfo.extmetadata."), meta


def wikidata():
    hotels = read(SEED / "hotels.json")
    qids = sorted({h["tags"]["wikidata"] for h in hotels if re.fullmatch("Q[0-9]+", h["tags"].get("wikidata", ""))})
    if not qids:
        raise ValueError("No exact Wikidata links on selected OSM hotels; building history stays unknown")
    path, meta = fetch("https://www.wikidata.org/w/api.php", "wikidata_entities.json", params={"action": "wbgetentities", "format": "json", "ids": "|".join(qids), "props": "labels|claims", "languages": "ru|en"})
    entities = read(path).get("entities", {})
    histories = []
    for h in hotels:
        qid = h["tags"].get("wikidata")
        e = entities.get(qid)
        if not e:
            continue
        claims = e.get("claims", {})
        def values(prop, statements=claims):
            return [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in statements.get(prop, []) if c.get("mainsnak", {}).get("datavalue")]
        histories.append({"hotel_id": h["id"], "wikidata_id": qid, "source_id": "wikidata", "url": "https://www.wikidata.org/wiki/" + qid,
                          "identity_method": "explicit_osm_wikidata_tag", "inception": values("P571"), "architecture": values("P149"), "heritage": values("P1435"),
                          "labels": e.get("labels"), "unknown": not bool(values("P571") or values("P149"))})
    dump(SEED / "building_history.json", {"hotels": histories})
    if not histories:
        raise ValueError("Wikidata entities unavailable")
    return source("wikidata", "Wikidata linked hotel entities", meta["url"], "CC0-1.0", "Wikidata contributors (CC0)", meta["retrieved_at"], meta["retrieved_at"],
                  "Only explicit OSM Wikidata links are used; nearby unrelated entities are never treated as hotel history."), meta


def run():
    sources, downloads, failures = meteo()
    sb, db, fb = meteo("B")
    sources.extend(sb)
    downloads.extend(db)
    failures.extend(fb)
    for name, fn in [("wikimedia_commons", commons), ("wikimedia_commons_b", lambda: commons("B")), ("wikidata", wikidata)]:
        try:
            s, m = fn()
            sources.append(s)
            downloads.append(m)
            print(name, "downloaded and used", flush=True)
        except Exception as e:
            failures.append({"source": name, "error": str(e)[:500]})
            print(name, str(e)[:300], flush=True)
    photo_records = {}
    for filename in ["nearby_photos_a.json", "nearby_photos_b.json"]:
        if (SEED / filename).exists():
            for photo in read(SEED / filename)["photos"]:
                photo_records[photo["id"]] = photo
    dump(SEED / "nearby_photos.json", {"photos": sorted(photo_records.values(), key=lambda p: p["id"]), "limitations": ["Nearby geotagged photographs do not prove the hotel appearance or room view."]})
    dump(RAW / "open_stage.json", {"sources": sources, "downloads": downloads, "failures": failures})


if __name__ == "__main__":
    run()
