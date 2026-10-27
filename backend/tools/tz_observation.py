"""Read prepared stage-one observations. No arbitrary paths, network or LLM facts."""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from backend.contracts import Claim, ToolResult, uid

STAGES = {
    "airport_noise": "environment", "construction_nearby": "environment", "mobile_masts": "environment",
    "car_arrival": "environment", "building_history": "environment", "night_open": "routes", "dog_walk": "routes",
    "veterinary_nearby": "routes", "pet_shop_nearby": "routes", "family_routes": "routes", "airport_transfer": "routes",
    "climate_days": "climate", "landmark_view": "landmarks",
    "location_access": "location", "mobile_internet": "metadata", "photo_features": "metadata",
    "events_nearby": "metadata", "hotel_parking": "supplements",
    "family_nearby": "routes", "car_access": "supplements", "climate": "supplements",
}


@lru_cache(maxsize=8)
def _records(path, identity):
    with gzip.open(path, "rt") as stream:
        return [Claim.model_validate_json(line) for line in stream if line.strip()]


@lru_cache(maxsize=1024)
def _digest(path, identity):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _identity(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _valid_inputs(claim, data_dir):
    for evidence in claim.evidence:
        hashes = evidence.inputs.get("input_sha256", {})
        if not hashes:
            return False
        for reference, expected in hashes.items():
            relative = Path(reference.removeprefix("/data/"))
            allowed = len(relative.parts) == 2 and relative.parts[0] in {"seed", "geo", "characteristics", "landmarks"}
            draft = len(relative.parts) == 3 and relative.parts[:2] == ("characteristics", "tz_draft")
            if not reference.startswith("/data/") or relative.is_absolute() or ".." in relative.parts or not (allowed or draft):
                return False
            path = data_dir / relative
            try:
                if path.is_symlink() or _digest(str(path), _identity(path)) != expected:
                    return False
            except OSError:
                return False
    return True


def active_landmark_overlay(hotel_id, events):
    matching = [event for event in events if event.get('kind') == 'osm_building_new'
                and hotel_id in event.get('affected_hotel_ids', []) and event.get('status', 'applied') == 'applied']
    matching.sort(key=lambda event: (event.get('detected_at', ''), event.get('id', '')), reverse=True)
    return (matching[0].get('payload', {}).get('geo_artifact') or '__missing_active_artifact__') if matching else None


def prepared_observations(data_dir, context, params, *, events=()):
    kind = params.get("kind")
    if kind not in STAGES:
        return ToolResult(warnings=["Неизвестный вид подготовленного расчёта"])
    stages = [STAGES[kind]]+(["history"] if kind == "building_history" else [])
    records = []
    for stage in stages:
        path = Path(data_dir) / "seed" / f"tz_{stage}_claims.jsonl.gz"
        try:
            records.extend(_records(str(path), _identity(path)))
        except (OSError, EOFError, ValueError, json.JSONDecodeError):
            continue
    if not records:
        return ToolResult(warnings=["Подготовленные данные отсутствуют или повреждены; значение неизвестно"])
    overlay = active_landmark_overlay(context.hotel_id, events) if kind == 'landmark_view' else None
    selected, changed, mismatched, stale_overlay = [], False, False, False
    for claim in records:
        if claim.hotel_id != context.hotel_id or claim.characteristic_id != kind or claim.scope.month not in {None, context.month} or claim.valid_until <= datetime.now(UTC):
            continue
        if params.get("landmark_id") and claim.value.get("landmark_id") != params["landmark_id"]:
            continue
        if overlay and not any(evidence.inputs.get('geo_artifact') == overlay for evidence in claim.evidence):
            stale_overlay = True
            continue
        requested = {k: v for k, v in params.items() if k not in {"kind", "target", "landmark_id"}}
        if any(claim.value.get("calculation_parameters", {}).get(k) != v for k, v in requested.items()):
            mismatched = True
            continue
        if not _valid_inputs(claim, Path(data_dir)):
            changed = True
            continue
        selected.append(claim.model_copy(deep=True, update={"id": uid(), "characteristic_id": context.characteristic_id, "run_id": context.run_id,
                                                           "supersedes": claim.id if context.characteristic_id == kind else None}))
    warnings = ["Исходные данные изменились; нужен новый расчёт"] if changed else []
    if stale_overlay:
        warnings.append('Геометрия окружения изменилась; подготовленная видимость объекта требует расчёта по действующему артефакту')
    if mismatched:
        warnings.append("Такие пороги ещё не рассчитаны; значение неизвестно")
    if not selected:
        warnings.append("Нет подготовленного наблюдения для этого отеля/среза; значение неизвестно")
    return ToolResult(claims=selected, warnings=warnings, artifacts={"stage": STAGES[kind], "prepared_only": True})
