"""Known-answer fixtures exercise provenance and conservative position descriptions."""
from datetime import timedelta

import pytest

from backend.agents.room_profiles import build_room_profiles, describe_room
from backend.contracts import Evidence, Fact, Hotel, RoomScope, Scope, now


def hotel(floors=6):
    return Hotel(id="profile-fixture", name="Synthetic profile fixture", lat=43.58, lon=39.72, floors=floors)


def fact(cid, *, side="W", low=1, high=6, value=None, status="computed", confidence=.9,
         synthetic=False, kind="calculation", month=None, identifier=None):
    scope = Scope(type="floor_range", facade=side, floor_min=low, floor_max=high, month=month) if side else Scope(month=month)
    return Fact(id=identifier or f"fixture-{cid}-{side}-{low}-{high}-{month}", hotel_id="profile-fixture",
                characteristic_id=cid, scope=scope, value={"available": True} if value is None else value,
                status=status, confidence=confidence, synthetic=synthetic,
                supporting_claim_ids=["synthetic-fixture-claim"], valid_until=now() + timedelta(days=2),
                evidence=[Evidence(id=(identifier or cid) + "-evidence", kind=kind,
                                   label="Known-answer profile fixture", source_ids=["test-fixture"],
                                   artifact="/tests/profile-fixture.json")])


def clause(profile, cid):
    return next(c for c in profile.clauses if c.characteristic_id == cid)


def describe(facts, *, actual=None, **scope):
    return describe_room(actual or hotel(), facts, [], RoomScope(facade="W", month=9, **scope))


def test_incompatible_facades_and_floors_do_not_form_one_positive_description():
    sea = fact("sea_view", low=4)
    quiet = fact("quiet_night", side="E", value={"available": True, "night_db_min": 35.2, "night_db_max": 46.8})
    sunset = fact("sunset_view", low=1, high=3, month=9)
    profiles = build_room_profiles(hotel(), [sea, quiet, sunset], [])
    assert {(p.scope.facade, p.scope.floor_min, p.scope.floor_max) for p in profiles} == {
        ("W", 1, 3), ("W", 4, 6), ("E", 1, 6)}
    upper = next(p for p in profiles if p.scope.facade == "W" and p.scope.floor_min == 4)
    assert clause(upper, "sea_view").status == "yes"
    assert clause(upper, "quiet_night").status == "unknown"
    assert clause(upper, "sunset_view").status == "unknown"
    assert quiet.id not in {i for c in upper.clauses for i in c.fact_ids}
    assert "35" not in upper.description


def test_single_floor_positive_does_not_cover_a_requested_range():
    sea = fact("sea_view", low=3, high=3)
    profile = describe([sea], floor_min=2, floor_max=5)
    item = clause(profile, "sea_view")
    assert item.status == "partial" and item.fact_ids == [sea.id]
    assert "часть этажей" in item.text
    assert "есть данные о видимости" not in item.text
    profiles = build_room_profiles(hotel(), [sea], [])
    assert [(p.scope.floor_min, p.scope.floor_max) for p in profiles] == [(3, 3), (1, 2), (4, 6)]
    assert [clause(p, "sea_view").status for p in profiles] == ["yes", "unknown", "unknown"]


def test_wider_requested_range_uses_all_intervals_including_a_negative_interior():
    broad = fact("sea_view")
    negative = fact("sea_view", low=3, high=3, value={"available": False}, identifier="negative-interior")
    item = clause(describe([broad, negative], floor_min=1, floor_max=6), "sea_view")
    assert item.status == "partial" and set(item.fact_ids) == {broad.id, negative.id}
    assert "меняется по этажам" in item.text


@pytest.mark.parametrize("state", ["disputed", "unknown", "stale", "expired", "no-evidence", "no-claims"])
def test_unusable_facts_cannot_become_positive_room_phrases(state):
    sea = fact("sea_view", low=1, high=6, status=state if state in {"disputed", "unknown", "stale"} else "computed")
    if state == "expired":
        sea.valid_until = now() - timedelta(seconds=1)
    if state == "no-evidence":
        sea.evidence = []
    if state == "no-claims":
        sea.supporting_claim_ids = []
    item = clause(describe([sea]), "sea_view")
    assert item.status == "unknown"
    assert "есть данные о видимости" not in item.text


def test_negative_view_and_wrong_month_are_not_rewritten_as_positive():
    sea = fact("sea_view", value={"available": False, "min_floor": 4})
    sunset = fact("sunset_view", month=7, value={"available": True, "visible_minutes": 55})
    profile = describe([sea, sunset])
    assert clause(profile, "sea_view").status == "no"
    assert "не подтверждается" in clause(profile, "sea_view").text
    assert clause(profile, "sunset_view").status == "unknown"
    assert "55" not in profile.description
    assert sunset.id not in clause(profile, "sunset_view").fact_ids


@pytest.mark.parametrize("value", [{"available": True}, {"available": True, "months": [7]},
                                    {"available": True, "month": 9, "months": [7]}])
def test_solar_statement_requires_actual_selected_month_coverage(value):
    profile = describe([fact("sunset_view", value=value)])
    assert clause(profile, "sunset_view").status == "unknown"


def test_synthetic_label_is_attached_to_each_affected_clause_and_aggregate():
    sea = fact("sea_view", synthetic=True)
    noise = fact("quiet_night", kind="simulation", value={"available": True, "night_db_min": 39.9, "night_db_max": 48.1})
    beach = fact("beach_walk", side=None, value={"minutes": 8.1})
    profile = describe([sea, noise, beach])
    for cid in ("sea_view", "quiet_night"):
        assert clause(profile, cid).synthetic
        assert clause(profile, cid).text.startswith("Синтетические демонстрационные данные:")
    assert not clause(profile, "beach_walk").synthetic
    assert any("Часть описания основана на синтетических" in c for c in profile.caveats)


def test_low_confidence_noise_is_preliminary_outdoor_proxy_with_rounded_numbers():
    noise = fact("quiet_night", confidence=.62, value={"available": True, "night_db_min": 41.2, "night_db_max": 49.7})
    profile = describe([noise])
    item = clause(profile, "quiet_night")
    assert item.status == "partial" and "Предварительно" in item.text
    assert "наружного" in item.text and "≈ 41–50 дБ" in item.text
    assert "41,2" not in item.text and "49,7" not in item.text
    assert any("не измерение внутри номера" in c for c in profile.caveats)
    assert "двор" not in profile.description


def test_boolean_hotelier_quiet_answer_never_borrows_geo_db_or_probability():
    answer = fact("quiet_night", kind="hotelier", status="confirmed")
    geo = {"facades": [{"code": "W", "night_db_min": 33, "night_db_max": 44, "probability": .99}]}
    profile = describe_room(hotel(), [answer], [], RoomScope(facade="W", floor_min=1, floor_max=6, month=9), geo=geo)
    item = clause(profile, "quiet_night")
    assert item.status == "yes" and "Тишина заявлена" in item.text
    assert "дБ" not in item.text and "99" not in profile.description


def test_unknown_hotel_height_only_generates_conditional_first_level_and_no_upper_floor_view():
    sea = fact("sea_view", low=4, high=12)
    beach = fact("beach_walk", side=None, value={"minutes": 8})
    [profile] = build_room_profiles(hotel(None), [sea, beach], [], geo={"building": {"floors": 12}})
    assert profile.scope.floor_min == profile.scope.floor_max == 1
    assert "условный уровень 1" in profile.title
    assert clause(profile, "sea_view").status == "unknown"
    assert clause(profile, "beach_walk").status == "yes"
    requested = describe([sea, beach], actual=hotel(None), floor_min=8, floor_max=10)
    assert clause(requested, "sea_view").status == "unknown"
    assert any("Этажность отеля" in c for c in requested.caveats)


def test_room_type_and_unspecified_facade_do_not_select_evidence_from_another_position():
    sea = fact("sea_view")
    specific = fact("soundproofing", value={"available": True, "windows": "double"})
    specific.scope.room_type = "Explicit fixture suite"
    profile = describe_room(hotel(), [sea, specific], [], RoomScope(floor_min=1, floor_max=6, month=9))
    assert clause(profile, "sea_view").status == clause(profile, "soundproofing").status == "unknown"
    assert "двойные окна" not in profile.description
    assert "Explicit fixture suite" not in profile.description


def test_descriptions_are_positions_without_invented_room_inventory():
    profile = describe([fact("sea_view")])
    assert profile.conditional and profile.title == "Западная сторона, 1–6-й этажи"
    assert any("направление реальных окон нужно подтвердить" in c for c in profile.caveats)
    assert any("его тип, кровати" in c for c in profile.caveats)
    assert "₽" not in profile.description and "номер 50" not in profile.description


def test_beach_route_only_describes_explicit_fields_and_osm_absence():
    beach = fact("beach_walk", side=None, value={"minutes": 7.1, "return_minutes": 10.1,
                 "return_ascent_m": 18.4, "has_steps": False, "crossings": []})
    profile = describe([beach])
    item = clause(profile, "beach_walk")
    assert "до пляжа ≈ 8 мин" in item.text and "обратно ≈ 11 мин" in item.text
    assert "подъём обратно ≈ 18 м" in item.text
    assert "в OSM-маршруте лестницы не отмечены" in item.text
    assert "лестниц нет" not in profile.description and "переходов нет" not in profile.description
    assert any("теги лестниц и переходов могут быть неполными" in c for c in profile.caveats)
    del beach.value["has_steps"]
    del beach.value["crossings"]
    missing = describe([beach])
    assert "не отмечены" not in clause(missing, "beach_walk").text
    assert any("Часть сведений" in c for c in missing.caveats)


def test_sunset_uses_exact_evidence_normal_or_an_explicit_sector_range():
    sunset = fact("sunset_view", low=3, high=3, month=9,
                  value={"available": True, "azimuth_deg": 260.2, "visible_minutes": 25.4})
    sunset.evidence[0].inputs = {"facade_azimuth_deg": 200, "window_half_angle_deg": 75}
    exact = clause(describe([sunset], floor_min=3, floor_max=3), "sunset_view")
    assert "закат сбоку" in exact.text and "угол к нормали фасада ≈ 60°" in exact.text
    assert "≈ 25 мин" in exact.text
    sunset.evidence[0].inputs = {}
    approximate = clause(describe([sunset], floor_min=3, floor_max=3), "sunset_view")
    assert "закат сбоку" not in approximate.text
    assert "ориентировочный угол" in approximate.text and "нормали фасада" not in approximate.text


def test_multifloor_solar_mean_is_not_presented_as_a_bound_for_each_floor():
    sunset = fact("sunset_view", month=9, value={"available": True, "visible_minutes": 47.1})
    profile = describe([sunset])
    assert clause(profile, "sunset_view").status == "yes"
    assert "47" not in profile.description


def test_profiles_are_capped_diverse_stable_and_do_not_embed_calculation_arrays():
    facts = [fact("sea_view", side=side, low=floor, high=floor, identifier=f"{side}-{floor}",
                  value={"available": bool(floor % 2)})
             for side in ("N", "S", "E", "W") for floor in range(1, 21)]
    facts[0].evidence[0].inputs = {"huge_array": list(range(10000))}
    profiles = build_room_profiles(hotel(20), facts, [], limit=100)
    assert len(profiles) == 12 and {p.scope.facade for p in profiles} == {"N", "S", "E", "W"}
    assert all(any("12 из 80" in c for c in p.caveats) for p in profiles)
    reverse = build_room_profiles(hotel(20), list(reversed(facts)), [], limit=100)
    assert [p.model_dump() for p in profiles] == [p.model_dump() for p in reverse]
    assert all(len(p.model_dump_json()) < 6000 for p in profiles)
    payload = " ".join(p.model_dump_json() for p in profiles)
    assert "huge_array" not in payload and "supporting_claim_ids" not in payload
    assert "N-1-evidence" in payload


def test_equivalent_adjacent_floor_facts_share_a_card_with_complete_references():
    facts = [fact("sea_view", low=floor, high=floor, identifier=f"floor-{floor}") for floor in range(1, 7)]
    [profile] = build_room_profiles(hotel(), facts, [])
    assert (profile.scope.floor_min, profile.scope.floor_max) == (1, 6)
    item = clause(profile, "sea_view")
    assert item.status == "yes" and set(item.fact_ids) == {f.id for f in facts}
    assert set(item.evidence_refs) == {f.evidence[0].id for f in facts}


def test_negative_route_does_not_turn_its_diagnostic_minutes_into_a_positive_route():
    beach = fact("beach_walk", side=None, value={"available": False, "minutes": 7})
    item = clause(describe([beach]), "beach_walk")
    assert item.status == "no" and "маршрут до пляжа не подтверждается" in item.text
    assert "7 мин" not in item.text


def test_nullable_solar_range_bounds_fall_back_to_known_index_without_crashing():
    sun = fact("evening_heat", month=9, value={"available": True, "exposure_index": .274,
               "exposure_index_min": None, "exposure_index_max": None})
    item = clause(describe([sun]), "evening_heat")
    assert item.status == "yes" and "0,27 из 1" in item.text


def test_window_construction_without_insulation_answer_does_not_confirm_insulation():
    windows = fact("soundproofing", value={"windows": "single"})
    item = clause(describe([windows]), "soundproofing")
    assert item.status == "partial" and "шумоизоляция выбранного положения не подтверждена" in item.text
    assert "Шумоизоляция заявлена" not in item.text


def test_negative_insulation_is_preserved_alongside_explicit_double_windows():
    windows = fact("soundproofing", value={"available": False, "windows": "double"})
    item = clause(describe([windows]), "soundproofing")
    assert item.status == "no" and "двойные окна" in item.text
    assert "Шумоизоляция не подтверждается источником" in item.text


def test_outside_known_hotel_height_remains_unknown_and_limits_are_validated():
    sea = fact("sea_view", low=1, high=12)
    profile = describe([sea], actual=hotel(3), floor_min=5)
    assert profile.scope.floor_min == profile.scope.floor_max == 5
    assert clause(profile, "sea_view").status == "unknown"
    with pytest.raises(ValueError):
        build_room_profiles(hotel(), [], [], month=13)
    assert build_room_profiles(hotel(), [], [], limit=0) == []


def test_unknown_height_never_presents_requested_seventh_floor_as_existing():
    profile = describe([fact("sea_view", low=7, high=9)], actual=hotel(None), floor_min=7, floor_max=9)
    assert profile.scope.floor_min == profile.scope.floor_max == 1
    assert "условный" in profile.title.casefold()
    assert clause(profile, "sea_view").status == "unknown"
    assert any("подтвержд" in caveat for caveat in profile.caveats)
