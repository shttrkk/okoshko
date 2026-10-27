"""Fetch only two daily ERA5 archives; reuse the original SST observations."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from geo.io import read_json
from geo.tz_climate import climate_days, water_comfort
from pipeline.common import CONFIG, SEED, fetch, gzip_json, sha, source
from pipeline.tz_common import baseline_sources, observation, parameters, spec_input, write_stage


def run():
    hotels = read_json(SEED / "hotels.json")
    rows, sources, inputs = [], [], dict([spec_input("climate_days")])
    old_sources = baseline_sources()
    original_manifest = read_json(SEED / "manifest.json")
    inputs["/data/seed/manifest.json"] = sha(SEED / "manifest.json")
    thresholds = parameters("climate_days")
    for zone, config in CONFIG["zones"].items():
        lat, lon, _ = config["meteo"]
        path, meta = fetch("https://archive-api.open-meteo.com/v1/archive", f"tz_daily_{zone}.json", params={
            "latitude": lat, "longitude": lon, "start_date": CONFIG["meteo"]["archive_start"], "end_date": CONFIG["meteo"]["archive_end"],
            "daily": "sunshine_duration,precipitation_sum", "models": "era5", "timezone": CONFIG["meteo"]["timezone"]})
        raw = read_json(path)
        if raw.get("daily_units", {}).get("sunshine_duration") != "s" or raw.get("daily_units", {}).get("precipitation_sum") != "mm":
            raise ValueError("unexpected daily archive units")
        daily_path = SEED / f"tz_daily_{zone.lower()}.json.gz"
        gzip_json(daily_path, {"zone": zone, "download": meta, "license": "CC-BY-4.0", "attribution": "Weather data by Open-Meteo.com; ERA5/Copernicus",
                               "data": raw, "semantics": "One representative grid point per zone, not independent hotel observations"})
        source_id = f"tz_open_meteo_daily_{zone.lower()}"
        sources.append(source(source_id, f"Open-Meteo daily ERA5 zone {zone}", meta["url"], "CC-BY-4.0", "Weather data by Open-Meteo.com; ERA5 / Copernicus", "2025-12-31T00:00:00Z", meta["retrieved_at"], "License https://open-meteo.com/en/licence verified; free API noncommercial. Historical 2016–2025 model grid, not hotel observations or a WMO 30-year normal."))
        old_path = SEED / ("open_meteo_b.json" if zone == "B" else "open_meteo.json")
        old_data = read_json(old_path)
        old = {v["month"]: v for v in old_data["climate_months"]}
        marine_metadata = next((d for d in original_manifest["stages"]["open"]["downloads"]
                                if urlsplit(d["url"]).hostname == "marine-api.open-meteo.com" and parse_qs(urlsplit(d["url"]).query).get("latitude") == [str(lat)]), None)
        marine_source_id = f"tz_open_meteo_sst_{zone.lower()}"
        if marine_metadata is None:
            raise ValueError("no exact original marine download provenance for zone")
        sources.append({**old_sources["open_meteo_marine"], "id": marine_source_id, "name": f"Original Open-Meteo marine SST zone {zone}",
                        "url": marine_metadata["url"], "retrieved_at": marine_metadata["retrieved_at"],
                        "notes": "Original immutable zonal marine aggregate and exact original download metadata; not the air-temperature series. CC BY 4.0 verified at https://open-meteo.com/en/licence"})
        for month in climate_days(raw["daily"], **{k: thresholds[k] for k in ("sunny_hours", "rainy_mm", "wet_month_days")}):
            water = old[month["month"]]
            value = {**month, "zone": zone, "period": [CONFIG["meteo"]["archive_start"], CONFIG["meteo"]["archive_end"]],
                     "daily_sample_ref": "/data/seed/"+daily_path.name, "daily_sample_days": len(raw["daily"].get("time", [])),
                     "water_c": water.get("water_c"), "water_period": water.get("water_c_period"), "water_sample_days": water.get("water_c_sample_days"),
                     "water_source_id": marine_source_id, "water_download": marine_metadata, "water_grid": old_data.get("grids", {}).get("open_meteo_marine"),
                     "water_comfort": water_comfort(water.get("water_c"), comfortable_c=thresholds["comfortable_c"], cool_c=thresholds["cool_c"]),
                     "water_comfortable_c": thresholds["comfortable_c"], "water_cool_c": thresholds["cool_c"], "calculation_parameters": thresholds,
                     "archive_sha256": meta["sha256"], "archive_url": meta["url"], "retrieved_at": meta["retrieved_at"],
                     "grid": {k: raw.get(k) for k in ("latitude", "longitude", "elevation", "timezone")}}
            for hotel in hotels:
                if hotel.get("tags", {}).get("demo_zone") == zone:
                    rows.append(observation(hotel["id"], "climate_days", value, scope={"type": "hotel", "month": month["month"]},
                                source=source_id, source_ids=[source_id, marine_source_id], observed_at=meta["retrieved_at"], ttl_days=365, confidence=.7))
                    rows[-1].update(related_artifacts=["/data/seed/"+daily_path.name], input_sha256={"/data/seed/"+daily_path.name: sha(daily_path), "/data/seed/"+old_path.name: sha(old_path)})
    return write_stage("climate", rows, sources, inputs=inputs, limitations=["Две общие зоновые выборки ERA5 дают 3000 производных hotel-month записей, а не 3000 независимых наблюдений у отелей", "Солнечный день: не менее 6 часов солнечного сияния; дождливый: осадки ≥1 мм", "Месяц с ≥12 дождливыми днями — условный влажный месяц, не официальный сезон дождей", "Считаются только полные месяцы лет; 2016–2025 — модельная десятилетняя выборка, не прогноз поездки", "Температура воды взята только из сохранённого marine SST с собственным периодом; воздух её не заменяет", "Пороги комфорта воды 22 и 18 °C субъективны и не гарантируют безопасность купания"])


if __name__ == "__main__":
    run()
