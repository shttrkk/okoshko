"""Directed shortest paths over the supplied real pedestrian graph."""
from __future__ import annotations

import heapq
import math

from shapely.geometry import mapping, shape

from geo.coordinates import TO_UTM, geographic, metric


def travel_minutes(length_m, rise_m, *, steps=False, highway="footway"):
    grade = max(-.3, min(.3, rise_m/max(length_m, 1)))
    speed = 6*math.exp(-3.5*abs(grade+.05))*1000/60
    multiplier = 2.0 if steps else 1.0
    if highway in {"path", "track"}:
        multiplier /= .6
    return length_m/speed*multiplier


class WalkGraph:
    def __init__(self, data, terrain=None):
        self.nodes = {str(node["id"]): dict(node) for node in data.get("nodes", [])}
        self.adj = {node_id: [] for node_id in self.nodes}
        self.terrain = terrain
        for node in self.nodes.values():
            node["xy"] = TO_UTM.transform(node["lon"], node["lat"])
            if node.get("elevation_m") is None and terrain:
                import numpy as np
                node["elevation_m"] = float(terrain.sample(np.array([node["xy"][0]]), np.array([node["xy"][1]]))[0])
        for raw in data.get("edges", []):
            edge = dict(raw)
            u, v = str(edge["u"]), str(edge["v"])
            if u not in self.nodes or v not in self.nodes:
                continue
            if edge.get("access") in {"no", "private"} or edge.get("foot") in {"no", "private"}:
                continue
            edge["u"], edge["v"] = u, v
            self.adj[u].append(edge)
            # Contract defaults to directed. Only explicit undirected edges reverse.
            if edge.get("bidirectional") or edge.get("directed") is False:
                self.adj[v].append({**edge, "u": v, "v": u, "reversed": True})

    def nearest(self, xy, *, max_distance=200):
        options = [(math.dist(node["xy"], xy), node_id) for node_id, node in self.nodes.items() if self.adj[node_id]]
        if not options:
            return None
        distance, node = min(options)
        return node if distance <= max_distance else None

    def edge_cost(self, edge):
        a, b = self.nodes[edge["u"]], self.nodes[edge["v"]]
        za, zb = a.get("elevation_m"), b.get("elevation_m")
        known = za is not None and zb is not None and math.isfinite(za) and math.isfinite(zb)
        rise = zb-za if known else 0
        length = float(edge.get("length_m") or math.dist(a["xy"], b["xy"]))
        return travel_minutes(length, rise, steps=edge.get("steps", False) or edge.get("highway") == "steps",
                              highway=edge.get("highway", "footway")), length, rise, known

    def shortest(self, start, targets):
        targets = set(targets)
        queue, costs, prev = [(0, start)], {start: 0.0}, {}
        end = None
        while queue:
            cost, u = heapq.heappop(queue)
            if cost > costs[u]:
                continue
            if u in targets:
                end = u
                break
            for edge in self.adj[u]:
                v = edge["v"]
                candidate = cost+self.edge_cost(edge)[0]
                if candidate < costs.get(v, float("inf")):
                    costs[v], prev[v] = candidate, edge
                    heapq.heappush(queue, (candidate, v))
        if end is None:
            return None
        path = []
        while end != start:
            edge = prev[end]
            path.append(edge)
            end = edge["u"]
        return list(reversed(path))

    def summary(self, edges):
        metrics = [self.edge_cost(edge) for edge in edges]
        return {"minutes": round(sum(x[0] for x in metrics), 2),
                "distance_m": round(sum(x[1] for x in metrics), 1),
                "ascent_m": round(sum(max(0, x[2]) for x in metrics), 1),
                "has_steps": any(edge.get("steps") or edge.get("highway") == "steps" for edge in edges),
                "elevation_coverage": sum(x[3] for x in metrics)/max(len(metrics), 1)}

    def geometry(self, edges):
        coords = []
        for edge in edges:
            geometry = edge.get("geometry")
            if isinstance(geometry, dict):
                geometry = geometry.get("coordinates", [])
            if geometry and len(geometry) >= 2:
                line = list(geometry)
                origin = self.nodes[edge["u"]]
                if math.dist(line[-1], [origin["lon"], origin["lat"]]) < math.dist(line[0], [origin["lon"], origin["lat"]]):
                    line.reverse()
                coords.extend(line if not coords else line[1:])
            else:
                for node_id in (edge["u"], edge["v"]):
                    node = self.nodes[node_id]
                    point = [node["lon"], node["lat"]]
                    if not coords or point != coords[-1]:
                        coords.append(point)
        return {"type": "LineString", "coordinates": coords}


def crossing_details(route_geometry, roads, railways, edges):
    if len(route_geometry.get("coordinates", [])) < 2:
        return []
    line = metric(shape(route_geometry))
    details = []
    transition_edges = [edge for edge in edges if edge.get("tunnel") in {True, "yes", "building_passage"}
                        or edge.get("bridge") in {True, "yes"} or edge.get("crossing")]
    for feature in roads+railways:
        props = feature["properties"]
        rail = props.get("railway") in {"rail", "light_rail", "tram", "narrow_gauge"}
        if not rail and props.get("highway") not in {"motorway", "trunk", "primary", "secondary", "trunk_link", "primary_link"}:
            continue
        intersection = line.intersection(feature["shape"])
        if intersection.is_empty:
            continue
        parts = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
        for point in parts:
            if point.geom_type != "Point":
                continue
            transition = next((edge for edge in transition_edges if edge.get("geometry") and metric(shape(edge["geometry"] if isinstance(edge["geometry"], dict) else {"type": "LineString", "coordinates": edge["geometry"]})).distance(point) <= 15), None)
            details.append({"type": "Feature", "geometry": mapping(geographic(point)),
                            "properties": {"source_id": feature["id"], "kind": "railway" if rail else "major_road",
                                           "name": props.get("name"), "controlled": bool(transition),
                                           "transition": "tunnel" if transition and transition.get("tunnel") else "bridge" if transition and transition.get("bridge") else "crossing" if transition else "unknown"}})
    return details


def beach_route(graph, hotel_xy, beaches, roads, railways):
    start = graph.nearest(hotel_xy)
    if start is None:
        return None, "Нет узла пешего графа в 200 м от отеля"
    beach_nodes = {}
    for beach in beaches:
        representative = beach["shape"].representative_point()
        target = graph.nearest((representative.x, representative.y), max_distance=250)
        if target:
            beach_nodes[target] = beach
    if not beach_nodes:
        return None, "Нет привязанного к графу входа на пляж; прямую линию маршрутом не считаем"
    edges = graph.shortest(start, beach_nodes)
    if not edges:
        return None, "Пляж недостижим по доступным направленным рёбрам графа"
    target = edges[-1]["v"]
    back = graph.shortest(target, [start])
    if back is None:
        return None, "Обратный путь по направленному графу не найден"
    outbound, inbound = graph.summary(edges), graph.summary(back)
    geometry = graph.geometry(edges)
    crossings = crossing_details(geometry, roads, railways, edges)
    beach = beach_nodes[target]
    return {"available": True, **outbound, "return_minutes": inbound["minutes"],
            "return_ascent_m": inbound["ascent_m"], "crossings": [item["properties"] for item in crossings],
            "beach_name": beach["properties"].get("name") or "Пляж OSM "+beach["id"],
            "beach_id": beach["id"], "geometry": geometry, "crossing_features": crossings,
            "node_ids": [start]+[edge["v"] for edge in edges],
            "return_node_ids": [target]+[edge["v"] for edge in back],
            "snap_distance_m": round(math.dist(hotel_xy, graph.nodes[start]["xy"]), 1)}, None
