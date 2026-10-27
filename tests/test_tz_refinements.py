"""Known answers for stale construction and incomplete airport-route evidence."""
from __future__ import annotations

from datetime import date

import pytest
from shapely.geometry import Point, box, mapping

from geo.coordinates import geographic
from geo.tz_construction import construction_nearby, current_evidence, date_interval
from geo.tz_transit import TransitNetwork
from pipeline.fetch_tz_fisht import confirmed_height
from pipeline.fetch_tz_transfer_diagnostics import access_diagnostics, primary_reason

AS_OF = date(2026, 10, 25)
META = {"element_timestamp": "2026-10-24T12:00:00Z", "version": 7}


@pytest.mark.parametrize("tags", [{}, {"check_date": "2026-10-20"}, {"opening_date": "2029-09-01"},
    {"start_date": "2026-01-01", "end_date": "2027-12-01"}])
def test_fresh_edit_generic_check_and_schedule_do_not_prove_current_works(tags):
    result = current_evidence(tags, META, AS_OF)
    assert result["weak_element_edit_recent"] is True
    assert result["eligible_temporally"] is False
    assert result["construction_tag_changed_at"] is None


def test_explicit_recent_construction_survey_and_date_boundaries():
    assert current_evidence({"check_date:construction": "2026-10-20"}, META, AS_OF)["eligible_temporally"]
    assert not current_evidence({"survey:date": "2025-10-20"}, META, AS_OF)["eligible_temporally"]
    assert not current_evidence({"survey:date": "2026-10-20", "end_date": "2026-09"}, META, AS_OF)["eligible_temporally"]
    assert not current_evidence({"survey:date": "2026-10-25"}, META, AS_OF)["eligible_temporally"]
    assert not current_evidence({"survey:date": "2026"}, META, AS_OF)["eligible_temporally"]
    assert date_interval("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert date_interval("2026-02-30") is None


def site(geometry, tags):
    return {"id": "site", "shape": geometry, "properties": tags}


def test_current_site_requires_close_facing_facade_not_just_500m_inventory():
    origin = (560000, 4820000)
    facade = {"code": "E", "azimuth_deg": 90, "geometry": mapping(geographic(Point(origin).buffer(1).boundary))}
    # A point geometry is sufficient for a deterministic facade reference.
    facade["geometry"] = mapping(geographic(Point(origin)))
    fresh = {"check_date:construction": "2026-10-20"}
    near = site(Point(origin[0]+100, origin[1]), fresh)
    result = construction_nearby(origin, [near], {"site": META}, [facade], as_of=AS_OF)
    assert result["available"] is True and result["current_eligible_count"] == 1
    far = construction_nearby(origin, [site(Point(origin[0]+250, origin[1]), fresh)], {"site": META}, [facade], as_of=AS_OF)
    assert far["mapped_count"] == 1 and far["available"] is None
    wrong_side = construction_nearby(origin, [site(Point(origin[0]-100, origin[1]), fresh)], {"site": META}, [facade], as_of=AS_OF)
    assert wrong_side["available"] is None
    contains = construction_nearby(origin, [site(box(origin[0]-10, origin[1]-10, origin[0]+10, origin[1]+10), fresh)], {"site": META}, [facade], as_of=AS_OF)
    assert contains["available"] is None
    outside = construction_nearby(origin, [site(Point(origin[0]+501, origin[1]), fresh)], {"site": META}, [facade], as_of=AS_OF)
    assert outside["mapped_count"] == 0 and outside["available"] is None


def test_fisht_does_not_borrow_height_or_infer_from_levels():
    assert confirmed_height({"name": "Фишт", "building:levels": "12"}, {"claims": {}}) is None
    statement = {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"unit": "http://www.wikidata.org/entity/Q11573", "amount": "+49"}}}}
    assert confirmed_height({}, {"claims": {"P2048": [statement]}})["height_m"] == 49
    assert confirmed_height({}, {"claims": {"P2048": [{**statement, "rank": "deprecated"}]}}) is None
    assert confirmed_height({"height": "36"}, {})["height_source"] == "osm_height"
    assert confirmed_height({"height": "inf"}, {}) is None
    assert confirmed_height({"height": "nan"}, {}) is None


def route(oid, members):
    return {"id": oid, "tags": {"route": "bus"}, "members": [{"id": oid, "role": role} for oid, role in members]}


def test_repeated_member_roles_are_position_specific():
    network = TransitNetwork([route("r", [("a", "stop_exit_only"), ("x", "stop"), ("a", "stop_entry_only"), ("h", "stop")])])
    result = network.connect(["a"], ["h"])
    assert result["transfers"] == 0
    assert result["witnesses"][0]["board"]["position"] == 2


def test_reverse_order_probe_and_missing_roles_never_create_connection():
    network = TransitNetwork([route("r", [("h", "stop"), ("a", "stop")])], {"a", "h"})
    result = network.connect(["a"], ["h"])
    assert result["available"] is None
    assert network.diagnose(["a"], ["h"], result)["reason"] == "direction_or_entry_exit_restriction"
    missing = TransitNetwork([route("r", [("a", ""), ("h", "")])], {"a", "h"})
    assert missing.connect(["a"], ["h"])["available"] is None
    assert missing.stats["without_two_supported_ordered_members"] == 1
    assert missing.diagnose(["a"], ["h"])["raw_member_probe_possible"] is True


def test_stop_and_platform_are_not_joined_by_guessing_equivalence():
    routes = [route("r1", [("a", "stop"), ("x_stop", "stop")]), route("r2", [("x_platform", "stop"), ("h", "stop")])]
    assert TransitNetwork(routes).connect(["a"], ["h"])["available"] is None
    routes[1]["members"][0]["id"] = "x_stop"
    result = TransitNetwork(routes).connect(["a"], ["h"])
    assert result["transfers"] == 1
    assert result["witnesses"][0]["transfer_alight"]["id"] == result["witnesses"][0]["transfer_board"]["id"]


def test_sensitivity_does_not_change_baseline_approach_threshold():
    limits = {"snap_limit_m": 120, "airport_radius_m": 500, "airport_walk_minutes": 10, "hotel_walk_minutes": 15}
    records = [{"id": "stop", "node": "n", "snap_m": 10, "terminal_distance_m": 400}]
    origin = ("a", 5, {"n": 12}, {})
    base = access_diagnostics(origin, 5, records, limits, airport=True)
    relaxed = access_diagnostics(origin, 5, records, {**limits, "airport_walk_minutes": 15}, airport=True)
    assert base["eligible_stop_ids"] == [] and relaxed["eligible_stop_ids"] == ["stop"]
    assert limits["airport_walk_minutes"] == 10
    assert primary_reason(base, relaxed, {"reason": None}, terminal_known=True) == "airport_stop_access_not_established"
    assert not access_diagnostics(origin, 121, records, limits)["anchor_snap_known"]
