"""Local rechecks and an explicit, idempotent synthetic new-building scenario."""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from backend.agents.resolver import resolve_hotel
from backend.contracts import (
    ChangeEvent,
    CharacteristicSpec,
    Claim,
    ExpectationPassport,
    Fact,
    ToolContext,
    now,
)
from backend.settings import DATA, RUNTIME_DIR
from backend.tools.registry import Registry
from backend.tracing import Trace

_LOCK = threading.RLock()
DEMO_HOTEL = "osm-node-10058556917"
GEO_CHARACTERISTICS = {"sea_view", "sunset_view", "mountain_view", "quiet_night", "beach_walk"}


def unchanged_geo_inputs(claims, data_dir):
    """Re-running an old snapshot is not a fresh observation of the hotel."""
    from geo.runtime_cache import input_digest
    snapshots = []
    for claim in claims:
        for evidence in claim.evidence:
            inputs = evidence.inputs
            hashes = inputs.get("input_sha256")
            if hashes:
                snapshots.append(hashes)
            elif inputs.get("baseline_artifact") and inputs.get("baseline_sha256"):
                snapshots.append({inputs["baseline_artifact"]: inputs["baseline_sha256"]})
    return bool(snapshots) and all(
        artifact.startswith("/data/") and input_digest(Path(data_dir)/artifact.removeprefix("/data/")) == expected
        for snapshot in snapshots for artifact, expected in snapshot.items())


def durable_geo(result, data_dir: Path = DATA, runtime: Path = RUNTIME_DIR):
    """Copy each produced artifact before DB references escape the container layer."""
    result = json.loads(json.dumps(result))
    artifact = result.get("geo", {}).get("artifact")
    if not artifact or not artifact.startswith("/data/geo/"):
        raise ValueError("Геосервис не вернул локальный артефакт расчёта")
    filename = Path(artifact).name
    target_dir = Path(runtime) / "geo"
    target_dir.mkdir(parents=True, exist_ok=True)
    new_prefix = "/data/runtime/geo/" + filename.removesuffix(".json")
    old_prefix = "/data/geo/" + filename.removesuffix(".json")

    def rewrite(value):
        if isinstance(value, str):
            return value.replace(old_prefix, new_prefix)
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    result = rewrite(result)
    source_npz = Path(data_dir) / "geo" / filename.replace(".json", ".npz")
    if source_npz.is_file():
        shutil.copy2(source_npz, target_dir / source_npz.name)
    target = target_dir / filename
    document = {**result["geo"], "claims": result["claims"]}
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(target)
    return result


def notify_passports(repo, hotel_id, before, after, change_id):
    from backend.api.passports import _all_hold, _stored_criteria, scopes_compatible
    before_by_id = {fact.id: fact for fact in before}
    changed = {fact.characteristic_id for fact in before + after
               if fact.characteristic_id in GEO_CHARACTERISTICS}
    notifications = []
    for row in repo.find("expectation_passports", hotel_id=hotel_id):
        passport = ExpectationPassport.model_validate(row)
        impacted = []
        for item in passport.items:
            if item.characteristic_id not in changed or not item.critical:
                continue
            old = [before_by_id[fid] for fid in item.fact_ids if fid in before_by_id]
            criteria = _stored_criteria(repo.get("passport_items", item.id) or {})
            scopes = [item.scope, *[fact.scope for fact in old]]
            candidates = [fact for fact in after if fact.characteristic_id == item.characteristic_id
                          and scopes_compatible([item.scope, fact.scope])]
            overlapping_negative = any(not _all_hold(fact, criteria) and any(scopes_compatible([scope, fact.scope]) for scope in scopes) for fact in candidates)
            if not candidates or not any(_all_hold(fact, criteria) for fact in candidates) or overlapping_negative:
                item.status = "at_risk"
                item.response = "Синтетическая новая застройка изменила расчёт; требуется повторное подтверждение номера отельером."
                impacted.append(item.id)
        if not impacted:
            continue
        passport.status = "at_risk"
        notification = {"id": hashlib.sha256((change_id+passport.id).encode()).hexdigest()[:32], "type": "change",
                        "channel": "local", "text": "Демо: новый дом повлиял на ожидания. Проверьте вид и выбранный номер.",
                        "change_id": change_id, "passport_id": passport.id, "item_ids": impacted, "synthetic": True, "at": now().isoformat()}
        if not any(item.get("id") == notification["id"] for item in passport.notifications):
            passport.notifications.append(notification)
            passport.timeline.append({**notification, "type": "at_risk"})
        repo.upsert("expectation_passports", passport.model_dump(mode="json"))
        for item in passport.items:
            stored = repo.get("passport_items", item.id) or {}
            repo.upsert("passport_items", {**stored, **item.model_dump(mode="json"), "passport_id": passport.id})
        notifications.append(notification)
    return notifications


def simulate_change(repo, hotel_id=None, *, data_dir=DATA, runtime=RUNTIME_DIR):
    from geo.changes import new_building_fixture
    from geo.service import compute_hotel
    with _LOCK:
        hotel_id = hotel_id or DEMO_HOTEL
        hotel = repo.get("hotels", hotel_id)
        if hotel is None:
            raise LookupError("Отель не найден")
        change_id = hashlib.sha256(("new_building_fixture_v1:"+hotel_id).encode()).hexdigest()[:32]
        existing = repo.get("change_events", change_id)
        if existing:
            return ChangeEvent.model_validate(existing)
        base_path = Path(data_dir) / "geo" / (hotel_id + ".json")
        base_geo = json.loads(base_path.read_text()) if base_path.is_file() else {}
        base_geo = base_geo.get("geo", base_geo)
        fixture = new_building_fixture(hotel, base_geo)
        before = [Fact.model_validate(row) for row in repo.find("facts", hotel_id=hotel_id)]
        trace = Trace(repo, "refresh", hotel_id=hotel_id, trigger="synthetic_new_building")
        try:
            result = durable_geo(compute_hotel(hotel, Path(data_dir), new_building=fixture), data_dir, runtime)
            if not any(row["characteristic_id"] in {"sea_view", "sunset_view", "mountain_view"} for row in result["claims"]):
                raise ValueError("Для пересчёта видимости не хватает геовходов; отсутствие вида не утверждается")
            old_claims = repo.find("claims", hotel_id=hotel_id)
            supersedes = {cid: [row["id"] for row in old_claims if row["characteristic_id"] == cid and row["source"] == "geo_engine"]
                          for cid in GEO_CHARACTERISTICS}
            claims = []
            for row in result["claims"]:
                claim = Claim.model_validate({**row, "run_id": trace.run.id, "synthetic": True,
                                              "supersedes_ids": supersedes.get(row["characteristic_id"], [])})
                if repo.get("claims", claim.id) is None:
                    claims.append(claim)
            if not claims:
                raise ValueError("Геосервис не смог пересчитать затронутые характеристики; событие не опубликовано")
            repo.insert_claims(claims)
            after = resolve_hotel(hotel_id, repo)
            notifications = notify_passports(repo, hotel_id, before, after, change_id)
            event = ChangeEvent(id=change_id, kind="osm_building_new", title="Демо: новый дом — видимость пересчитана",
                                geometry=fixture["geometry"], payload={"hotel_id": hotel_id, "fixture": fixture, "run_id": trace.run.id,
                                "scenario": "new_building_fixture_v1", "geo_artifact": result["geo"]["artifact"],
                                "claim_ids": [claim.id for claim in claims]}, affected_hotel_ids=[hotel_id],
                                before=[fact for fact in before if fact.characteristic_id in GEO_CHARACTERISTICS],
                                after=[fact for fact in after if fact.characteristic_id in GEO_CHARACTERISTICS], notifications=notifications)
            # Persist the final claims, including supersession and run provenance, in the same artifact.
            document = {**result["geo"], "claims": [claim.model_dump(mode="json") for claim in claims]}
            (Path(runtime) / "geo" / Path(result["geo"]["artifact"]).name).write_text(json.dumps(document, ensure_ascii=False) + "\n")
            repo.insert("change_events", event.model_dump(mode="json"))
            trace.step("collector", "tool_call", "Пересчёт с синтетическим новым домом", input={"fixture": fixture},
                       output={"claim_ids": [claim.id for claim in claims], "change_id": event.id, "notifications": len(notifications)},
                       evidence_refs=[e.id for claim in claims for e in claim.evidence],
                       rationale="Новый полигон реально включён в LOS; все старые диапазоны заменены новыми наблюдениями")
            trace.finish()
            return event
        except Exception as error:
            trace.finish("failed", str(error)[:300])
            raise


def refresh_expired(repo, *, data_dir=DATA, runtime=RUNTIME_DIR):
    """Recheck expired active observations locally; unavailable inputs stay stale."""
    claims = [Claim.model_validate(row) for row in repo.all("claims")]
    superseded = {item for claim in claims for item in ([claim.supersedes] if claim.supersedes else [])+claim.supersedes_ids}
    targets = {}
    for claim in claims:
        if claim.id not in superseded and claim.valid_until <= datetime.now(UTC):
            targets.setdefault(claim.hotel_id, set()).add(claim.characteristic_id)
    trace = Trace(repo, "refresh", trigger="expired_valid_until")
    processed = 0
    try:
        registry = Registry(repo, data_dir=Path(data_dir))
        for hotel_id, ids in targets.items():
            hotel = repo.get("hotels", hotel_id)
            if not hotel:
                continue
            refreshed, warnings = [], []
            geo_ids = ids & GEO_CHARACTERISTICS
            unchanged = geo_ids and unchanged_geo_inputs([claim for claim in claims if claim.hotel_id == hotel_id and claim.characteristic_id in geo_ids and claim.id not in superseded], data_dir)
            if unchanged:
                warnings.append("Геоданные не обновились: повторный расчёт того же снимка не продлевает срок наблюдений; факты остаются устаревшими")
            if geo_ids and not unchanged:
                from geo.service import compute_hotel
                events = [event for event in repo.all("change_events") if hotel_id in event.get("affected_hotel_ids", [])]
                events.sort(key=lambda event: event.get("detected_at", ""), reverse=True)
                fixture = events[0].get("payload", {}).get("fixture") if events else None
                result = durable_geo(compute_hotel(hotel, Path(data_dir), **({"new_building": fixture} if fixture else {})), data_dir, runtime)
                geo_claims = []
                for row in result["claims"]:
                    if row["characteristic_id"] in ids:
                        old = [claim.id for claim in claims if claim.hotel_id == hotel_id and claim.characteristic_id == row["characteristic_id"] and claim.source == "geo_engine"]
                        claim = Claim.model_validate({**row, "run_id": trace.run.id, "supersedes_ids": old})
                        refreshed.append(claim)
                        geo_claims.append(claim.model_dump(mode="json"))
                    else:
                        geo_claims.append(row)
                document = {**result["geo"], "claims": geo_claims}
                target = Path(runtime) / "geo" / Path(result["geo"]["artifact"]).name
                temporary = target.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(document, ensure_ascii=False) + "\n")
                temporary.replace(target)
            for cid in ids - GEO_CHARACTERISTICS:
                row = repo.get("characteristics", cid)
                if not row:
                    continue
                spec = CharacteristicSpec.model_validate(row)
                for call in spec.methods:
                    result = registry.execute(ToolContext(hotel_id=hotel_id, characteristic_id=cid, run_id=trace.run.id), call)
                    for claim in result.claims:
                        claim.spec_version = spec.version
                    refreshed.extend(result.claims)
                    warnings.extend(result.warnings)
            if refreshed:
                repo.insert_claims([claim for claim in refreshed if repo.get("claims", claim.id) is None])
            resolve_hotel(hotel_id, repo)
            processed += len(ids)
            trace.step("collector", "tool_call", "Проверка истёкших наблюдений", input={"hotel_id": hotel_id, "characteristic_ids": sorted(ids)},
                       output={"new_claims": len(refreshed), "warnings": warnings},
                       rationale="Проверены локальные инструменты; сохранённые архивные данные не выданы за новую загрузку, отсутствие обновления остаётся stale")
        trace.finish()
        return {"processed": processed, "run_id": trace.run.id}
    except Exception as error:
        trace.finish("failed", str(error)[:300])
        raise
