"""Numeric raster helpers. Runtime imports no rasterio/GDAL."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.strtree import STRtree


@dataclass
class Grid:
    values: np.ndarray
    x_min: float
    y_max: float
    cell: float

    def sample(self, x, y):
        x, y = np.broadcast_arrays(x, y)
        col = np.floor((x - self.x_min) / self.cell).astype(int)
        row = np.floor((self.y_max - y) / self.cell).astype(int)
        valid = (row >= 0) & (col >= 0) & (row < self.values.shape[0]) & (col < self.values.shape[1])
        result = np.full(x.shape, np.nan, dtype=float)
        result[valid] = self.values[row[valid], col[valid]]
        return result

    @classmethod
    def load(cls, path: Path):
        with np.load(path, allow_pickle=False) as data:
            key = "terrain" if "terrain" in data else "elevation"
            return cls(data[key].astype(np.float32), float(data["x_min"]), float(data["y_max"]), float(data["cell"]))


class SeaClassifier:
    """OSM coastline orientation: land on the left, sea on the right.

    Nearest oriented segment classification is used only inside the supplied
    coastline's coverage. It cannot infer inland lakes or unmapped islands.
    """
    def __init__(self, coastline):
        segments = []
        for geometry in coastline:
            lines = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
            for line in lines:
                if line.geom_type != "LineString":
                    continue
                coords = np.asarray(line.coords)
                segments.extend(LineString([a, b]) for a, b in zip(coords[:-1], coords[1:], strict=True) if np.linalg.norm(b-a) > .1)
        self.segments = segments
        self.tree = STRtree(segments) if segments else None
        self.starts = np.asarray([list(line.coords)[0] for line in segments])
        self.ends = np.asarray([list(line.coords)[1] for line in segments])

    def has_coverage(self, point):
        if self.tree is None:
            return False
        point = shapely.Point(point)
        nearest = int(self.tree.nearest(point))
        return self.segments[nearest].distance(point) <= 2000

    def sample(self, x, y):
        x, y = np.broadcast_arrays(x, y)
        result = np.zeros(x.size, dtype=bool)
        if self.tree is None:
            return result.reshape(x.shape)
        points = shapely.points(x.ravel(), y.ravel())
        nearest = self.tree.nearest(points)
        a, b = self.starts[nearest], self.ends[nearest]
        v = b - a
        cross = v[:, 0] * (y.ravel() - a[:, 1]) - v[:, 1] * (x.ravel() - a[:, 0])
        result[:] = cross < 0
        return result.reshape(x.shape)

    def rays(self, point, azimuths, distances):
        """Trace actual shoreline crossings; never extend its nearest tangent.

        The initial state is accepted only within 2km of a mapped shore.
        Once a ray exits land across a mapped coastline, it remains water until
        another mapped crossing. Missing islands remain a declared limitation.
        """
        output = np.zeros((len(azimuths), len(distances)), dtype=bool)
        if self.tree is None:
            return output
        start = np.asarray(point)
        if not self.has_coverage(start):
            return output
        initial_sea = bool(self.sample(np.asarray([start[0]]), np.asarray([start[1]]))[0])
        directions = np.column_stack((np.sin(np.radians(azimuths)), np.cos(np.radians(azimuths))))
        lines = shapely.linestrings(np.stack((np.broadcast_to(start, directions.shape), start+directions*distances[-1]), axis=1))
        pairs = self.tree.query(lines, predicate="intersects")
        for ray in range(len(azimuths)):
            crossings = []
            for segment_index in pairs[1, pairs[0] == ray]:
                intersection = lines[ray].intersection(self.segments[int(segment_index)])
                points = list(intersection.geoms) if hasattr(intersection, "geoms") else [intersection]
                for point_geometry in points:
                    if point_geometry.geom_type == "Point":
                        crossings.append(lines[ray].project(point_geometry))
            if crossings:
                unique = np.unique(np.round(crossings, 2))
                count = np.searchsorted(unique, distances, side="right")
                output[ray] = (count % 2 == 0) if initial_sea else (count % 2 == 1)
            elif initial_sea:
                output[ray] = True
        return output


def build_local_dsm(terrain: Grid, buildings: list[dict], center, *, radius_m: float = 1000, cell: float = 2):
    """Replace roof cells with base+height; never add OSM height onto a DSM roof.

    The base is a low percentile of coarse samples outside this footprint.
    This is a rough DTM proxy, not a bare-earth survey.
    """
    size = int(2 * radius_m / cell)
    x_min, y_max = center[0] - radius_m, center[1] + radius_m
    xs = x_min + (np.arange(size) + .5) * cell
    ys = y_max - (np.arange(size) + .5) * cell
    xx, yy = np.meshgrid(xs, ys)
    ground = terrain.sample(xx, yy).astype(np.float32)
    ids = np.full((size, size), -1, dtype=np.int32)
    # Tallest top wins in overlapping footprints, including building:part.
    tops = np.full((size, size), -np.inf, dtype=np.float32)
    union = shapely.union_all([building["shape"] for building in buildings])
    shapely.prepare(union)
    for index, building in enumerate(buildings):
        polygon = building["shape"]
        minx, miny, maxx, maxy = polygon.bounds
        c0, c1 = max(0, int((minx-x_min)/cell)), min(size, int((maxx-x_min)/cell)+2)
        r0, r1 = max(0, int((y_max-maxy)/cell)), min(size, int((y_max-miny)/cell)+2)
        if c0 >= c1 or r0 >= r1:
            continue
        ringx = np.arange(minx-60, maxx+61, terrain.cell)
        ringy = np.arange(miny-60, maxy+61, terrain.cell)
        rx, ry = np.meshgrid(ringx, ringy)
        outside = ~shapely.contains_xy(union, rx, ry)
        samples = terrain.sample(rx, ry)
        samples = samples[outside & np.isfinite(samples)]
        base = float(np.percentile(samples, 10)) if len(samples) else float(terrain.sample(np.array([polygon.centroid.x]), np.array([polygon.centroid.y]))[0])
        building["base_m"] = max(0.0, base) if np.isfinite(base) else np.nan
        mask = shapely.contains_xy(polygon, xx[r0:r1, c0:c1], yy[r0:r1, c0:c1])
        top = building["base_m"] + building["height_m"]
        mask &= top > tops[r0:r1, c0:c1]
        ids[r0:r1, c0:c1][mask] = index
        tops[r0:r1, c0:c1][mask] = top
        # Explicit replacement removes the original coarse roof contribution.
        ground[r0:r1, c0:c1][mask] = building["base_m"]
    dsm = ground.copy()
    mask = ids >= 0
    dsm[mask] = tops[mask]
    return Grid(ground, x_min, y_max, cell), ids, dsm
