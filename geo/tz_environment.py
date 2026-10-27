"""Mapped construction/masts and car arrival with explicit evidence limits."""
from __future__ import annotations

import heapq
import math

from pyproj import Geod
from shapely.geometry import Point
from shapely.strtree import STRtree

from geo.coordinates import angle_difference, azimuth

CAR_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link", "residential", "unclassified", "service", "living_street", "road"}
GEOD = Geod(ellps="WGS84")


def car_allowed(properties):
    if properties.get("highway") not in CAR_CLASSES:
        return False
    # OSM access hierarchy: the more specific motor_vehicle overrides vehicle
    # and access. A private/customer drive is not public arrival evidence.
    permission = properties.get("motor_vehicle", properties.get("vehicle", properties.get("access")))
    return permission not in {"no", "private", "customers", "agricultural", "forestry", "delivery"}


def mapped_nearby(xy, features, *, radius_m, facades=()):
    point, rows = Point(xy), []
    for feature in features:
        distance = feature["shape"].distance(point)
        if distance <= radius_m:
            target = feature["shape"].representative_point()
            properties = feature["properties"]
            rows.append({"osm_id": feature["id"], "distance_m": round(distance, 1), "name": properties.get("name"),
                         "opening_date": properties.get("opening_date"), "end_date": properties.get("end_date"),
                         "construction": properties.get("construction"),
                         "radio_tags": {k: v for k, v in properties.items() if k.startswith("communication:")},
                         "facing_facades": [f["code"] for f in facades if angle_difference(f["azimuth_deg"], azimuth(target.x-xy[0], target.y-xy[1])) <= 60]})
    return {"available": True if rows else None, "mapped_count": len(rows), "radius_m": radius_m,
            "nearest": sorted(rows, key=lambda r: (r["distance_m"], r["osm_id"]))[:10],
            "absence_semantics": "zero mapped objects does not establish absence or safety"}


def car_arrival(hotel, roads, entrances, parking, *, threshold_m=30):
    footprint = hotel.get("footprint")
    from shapely.geometry import shape

    from geo.coordinates import metric
    polygon = metric(shape(footprint)) if footprint else None
    point = metric(Point(hotel["lon"], hotel["lat"]))
    if polygon is not None and not polygon.covers(point):
        polygon = None  # a nearby, unassigned building cannot identify an entrance/parking
    # An entrance is tied to this hotel only by the assigned footprint boundary.
    matched = [e for e in entrances if polygon is not None and polygon.boundary.distance(e["shape"]) <= 2]
    origins = [e["shape"] for e in matched] or ([polygon] if polygon is not None else [])
    allowed = [r for r in roads if car_allowed(r["properties"])]
    nearest = min(((r["shape"].distance(o), r) for o in origins for r in allowed), default=None, key=lambda pair: pair[0])
    name = str(hotel.get("name", "")).strip().casefold()
    own_parking = [p["id"] for p in parking if polygon is not None and p["shape"].within(polygon) and
                   ((name and name in {str(p["properties"].get("name", "")).strip().casefold(), str(p["properties"].get("operator", "")).strip().casefold()}) or p["properties"].get("access") == "customers")]
    local = [r for r in roads if r["shape"].distance(point) <= 50]
    pedestrian = True if local and all(r["properties"].get("highway") in {"pedestrian", "footway", "steps", "path"} or not car_allowed(r["properties"]) for r in local) else None
    return {"available": nearest[0] <= threshold_m if nearest else None,
            "road_distance_m": round(nearest[0], 1) if nearest else None, "road_osm_id": nearest[1]["id"] if nearest else None,
            "entrance_osm_ids": [e["id"] for e in matched], "arrival_anchor": "mapped_entrance" if matched else "assigned_footprint" if polygon is not None else None,
            "threshold_m": threshold_m, "pedestrian_zone_proxy": pedestrian,
            "private_parking": True if own_parking else None, "private_parking_osm_ids": own_parking,
            "access_semantics": "OSM motor_vehicle > vehicle > access; standard road class default access; no turn restrictions or live barriers"}


class CarGraph:
    def __init__(self, roads):
        self.nodes, self.adj = {}, {}
        for road in roads:
            if not car_allowed(road["properties"]) or road["shape"].geom_type != "LineString":
                continue
            coords = road["feature"]["geometry"]["coordinates"]
            ids = road["properties"].get("node_ids", [])
            if len(ids) != len(coords):
                continue
            oneway = road["properties"].get("oneway")
            if oneway is None and (road["properties"].get("junction") == "roundabout" or road["properties"].get("highway") in {"motorway", "motorway_link"}):
                oneway = "yes"
            for u, v, a, b in zip(ids, ids[1:], coords, coords[1:], strict=False):
                self.nodes[u], self.nodes[v] = a, b
                self.adj.setdefault(u, [])
                self.adj.setdefault(v, [])
                length = GEOD.inv(*a, *b)[2]
                if oneway != "-1":
                    self.adj[u].append((v, length))
                if oneway not in {"yes", "1", "true"}:
                    self.adj[v].append((u, length))
        from geo.coordinates import TO_UTM
        self.ids = list(self.nodes)
        self.points = [Point(TO_UTM.transform(*self.nodes[n])) for n in self.ids]
        self.tree = STRtree(self.points)

    def snap(self, xy, limit=250):
        if not self.ids:
            return None, None
        index = int(self.tree.nearest(Point(xy)))
        distance = self.points[index].distance(Point(xy))
        return (self.ids[index], round(distance, 1)) if distance <= limit else (None, round(distance, 1))

    def distances(self, start):
        if start not in self.adj:
            return {}
        costs, queue = {start: 0.0}, [(0.0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node]:
                continue
            for target, length in self.adj[node]:
                value = cost+length
                if value < costs.get(target, math.inf):
                    costs[target] = value
                    heapq.heappush(queue, (value, target))
        return costs
