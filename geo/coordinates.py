"""All metric geometry uses UTM 37N; public geometry is WGS84."""
from __future__ import annotations

import math

import numpy as np
import shapely
from pyproj import Transformer

TO_UTM = Transformer.from_crs(4326, 32637, always_xy=True)
TO_WGS = Transformer.from_crs(32637, 4326, always_xy=True)
SIDES = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _projection(transformer, x, y):
    # pyproj 3.7's point fast path casts length-one numpy arrays to scalars.
    if len(x) == 1:
        a, b = transformer.transform(float(x[0]), float(y[0]))
        return np.asarray([a]), np.asarray([b])
    return transformer.transform(x, y)


def metric(geometry):
    return shapely.transform(geometry, lambda x, y: _projection(TO_UTM, x, y), interleaved=False)


def geographic(geometry):
    return shapely.transform(geometry, lambda x, y: _projection(TO_WGS, x, y), interleaved=False)


def azimuth(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dx, dy)) % 360


def side(angle: float) -> str:
    return SIDES[int((angle + 22.5) % 360 // 45)]


def angle_difference(a, b):
    return np.abs((np.asarray(a) - b + 180) % 360 - 180)


def hotel_center(hotel: dict) -> tuple[float, float]:
    if hotel.get("lon") is not None:
        return float(hotel["lon"]), float(hotel["lat"])
    if hotel.get("longitude") is not None:
        return float(hotel["longitude"]), float(hotel["latitude"])
    coordinates = hotel.get("coordinates") or hotel.get("location")
    if isinstance(coordinates, dict):
        return float(coordinates.get("lon", coordinates.get("longitude"))), float(coordinates.get("lat", coordinates.get("latitude")))
    if coordinates:
        return float(coordinates[0]), float(coordinates[1])
    geometry = hotel.get("geometry", {})
    if geometry.get("type") == "Point":
        return tuple(geometry["coordinates"])
    raise ValueError("Hotel has no WGS84 coordinates")
