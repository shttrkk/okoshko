"""Read-only evidence adapters over shipped OSM and numeric horizon artifacts.

``compute_trait`` never writes artifacts, uses the network, or invents missing
tags. It returns Claim-compatible dictionaries for the Registry to validate and
attach to its run. The pipeline can materialize the same results separately.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from geo.coordinates import TO_UTM, hotel_center
from geo.facades import extract_facades
from geo.noise import noise_facade, source_samples
from geo.region import get_region
from geo.runtime_cache import input_digest
from geo.sun import facade_exposure, monthly_exposure_paths

TRAITS = frozenset({"morning_sun", "evening_heat", "railway_noise", "beach_surface",
                    "accessible_route", "green_nearby"})
METHODS = {"morning_sun": "astral_morning_horizon_proxy_v1",
           "evening_heat": "astral_evening_exposure_proxy_v1",
           "railway_noise": "outdoor_rail_noise_proxy_v1",
           "beach_surface": "osm_beach_surface_v1",
           "accessible_route": "osm_route_accessibility_v1",
           "green_nearby": "osm_green_geometry_distance_v1"}
SOLAR_LIMITATIONS = [
    "Геометрическая оценка по сохранённому профилю горизонта; облачность и погода не моделируются",
    "Репрезентативная дата — 15-е число месяца; окно — первые/последние три часа светового дня",
    "Наличие окна, стеклопакет, шторы, вентиляция и кондиционер неизвестны",
    "Индекс экспозиции — безразмерный модельный прокси прямого солнца, не температура и не прогноз нагрева комнаты",
    "Использован средний профиль точек фасада; отдельного Monte Carlo для этой характеристики нет",
]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _observation(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("input observation must include a timezone")
    return parsed


def _claim(hotel_id, characteristic, value, scope, confidence, observed_at, *, artifact,
           source_ids, inputs, limitations, synthetic=False, kind="calculation"):
    observed = _observation(observed_at)
    record = {"hotel_id": hotel_id, "characteristic_id": characteristic, "spec_version": 1,
              "scope": scope, "value": value, "source": "geo_engine", "method": METHODS[characteristic],
              "confidence": round(float(np.clip(confidence, 0, 1)), 4),
              "observed_at": observed.isoformat(), "valid_until": (observed+timedelta(days=90)).isoformat(),
              "synthetic": bool(synthetic)}
    # Input identity is part of the claim identity even when outputs are equal.
    identity = {**record, "input_identity": inputs, "source_ids": sorted(set(source_ids))}
    record["id"] = "geo-trait-"+hashlib.sha256(_canonical(identity).encode()).hexdigest()[:24]
    record["evidence"] = [{"id": "ev-"+record["id"], "kind": "simulation" if synthetic else kind,
                           "label": "Открытые геоданные: "+characteristic,
                           "source_ids": sorted(set(source_ids)), "artifact": artifact,
                           "inputs": inputs, "output": value, "limitations": limitations}]
    return record


def _geo_path(hotel, data_dir, geo_artifact=None):
    """Resolve only an explicitly selected, hotel-owned geo artifact.

    Registry supplies the authoritative change-event reference. We never scan
    for the newest variant, since an unused/abandoned scenario is not state.
    """
    if geo_artifact is None:
        path = data_dir/"geo"/(hotel["id"]+".json")
    else:
        text = str(geo_artifact)
        if text.startswith("/data/geo/"):
            path = data_dir/"geo"/text.removeprefix("/data/geo/")
        elif text.startswith("/data/runtime/geo/"):
            path = data_dir.parent/"runtime"/"geo"/text.removeprefix("/data/runtime/geo/")
        else:
            path = Path(geo_artifact)
    path = path.resolve()
    allowed = {(data_dir/"geo").resolve(), (data_dir/"runtime"/"geo").resolve(),
               (data_dir.parent/"runtime"/"geo").resolve()}
    name = path.name
    if path.parent not in allowed or path.suffix != ".json" or not (
            name == hotel["id"]+".json" or name.startswith(hotel["id"]+"-change-")):
        raise ValueError("geo artifact must belong to this hotel in an approved geo directory")
    return path


def _baseline(hotel, data_dir, geo_artifact=None):
    path = _geo_path(hotel, data_dir, geo_artifact)
    document = json.loads(path.read_text()) if path.is_file() else None
    if document is not None and document.get("hotel_id") != hotel["id"]:
        return None
    return document


def solar_floor_ranges(outputs, characteristic):
    """Compact only contiguous floors with the same declared predicate outcome.

    Keep actual extrema, with the upper exposure bound used for an LTE search.
    The default heat threshold (.35) also partitions ranges, so a range cannot
    hide floors on opposite sides of the characteristic's default predicate.
    """
    def key(result):
        if result is None:
            return None
        return (result["visible_minutes"] >= 20,
                result["exposure_index"] <= .35 if characteristic == "evening_heat" else None)

    start = 0
    while start < len(outputs):
        if outputs[start] is None:
            start += 1
            continue
        stop = start+1
        while stop < len(outputs) and key(outputs[stop]) == key(outputs[start]):
            stop += 1
        segment = outputs[start:stop]
        minutes = [result["visible_minutes"] for result in segment]
        exposure = [result["exposure_index"] for result in segment]
        yield start+1, stop, {"available": key(outputs[start])[0],
                              "visible_minutes": min(minutes),
                              "visible_minutes_min": min(minutes), "visible_minutes_max": max(minutes),
                              "exposure_index": max(exposure),
                              "exposure_index_min": min(exposure), "exposure_index_max": max(exposure),
                              "value_semantics": "minutes_lower_bound_exposure_upper_bound_across_floor_range"}
        start = stop


def _solar(hotel, data_dir, characteristic, month, geo_artifact=None):
    path = _geo_path(hotel, data_dir, geo_artifact)
    baseline = _baseline(hotel, data_dir, geo_artifact)
    npz = path.with_suffix(".npz")
    if not baseline or not npz.is_file() or not baseline.get("facades"):
        return [], ["Нет сохранённого пофасадного профиля горизонта; солнце неизвестно"]
    if not isinstance(baseline.get("computed_at"), str) or any(
            facade.get("code") not in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
            or not isinstance(facade.get("azimuth_deg"), int | float)
            or not np.isfinite(facade["azimuth_deg"]) for facade in baseline["facades"]):
        return [], ["Неподдержанные метаданные выбранного профиля горизонта; солнце неизвестно"]
    if baseline.get("floors", {}).get("count") is None:
        return [], ["Нет известной этажности связанного здания; условный уровень глаза не считаем этажами отеля"]
    if not isinstance(baseline["floors"]["count"], int) or not 1 <= baseline["floors"]["count"] <= 100:
        return [], ["Некорректная этажность выбранного профиля горизонта; солнце неизвестно"]
    # A changed regional snapshot invalidates a saved horizon, even if the file
    # name stayed the same. Do not silently combine incompatible generations.
    for artifact, digest in baseline.get("inputs", {}).items():
        if input_digest(data_dir/artifact.removeprefix("/data/")) != digest:
            return [], ["Исходные геоданные изменились: сохранённый профиль горизонта требует пересчёта"]
    period = "morning" if characteristic == "morning_sun" else "evening"
    lon, lat = hotel_center(hotel)
    observed = baseline["computed_at"]
    try:
        paths = monthly_exposure_paths(lon, lat, period=period, year=_observation(observed).year)
    except ValueError:
        return [], ["Для координат/даты нет поддержанного окна восхода и заката; значение неизвестно"]
    if month is not None:
        paths = [path for path in paths if path["month"] == month]
    public_root = "/data/geo/" if path.parent == (data_dir/"geo").resolve() else "/data/runtime/geo/"
    selected_artifact = public_root+path.name
    hashes = {public_root+npz.name: input_digest(npz), selected_artifact: input_digest(path)}
    claims = []
    sources = sorted({sid for c in baseline.get("claims", []) for e in c.get("evidence", [])
                      for sid in e.get("source_ids", [])})
    if not sources:
        return [], ["В профиле горизонта отсутствует provenance источников"]
    limitations = list(baseline.get("limitations", []))+SOLAR_LIMITATIONS
    with np.load(npz, allow_pickle=False) as arrays:
        for index, facade in enumerate(baseline["facades"]):
            key = f"facade_{index}_horizon"
            if key not in arrays:
                key = f"f{index}_horizon"  # additive-building compact runtime cache
                if key not in arrays:
                    continue
            horizon = arrays[key]
            if horizon.ndim != 2 or horizon.shape[1] == 0 or horizon.shape[0] != baseline["floors"]["count"]:
                continue
            angles = np.arange(horizon.shape[1])*360/horizon.shape[1]
            for path in paths:
                outputs = facade_exposure(path, angles, horizon, facade["azimuth_deg"])
                for low, high, result in solar_floor_ranges(outputs, characteristic):
                    value = {**result,
                             "facade": facade["code"], "month": path["month"],
                             "period": period, "model": METHODS[characteristic],
                             "temperature_c": None, "measurement": False}
                    inputs = {"input_sha256": hashes, "horizon_array": key, "representative_date": path["date"],
                              "start": path["start"], "end": path["end"], "sample_minutes": path["sample_minutes"],
                              "facade_azimuth_deg": facade["azimuth_deg"], "window_half_angle_deg": 75,
                              "minimum_visible_minutes": 20, "baseline_inputs": baseline.get("inputs", {})}
                    claims.append(_claim(hotel["id"], characteristic, value,
                                         {"type": "floor_range", "facade": facade["code"],
                                          "floor_min": low, "floor_max": high, "month": path["month"]},
                                         min(.6, facade.get("confidence", 0))*.9, observed,
                                         artifact=selected_artifact, source_ids=sources,
                                         inputs=inputs, limitations=limitations,
                                         synthetic=baseline.get("synthetic", False)))
    return claims, [] if claims else ["В сохранённом NPZ нет подходящих числовых профилей горизонта"]


def route_accessibility(edges, road_properties, *, endpoint_connections_verified=False):
    """Explicit tag evidence only. Step-free does not establish wheelchair access."""
    details = []
    for edge in edges:
        tags = {**road_properties.get(edge.get("osm_way_id"), {}), **edge}
        wheelchair = tags.get("wheelchair")
        details.append({"osm_way_id": edge.get("osm_way_id"), "u": edge["u"], "v": edge["v"],
                        "highway": tags.get("highway"), "wheelchair": wheelchair,
                        "surface": tags.get("surface"), "smoothness": tags.get("smoothness"),
                        "incline": tags.get("incline"), "kerb": tags.get("kerb"),
                        "ramp_wheelchair": tags.get("ramp:wheelchair"),
                        "steps": bool(tags.get("steps") or tags.get("highway") == "steps")})
    explicit_no = any(item["wheelchair"] == "no" for item in details)
    mapped_yes = bool(details) and all(item["wheelchair"] in {"yes", "designated"} for item in details)
    suitable = False if explicit_no else True if mapped_yes and endpoint_connections_verified else None
    return {"available": suitable, "wheelchair_suitable": suitable,
            "has_steps": any(item["steps"] for item in details) if details else None,
            "mapped_edges_wheelchair_yes": mapped_yes,
            "wheelchair_tag_coverage": round(sum(item["wheelchair"] is not None for item in details)/max(len(details), 1), 4),
            "endpoint_connections_verified": bool(endpoint_connections_verified), "edge_details": details}


def _route_edges(graph, node_ids):
    edges = []
    for u, v in zip(node_ids, node_ids[1:], strict=False):
        candidates = [edge for edge in graph.adj.get(str(u), []) if edge["v"] == str(v)]
        if not candidates:
            return None
        edges.append(min(candidates, key=lambda edge: graph.edge_cost(edge)[0]))
    return edges


def _regional(hotel, data_dir, characteristic):
    region = get_region(data_dir)
    xy = TO_UTM.transform(*hotel_center(hotel))
    from shapely.geometry import Point
    point = Point(xy)
    if characteristic == "railway_noise":
        buildings = region.buildings(xy, 1600)
        own, matched, distance = region.hotel_building(hotel, xy, buildings)
        if own is None:
            return [], ["Нет привязки к зданию для пофасадной оценки железнодорожного шума"]
        railways = region.nearby("railways", xy, 650)
        sources = source_samples(railways, kind="rail")
        source_ids = {feature["properties"].get("source_id", "osm_geofabrik") for feature in railways}
        # Footprints screen the rail sources, including Overture additions.
        source_ids.update(building["properties"].get("source_id", "osm_geofabrik") for building in buildings)
        synthetic = bool(own["properties"].get("synthetic") or any(feature["properties"].get("synthetic") for feature in railways))
        claims = []
        for facade in extract_facades(own["shape"]):
            noise = noise_facade(facade, sources, buildings)
            if noise is None:
                continue
            value = {key: noise[key] for key in ("facade", "night_db_min", "night_db_max", "day_db_min", "day_db_max", "sources")}
            value.update(model=METHODS[characteristic], measurement=False)
            claims.append(_claim(hotel["id"], characteristic, value,
                                 {"type": "facade", "facade": facade["code"]}, .32,
                                 region.observed_at, artifact="/data/seed/railways.geojson",
                                 source_ids=source_ids, synthetic=synthetic, inputs={"input_sha256": region.inputs,
                                 "source_details": noise["source_details"], "building_id": own["id"],
                                 "building_match": matched, "building_distance_m": distance},
                                 limitations=["Некалиброванный прокси наружного шума только картированных железных дорог",
                                              "Нет потоков поездов, расписания, измерений шума и данных о шумоизоляции",
                                              "Диапазон ±8 дБ и эталон на 10 м — допущения модели; отсутствие источника не доказывает тишину"]))
        return claims, [] if claims else ["Нет картированного железнодорожного источника в зоне модели; шум неизвестен"]
    if characteristic == "accessible_route":
        baseline = _baseline(hotel, data_dir)
        route = (baseline or {}).get("route_details", {})
        if not region.graph or not route:
            return [], ["Нет сохранённого маршрута до пляжа; доступность неизвестна"]
        if baseline.get("inputs", {}).get("/data/seed/walk_graph.json.gz") != region.inputs.get("/data/seed/walk_graph.json.gz"):
            return [], ["Пеший граф изменился; сохранённый маршрут требует пересчёта"]
        outbound = _route_edges(region.graph, route.get("node_ids", []))
        inbound = _route_edges(region.graph, route.get("return_node_ids", []))
        if not outbound or not inbound:
            return [], ["Рёбра сохранённого маршрута не найдены в текущем графе"]
        properties = {feature["id"]: feature["properties"] for feature in region.features["roads"]}
        value = route_accessibility(outbound+inbound, properties)
        details = value.pop("edge_details")
        value.update(beach_name=route.get("beach_name"), beach_id=route.get("beach_id"),
                     route_ref="/data/geo/"+hotel["id"]+".json#route", distance_m=route.get("distance_m"))
        return [_claim(hotel["id"], characteristic, value, {"type": "hotel"}, .65 if value["available"] is not None else .35,
                       region.observed_at, artifact=value["route_ref"], source_ids=["osm_geofabrik"],
                       synthetic=baseline.get("synthetic", False),
                       inputs={"input_sha256": region.inputs, "edge_details": details},
                       limitations=["Проверены картированные рёбра маршрута до пляжа и обратно, не доступность здания/номера",
                                    "Соединители входа отеля/пляжа с графом не проверены; wheelchair=yes на всех рёбрах недостаточно",
                                    "Нет тега wheelchair — unknown; отсутствие лестниц не означает доступность коляске",
                                    "Ширина, бордюры, уклон и покрытие могут быть не картированы"])], []
    dataset = "beaches" if characteristic == "beach_surface" else "pois"
    matches = region.nearby(dataset, xy, 5000)
    if characteristic == "green_nearby":
        matches = [feature for feature in matches if feature["properties"].get("leisure") in {"park", "garden"}
                   and feature["properties"].get("access") not in {"no", "private"}]
    if not matches:
        return [], ["Нет подходящего картированного объекта в 5 км; отсутствие объекта не установлено"]
    feature = min(matches, key=lambda item: (item["shape"].distance(point), item["id"]))
    distance = round(feature["shape"].distance(point), 1)
    properties = feature["properties"]
    value = {"osm_id": feature["id"], "name": properties.get("name"), "distance_m": distance,
             "distance_kind": "straight_line_to_mapped_geometry"}
    if characteristic == "beach_surface":
        surface = properties.get("surface")
        value.update(surface=surface, available=True if surface else None)
        limitations = ["Покрытие только из явного OSM surface ближайшего картированного пляжа; отсутствующий тег — unknown",
                       "Расстояние до геометрии по прямой; вход, доступ, чистота воды и безопасность купания не проверены"]
    else:
        value.update(available=True if distance <= 500 else None, leisure=properties.get("leisure"),
                     access=properties.get("access"), near_m=500)
        limitations = ["Ближайший картированный парк/сад по прямой до геометрии, не пешеходный маршрут и не вид зелени из окна",
                       "Отсутствие тега access не подтверждает свободный вход; неизвестные/не нанесённые объекты возможны",
                       "Объект дальше 500 м не доказывает отсутствие более близкой некартированной зелени"]
    return [_claim(hotel["id"], characteristic, value, {"type": "hotel"}, .7,
                   properties.get("observed_at", region.observed_at), artifact="/data/seed/"+dataset+".geojson",
                   source_ids=[properties.get("source_id", "osm_geofabrik")],
                   inputs={"input_sha256": {key: digest for key, digest in region.inputs.items()
                                            if key == "/data/seed/"+dataset+".geojson"},
                           "feature_id": feature["id"], "tags": properties},
                   limitations=limitations, kind="open_data", synthetic=properties.get("synthetic", False))], []


def compute_trait(hotel: dict, data_dir: Path, characteristic_id: str, month: int | None = None,
                  *, geo_artifact: Path | str | None = None) -> dict:
    """Return ``{claims, warnings, artifacts}``; month=None emits all 12 months.

    Inputs and the source snapshot are read-only. ``geo_artifact`` is an explicit
    authoritative solar change-event reference; missing/unsupported variants
    return unknown. Registry must pass it for hotels with an active change.
    """
    if characteristic_id not in TRAITS:
        raise ValueError("unsupported geo trait")
    hotel_id = hotel["id"]
    if "/" in hotel_id or "\\" in hotel_id or hotel_id in {".", ".."}:
        raise ValueError("unsafe hotel ID")
    if month is not None and (isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12):
        raise ValueError("month must be 1..12 or None")
    data_dir = Path(data_dir)
    if characteristic_id in {"morning_sun", "evening_heat"}:
        claims, warnings = _solar(hotel, data_dir, characteristic_id, month, geo_artifact)
    else:
        claims, warnings = _regional(hotel, data_dir, characteristic_id)
    return {"claims": claims, "warnings": warnings,
            "artifacts": {"read_only": True, "method": METHODS[characteristic_id],
                          "references": sorted({e["artifact"] for c in claims for e in c["evidence"]})}}
