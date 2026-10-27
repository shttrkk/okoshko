"""Exact ordered OSM member joins and diagnostics; relaxed probes never assert service."""
from __future__ import annotations

from collections import Counter


class TransitNetwork:
    def __init__(self, routes, stop_ids=None):
        self.routes = routes
        self.ordered, self.raw = [], []
        for route in routes:
            members = route["members"]
            entries = [dict(m, position=i) for i, m in enumerate(members) if m.get("role", "").startswith("stop")]
            family = "stop"
            if len(entries) < 2:
                entries = [dict(m, position=i) for i, m in enumerate(members) if m.get("role", "").startswith("platform")]
                family = "platform"
            if len(entries) >= 2:
                self.ordered.append((route, entries, family))
            raw_ids = {m["id"] for m in members if stop_ids is None or m["id"] in stop_ids}
            self.raw.append((route, raw_ids))
        self.stats = {"relations": len(routes), "ordered_usable": len(self.ordered),
                      "without_two_supported_ordered_members": len(routes)-len(self.ordered),
                      "modes": dict(Counter(r["tags"].get("route", "unknown") for r in routes)),
                      "semantics": "Member roles/order only; no inferred stops, route direction reversal, platform equivalence or timetable"}

    @staticmethod
    def board(entry):
        return not entry.get("role", "").endswith("exit_only")

    @staticmethod
    def alight(entry):
        return not entry.get("role", "").endswith("entry_only")

    def connect(self, airport_stops, hotel_stops):
        starts, ends = set(airport_stops), set(hotel_stops)
        direct, first_legs, last_legs = [], [], []
        for route, entries, family in self.ordered:
            for i, entry in enumerate(entries):
                if entry["id"] in starts and self.board(entry):
                    for goal in entries[i+1:]:
                        if self.alight(goal):
                            first_legs.append((route, entry, goal))
                            if goal["id"] in ends:
                                direct.append((route, entry, goal, family))
                if entry["id"] in ends and self.alight(entry):
                    last_legs.extend((route, board, entry) for board in entries[:i] if self.board(board))
        witnesses, selected, count = [], {}, None
        if direct:
            count = 0
            for route, board, alight, family in direct[:5]:
                selected[route["id"]] = route
                witnesses.append({"route_ids": [route["id"]], "board": board, "alight": alight, "member_family": family})
        else:
            by_transfer = {}
            for route, board, alight in last_legs:
                by_transfer.setdefault(board["id"], []).append((route, board, alight))
            for first, board, transfer_alight in first_legs:
                for last, transfer_board, alight in by_transfer.get(transfer_alight["id"], []):
                    if first["id"] == last["id"]:
                        continue
                    count = 1
                    selected[first["id"]], selected[last["id"]] = first, last
                    witnesses.append({"route_ids": [first["id"], last["id"]], "board": board, "transfer_alight": transfer_alight,
                                      "transfer_board": transfer_board, "alight": alight})
                    if len(witnesses) == 5:
                        break
                if len(witnesses) == 5:
                    break
        return {"available": True if count is not None else None, "transfers": count,
                "routes": [{"id": r["id"], "ref": r["tags"].get("ref"), "mode": r["tags"].get("route"),
                            "opening_hours": r["tags"].get("opening_hours"), "interval": r["tags"].get("interval"),
                            "by_night": r["tags"].get("by_night")} for r in selected.values()],
                "witnesses": witnesses, "scheduled_minutes": None, "night_service": None,
                "coverage": "Exact ordered members, index-specific boarding/alighting and exact stop-ID transfer; no GTFS"}

    @staticmethod
    def _relaxed_possible(route_sets, starts, ends):
        first = [ids for ids in route_sets if ids & starts]
        last = [ids for ids in route_sets if ids & ends]
        return any(ids & ends for ids in first) or any(a & b for a in first for b in last)

    def diagnose(self, airport_stops, hotel_stops, result=None):
        starts, ends = set(airport_stops), set(hotel_stops)
        result = result or self.connect(starts, ends)
        ordered_sets = [{e["id"] for e in entries} for _, entries, _ in self.ordered]
        raw_sets = [ids for _, ids in self.raw]
        airport_ordered = any(ids & starts for ids in ordered_sets)
        hotel_ordered = any(ids & ends for ids in ordered_sets)
        order_relaxed = self._relaxed_possible(ordered_sets, starts, ends)
        raw_relaxed = self._relaxed_possible(raw_sets, starts, ends)
        if result["available"] is True:
            reason = None
        elif not airport_ordered:
            reason = "airport_ordered_members_missing" if any(ids & starts for ids in raw_sets) else "airport_stops_absent_from_relations"
        elif not hotel_ordered:
            reason = "hotel_ordered_members_missing" if any(ids & ends for ids in raw_sets) else "hotel_stops_absent_from_relations"
        elif order_relaxed:
            reason = "direction_or_entry_exit_restriction"
        elif raw_relaxed:
            reason = "incomplete_ordered_members"
        else:
            reason = "no_mapped_zero_or_one_transfer_join"
        return {"reason": reason, "airport_in_ordered_relations": airport_ordered, "hotel_in_ordered_relations": hotel_ordered,
                "order_relaxed_probe_possible": order_relaxed, "raw_member_probe_possible": raw_relaxed,
                "shared_airport_hotel_stop_ids": sorted(starts & ends),
                "relaxed_probe_semantics": "Diagnostics only; ignoring order/roles never makes available=true or estimates 2+ transfers"}
