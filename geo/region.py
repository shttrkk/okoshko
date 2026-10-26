"""Cached read-only regional inputs, shared by offline and runtime calculations."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from geo.coordinates import metric
from geo.terrain import Grid, SeaClassifier
from geo.walk import WalkGraph


def number(value):
    try:
        return float(str(value).replace("m", "").replace(",", ".").split(";")[0].strip())
    except (ValueError, TypeError):
        return None


def load_features(path):
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    result = []
    for feature in data.get("features", []):
        try:
            geometry = metric(shape(feature["geometry"]))
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            if geometry.is_empty:
                continue
            result.append({"id": str(feature.get("id") or feature.get("properties", {}).get("osm_id")),
                           "properties": feature.get("properties", {}), "shape": geometry,
                           "feature": feature})
        except (ValueError, TypeError):
            continue
    return result


class Region:
    def __init__(self, data_dir: Path, *, terrain_path: Path | None = None):
        self.data_dir = Path(data_dir)
        self.seed = self.data_dir/"seed" if (self.data_dir/"seed").exists() else self.data_dir
        terrain_path = terrain_path or self.data_dir/"geo"/"terrain_30m.npz"
        self.terrain = Grid.load(terrain_path) if terrain_path.exists() else None
        self.inputs = {}
        self.features = {}
        self.indexes = {}
        for name in ("buildings", "coastline", "roads", "railways", "beaches", "pois"):
            path = self.seed/(name+".geojson")
            features = load_features(path)
            self.features[name] = features
            self.indexes[name] = STRtree([feature["shape"] for feature in features])
            if path.exists():
                self.inputs["/data/seed/"+path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if terrain_path.exists():
            self.inputs["/data/geo/terrain_30m.npz"] = hashlib.sha256(terrain_path.read_bytes()).hexdigest()
        self.sea = SeaClassifier([feature["shape"] for feature in self.features["coastline"]])
        self.graph = None
        graph_path = self.seed/"walk_graph.json.gz"
        if graph_path.exists():
            with gzip.open(graph_path, "rt") as stream:
                self.graph = WalkGraph(json.load(stream), self.terrain)
            self.inputs["/data/seed/"+graph_path.name] = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        self.source_ids = sorted({feature["properties"].get("source_id", "osm_geofabrik")
                                  for values in self.features.values() for feature in values})
        if self.terrain:
            self.source_ids.append("copernicus_dem")
        known_heights = [number(feature["properties"].get("height_m")) for feature in self.features["buildings"]
                         if feature["properties"].get("height_source") in {"height", "levels"}]
        self.height_prior = np.asarray([height for height in known_heights if height and 2 <= height <= 150])
        self.observed_at = max((feature["properties"].get("observed_at", "2026-10-26T00:00:00Z")
                                for values in self.features.values() for feature in values), default="2026-10-26T00:00:00Z")

    def nearby(self, name, center, radius):
        geometry = Point(center).buffer(radius)
        return [self.features[name][int(index)] for index in self.indexes[name].query(geometry, predicate="intersects")]

    def buildings(self, center, radius=1500):
        result = []
        for feature in self.nearby("buildings", center, radius):
            properties = feature["properties"]
            height = number(properties.get("height_m") or properties.get("height"))
            levels = number(properties.get("levels") or properties.get("building:levels"))
            source = properties.get("height_source")
            if height is None and levels:
                height, source = levels*3, "levels"
            if height is None:
                height = float(np.median(self.height_prior)) if len(self.height_prior) else 6.0
                source = "empirical_prior" if len(self.height_prior) else "assumed_prior"
            result.append({**feature, "height_m": min(250, max(2, height)), "height_source": source or "height",
                           "levels": int(levels) if levels and 1 <= levels <= 100 else None, "base_m": 0.0})
        return result

    def hotel_building(self, hotel, xy, buildings):
        requested = hotel.get("building_id") or hotel.get("tags", {}).get("building_id")
        if requested:
            matched = next((building for building in buildings if building["id"] == requested), None)
            if matched:
                return matched, "id", 0.0
        point = Point(xy)
        inside = [building for building in buildings if building["shape"].covers(point)]
        if inside:
            return min(inside, key=lambda building: building["shape"].area), "contains", 0.0
        nearest = sorted((building["shape"].distance(point), i) for i, building in enumerate(buildings))
        if nearest and nearest[0][0] <= 30:
            distance, i = nearest[0]
            return buildings[i], "nearest_30m", distance
        return None, "unmatched", None


_CACHE = {}


def get_region(data_dir: Path):
    data_dir = Path(data_dir).resolve()
    seed = data_dir/"seed" if (data_dir/"seed").exists() else data_dir
    files = list(seed.glob("*.geojson")) + [seed/"walk_graph.json.gz", data_dir/"geo"/"terrain_30m.npz"]
    key = (str(data_dir), tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in files if path.exists()))
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = Region(data_dir)
    return _CACHE[key]
