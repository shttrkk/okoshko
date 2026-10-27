"""Multiple POI destinations on the actual directed foot graph."""
from __future__ import annotations

import heapq
import math
import re

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from geo.coordinates import metric
from geo.walk import crossing_details


def open_at_two(hours):
    """Conservative daily-clock subset; calendar/holiday rules stay unknown."""
    if not hours:
        return None
    hours = hours.strip()
    if hours == "24/7":
        return True
    if hours in {"off", "closed"}:
        return False
    if not re.fullmatch(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}(?:,\d{1,2}:\d{2}-\d{1,2}:\d{2})*", hours):
        return None
    for interval in hours.split(","):
        first, last = interval.split("-")
        sh, sm = map(int, first.split(":"))
        eh, em = map(int, last.split(":"))
        if sh > 24 or eh > 24 or sm > 59 or em > 59 or sh == 24 and sm or eh == 24 and em:
            return None
        start, end = sh*60+sm, eh*60+em
        if end < start:
            if 120 >= start or 120 < end:
                return True
        elif start <= 120 < end:
            return True
    return False


def pediatric(properties):
    specialties = str(properties.get("healthcare:speciality", "")).lower().split(";")
    return bool({"paediatrics", "pediatrics"}.intersection(specialties) or re.search(r"детск[а-яё]*(?:\s+[а-яё]+){0,2}\s+(?:больниц|поликлиник)|children.?s?\s+hospital", str(properties.get("name", "")).lower()))


def category(properties, kind):
    amenity, leisure = properties.get("amenity"), properties.get("leisure")
    if kind == "night_shop":
        return properties.get("shop") in {"convenience", "supermarket", "greengrocer", "bakery", "grocery"}
    if kind == "night_food":
        return amenity in {"cafe", "restaurant", "fast_food", "food_court"}
    if kind in {"dog_park", "playground"}:
        return leisure == kind
    if kind == "park":
        return leisure in {"park", "garden"}
    if kind in {"veterinary", "hospital", "parking"}:
        return amenity == kind
    if kind == "pet_shop":
        return properties.get("shop") == "pet"
    if kind == "children_hospital":
        return amenity in {"hospital", "clinic"} and pediatric(properties)
    if kind == "promenade":
        return properties.get("highway") in {"pedestrian", "living_street"} or "набережн" in str(properties.get("name", "")).lower()
    if kind == "station":
        return properties.get("railway") in {"station", "halt"}
    if kind == "bus_stop":
        return properties.get("highway") == "bus_stop" or properties.get("public_transport") == "platform"
    return amenity == "charging_station" if kind == "ev" else False


class NearbyRoutes:
    def __init__(self, graph, roads=(), railways=(), *, snap_limit_m=120):
        self.graph, self.roads, self.railways = graph, list(roads), list(railways)
        self.crossing_sources = self.roads+self.railways
        self.crossing_tree = STRtree([f["shape"] for f in self.crossing_sources])
        self.snap_limit_m = snap_limit_m
        self.reverse = {node: [] for node in graph.nodes}
        self.edge_minutes, self.poi_snaps = {}, {}
        for edges in graph.adj.values():
            for edge in edges:
                self.reverse[edge["v"]].append(edge)
                self.edge_minutes[id(edge)] = graph.edge_cost(edge)[0]
        self.ids = list(graph.nodes)
        self.tree = STRtree([Point(graph.nodes[node]["xy"]) for node in self.ids])

    def snap(self, xy, limit=None):
        if not self.ids:
            return None, None
        index = int(self.tree.nearest(Point(xy)))
        node = self.ids[index]
        distance = math.dist(xy, self.graph.nodes[node]["xy"])
        return (node, distance) if distance <= (limit or self.snap_limit_m) else (None, distance)

    def from_hotel(self, xy, *, max_minutes=90, reverse=False):
        start, snap = self.snap(xy)
        if start is None:
            return None
        costs, prev, queue = {start: 0.0}, {}, [(0.0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node] or cost > max_minutes:
                continue
            for edge in self.reverse[node] if reverse else self.graph.adj[node]:
                target = edge["u"] if reverse else edge["v"]
                candidate = cost+self.edge_minutes[id(edge)]
                if candidate <= max_minutes and candidate < costs.get(target, float("inf")):
                    costs[target], prev[target] = candidate, edge
                    heapq.heappush(queue, (candidate, target))
        return start, snap, costs, prev

    def nearest(self, origin, features, *, kind, max_minutes=15):
        if origin is None:
            return None
        start, hotel_snap, costs, prev = origin
        options, unknown_hours = [], 0
        for feature in features:
            properties = feature["properties"]
            if not category(properties, kind):
                continue
            if kind.startswith("night_"):
                opened = open_at_two(properties.get("opening_hours"))
                unknown_hours += opened is None
                if opened is not True:
                    continue
            point = feature["shape"].representative_point()
            if feature["id"] not in self.poi_snaps:
                self.poi_snaps[feature["id"]] = self.snap((point.x, point.y))
            node, snap = self.poi_snaps[feature["id"]]
            if node in costs:
                options.append((costs[node], feature["id"], feature, node, snap))
        if not options:
            return None
        _, _, feature, target, poi_snap = min(options, key=lambda row: row[:2])
        edges, node = [], target
        while node != start:
            edge = prev[node]
            edges.append(edge)
            node = edge["u"]
        edges.reverse()
        summary = self.graph.summary(edges)
        geometry = self.graph.geometry(edges)
        crossing_sources = (sorted([self.crossing_sources[int(i)] for i in self.crossing_tree.query(metric(shape(geometry)), predicate="intersects")], key=lambda f: f["id"])
                            if len(geometry.get("coordinates", [])) >= 2 else [])
        crossings = crossing_details(geometry, crossing_sources, [], edges)
        return {"available": summary["minutes"] <= max_minutes, "osm_id": feature["id"],
                "name": feature["properties"].get("name"), **summary,
                "hotel_snap_m": round(hotel_snap, 1), "destination_snap_m": round(poi_snap, 1),
                "node_ids": [start]+[e["v"] for e in edges], "crossings": [c["properties"] for c in crossings],
                "opening_hours": feature["properties"].get("opening_hours"), "unknown_hours_candidates": unknown_hours,
                "emergency": feature["properties"].get("emergency"), "threshold_minutes": max_minutes,
                "route_semantics": "graph only; snap connectors are unmapped and excluded from minutes"}
