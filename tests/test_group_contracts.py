import pytest
from pydantic import ValidationError

from backend.contracts import ParsedQuery, RoomClause, RoomRequest, RoomScope, Scope


def test_legacy_query_has_no_implicit_room_count():
    assert ParsedQuery(text="на пятерых тихо").rooms == []


def test_position_wish_can_limit_floors_without_inventing_a_facade():
    room = RoomRequest(label="Маме", scope=RoomScope(floor_max=3))
    assert room.scope.facade is None and room.scope.floor_max == 3
    with pytest.raises(ValidationError):
        Scope(type="floor_range", floor_max=3)
    with pytest.raises(ValidationError):
        RoomScope(floor_min=7, floor_max=3)


def test_known_description_clause_requires_fact_reference():
    with pytest.raises(ValidationError):
        RoomClause(text="Тихо", status="yes")
    assert RoomClause(text="Неизвестно, есть ли окно", status="unknown").fact_ids == []
    assert RoomClause(text="Море видно", status="partial", fact_ids=["fact"]).fact_ids == ["fact"]
