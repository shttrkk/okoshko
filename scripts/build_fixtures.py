"""Synthetic, explicitly marked wire examples for parallel frontend development."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.contracts import (
    Claim,
    Criterion,
    CriterionVerdict,
    Evidence,
    ExpectationPassport,
    Fact,
    Hotel,
    HotelDetail,
    ParsedQuery,
    PassportItem,
    Scope,
    SearchHit,
    SearchResult,
)

root = Path(__file__).resolve().parents[1]
destination = root / "docs" / "fixtures"
observed = datetime(2026, 10, 26, 12, tzinfo=UTC)
hotel = Hotel(id="fixture-hotel", name="Пример для разработки · синтетика", lat=43.58, lon=39.72,
              stars=4, price_rub=8500, floors=9, tags={"synthetic": True})
evidence = Evidence(id="fixture-evidence", kind="simulation", label="Синтетический пример wire-контракта",
                    source_ids=["fixture"], artifact="/data/fixtures/development.json",
                    limitations=["Это не реальный расчёт и не данные отеля"])
claim = Claim(id="fixture-claim", hotel_id=hotel.id, characteristic_id="sunset_view",
              scope=Scope(type="floor_range", facade="W", floor_min=5, floor_max=9),
              value={"available": True, "min_floor": 5, "facade": "W", "months": [9], "probability": .72},
              source="fixture", method="fixture@1", evidence=[evidence], confidence=.65,
              observed_at=observed, valid_until=observed+timedelta(days=180), synthetic=True)
fact = Fact(id="fixture-fact", hotel_id=hotel.id, characteristic_id="sunset_view", scope=claim.scope,
            value=claim.value, confidence=claim.confidence, status="computed", resolution="single",
            supporting_claim_ids=[claim.id], evidence=[evidence], valid_until=claim.valid_until, synthetic=True)
parsed = ParsedQuery(text="тихий номер с видом на закат недалеко от моря", check_in="2027-09-14", check_out="2027-09-21",
                     must=[Criterion(characteristic_id="sunset_view", label="Закат из окна")],
                     nice=[Criterion(characteristic_id="quiet_night", label="Тишина ночью"),
                           Criterion(characteristic_id="beach_walk", label="До моря ≤ 10 минут", op="lte", value=10, field="minutes")])
verdict = CriterionVerdict(characteristic_id="sunset_view", label="Закат из окна", required=True,
                          status="partial", symbol="≈", text="Закат — с 5-го этажа, западный фасад · синтетический пример",
                          confidence=.65, fact_ids=[fact.id], evidence_refs=[evidence.id], caveat="Уточните номер у отеля")
result = SearchResult(parsed=parsed, results=[SearchHit(hotel=hotel, score=.65, group="almost", must_total=1,
                      verdicts=[verdict], room_recommendation="Западная сторона, этаж 5+ · синтетический пример")], total=1, latency_ms=12)
detail = HotelDetail(hotel=hotel, facts=[fact], claims=[claim], verdicts=[verdict],
                     room_recommendation="Западная сторона, этаж 5+ · синтетический пример")
passport = ExpectationPassport(id="fixture-passport", hotel_id=hotel.id,
            items=[PassportItem(characteristic_id="sunset_view", text="Западный фасад, этаж 5+", scope=claim.scope)],
            timeline=[{"at": observed.isoformat(), "text": "Демонстрационный запрос отправлен"}])
for name, obj in {"search": result, "hotel": detail, "passport": passport}.items():
    (destination / f"{name}.json").write_text(json.dumps(obj.model_dump(mode="json"), ensure_ascii=False, indent=2))
(root / "data/fixtures/development.json").write_text(json.dumps({"synthetic":True,"purpose":"Wire example, never seed as real evidence"}))
print("Exported 3 synthetic fixtures")

