from datetime import UTC, datetime, timedelta

from backend.agents.ranker import rank_hotels
from backend.contracts import (
    CharacteristicSpec,
    Criterion,
    Evidence,
    Fact,
    Hotel,
    ParsedQuery,
    RoomRequest,
    RoomScope,
    Scope,
    ToolCall,
)


def fact(cid, side=None, low=None, high=None, value=True, month=None):
    key = f"{cid}-{side}-{low}-{month}"
    return Fact(
        id=key,
        hotel_id="h",
        characteristic_id=cid,
        scope=Scope(facade=side, floor_min=low, floor_max=high, month=month),
        value=value if isinstance(value, dict) else {"available": value},
        status="computed",
        confidence=0.9,
        supporting_claim_ids=[key + "-claim"],
        evidence=[
            Evidence(
                id=key + "-e",
                kind="calculation",
                label="Synthetic known-answer fixture",
                source_ids=["fixture"],
                artifact="/fixture.json",
            )
        ],
        valid_until=datetime.now(UTC) + timedelta(days=1),
        synthetic=True,
    )


def fixtures():
    hotel = Hotel(id="h", name="Fixture hotel", lat=43, lon=39, floors=6)
    facts = [
        fact("sea_view", "W", 4, 6),
        fact("quiet_night", "E", 1, 3),
        fact("beach_walk", value={"minutes": 8, "available": True, "has_steps": False}),
    ]
    specs = [
        CharacteristicSpec(
            id=cid,
            name_ru=cid,
            description="fixture",
            value_schema={"type": "object"},
            methods=[ToolCall(tool="fixture")],
        )
        for cid in ["sea_view", "quiet_night", "beach_walk", "room_orientation"]
    ]
    query = ParsedQuery(
        text="2 rooms",
        must=[Criterion(characteristic_id="beach_walk", label="Beach", field="minutes", op="lte", value=10)],
        rooms=[
            RoomRequest(label="Sea", must=[Criterion(characteristic_id="sea_view", label="Sea")]),
            RoomRequest(label="Quiet", must=[Criterion(characteristic_id="quiet_night", label="Quiet")]),
        ],
    )
    return hotel, facts, specs, query


def test_different_room_requests_can_use_opposite_sides_but_each_is_internally_compatible():
    h, f, s, q = fixtures()
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group == "all"
    assert [room.scope.facade for room in hit.room_plan] == ["W", "E"]
    assert hit.room_plan[0].scope.floor_min >= 4 and hit.room_plan[1].scope.floor_max <= 3
    assert all(
        room.profile and "конкретного номера" in " ".join(room.profile.caveats) for room in hit.room_plan
    )
    flat = ParsedQuery(text="one room", must=[c for room in q.rooms for c in room.must])
    assert rank_hotels([h], f, flat, s)[0].group != "all"


def test_shared_hotel_condition_is_checked_once_and_can_reject_the_entire_plan():
    h, f, s, q = fixtures()
    q.must[0].value = 5
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group != "all" and len(hit.verdicts) == 1


def test_upper_floor_limit_is_not_lost_when_other_side_matches():
    h, f, s, q = fixtures()
    q.rooms[0].scope = RoomScope(floor_max=3)
    assert rank_hotels([h], f, q, s)[0].group != "all"


def test_unknown_height_or_room_type_cannot_satisfy_scope_only_wishes():
    h, f, s, q = fixtures()
    q.rooms = [RoomRequest(label="Mother", scope=RoomScope(floor_max=3))]
    assert rank_hotels([h], f, q, s)[0].group == "all"
    h.floors = None
    assert rank_hotels([h], f, q, s)[0].group != "all"
    h.floors = 6
    q.rooms[0].scope = RoomScope(room_type="single")
    assert rank_hotels([h], f, q, s)[0].group != "all"


def test_same_model_position_is_not_a_quantity_or_availability_statement():
    h, f, s, q = fixtures()
    q.rooms[1] = q.rooms[0].model_copy(update={"label": "Second"}, deep=True)
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group == "all" and hit.room_plan[0].scope == hit.room_plan[1].scope
    assert all("свободно" in " ".join(room.caveats) for room in hit.room_plan)


def test_required_missing_field_is_visible_and_blocks_full_group_match():
    h, f, s, q = fixtures()
    q.rooms[1].no_data_must = [
        Criterion(characteristic_id="room_orientation", label="Courtyard", field="courtyard")
    ]
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group != "all"
    assert any(
        v.required and v.status == "unknown" and v.label == "Courtyard" for v in hit.room_plan[1].verdicts
    )


def test_recommended_floor_never_expands_across_an_interior_negative_slice():
    h, _, s, q = fixtures()
    f = [
        fact("sea_view", "W", 1, 6),
        fact("sea_view", "W", 3, 3, value=False),
        fact("quiet_night", "W", 1, 6),
        fact("beach_walk", value={"minutes": 8}),
    ]
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group == "all"
    assert hit.room_plan[0].scope.floor_min == hit.room_plan[0].scope.floor_max != 3
    assert (
        next(c for c in hit.room_plan[0].profile.clauses if c.characteristic_id == "sea_view").status == "yes"
    )


def test_solar_without_month_evidence_is_not_a_supported_personal_wish():
    h, _, s, q = fixtures()
    q.rooms[0].must[0].characteristic_id = "sunset_view"
    s.append(
        CharacteristicSpec(
            id="sunset_view",
            name_ru="Sunset",
            description="Fixture",
            value_schema={"type": "object"},
            methods=[ToolCall(tool="fixture")],
        )
    )
    hit = rank_hotels([h], [fact("sunset_view", "W", 1, 6)], q, s)[0]
    assert hit.group != "all"
    assert hit.room_plan[0].verdicts[0].status == "unknown"


def test_global_position_limits_cannot_be_lost_when_evaluating_rooms():
    h, f, s, q = fixtures()
    q.min_floor = 5
    assert rank_hotels([h], f, q, s)[0].group != "all"
    q.facade = "E"
    q.rooms[0].scope.facade = "W"
    assert rank_hotels([h], f, q, s)[0].room_plan[0].status == "unknown"


def test_known_type_on_another_side_does_not_prove_requested_combination():
    h, f, s, q = fixtures()
    suite = fact("room_orientation", "E", 1, 2)
    suite.scope.room_type = "Known suite"
    q.rooms = [RoomRequest(label="Suite", scope=RoomScope(facade="W", floor_min=4, room_type="Known suite"))]
    hit = rank_hotels([h], [*f, suite], q, s)[0]
    assert hit.group != "all" and hit.room_plan[0].status == "unknown"


def test_identical_requests_reuse_assessment_without_sharing_mutable_plans(monkeypatch):
    from backend.agents import group_match

    h, f, s, q = fixtures()
    q.rooms = [q.rooms[0].model_copy(deep=True, update={"label": str(i)}) for i in range(12)]
    actual = group_match.assess_hotel
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return actual(*args, **kwargs)

    monkeypatch.setattr(group_match, "assess_hotel", counting)
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group == "all" and len(calls) == 2  # shared + one repeated request
    assert [r.label for r in hit.room_plan] == [str(i) for i in range(12)]
    hit.room_plan[0].scope.floor_max = 99
    assert hit.room_plan[1].scope.floor_max != 99


def test_unmapped_personal_wish_cannot_turn_into_an_all_match():
    h, f, s, q = fixtures()
    q.rooms[0].unmapped = ["низкий этаж"]
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group != "all"
    assert hit.room_plan[0].status == "partial"
    assert any("низкий этаж" in caveat for caveat in hit.room_plan[0].caveats)


def test_scope_only_type_recommendation_uses_its_own_floor_witness():
    h, f, s, q = fixtures()
    suite = fact("room_orientation", "W", 5, 6)
    suite.scope.room_type = "Known suite"
    q.rooms = [RoomRequest(label="Suite", scope=RoomScope(facade="W", floor_min=4, room_type="Known suite"))]
    hit = rank_hotels([h], [*f, suite], q, s)[0]
    assert hit.group == "all"
    assert hit.room_plan[0].scope.floor_min == hit.room_plan[0].scope.floor_max == 5


def test_floor_only_request_keeps_its_ceiling_without_picking_an_arbitrary_side():
    h, f, s, q = fixtures()
    q.rooms = [RoomRequest(label="Маме", scope=RoomScope(floor_max=3))]
    hit = rank_hotels([h], f, q, s)[0]
    assert hit.group == "all"
    assert hit.room_plan[0].scope.floor_min == 1 and hit.room_plan[0].scope.floor_max == 3
    assert hit.room_plan[0].scope.facade is None
