"""Monthly solar geometry; every value is tied to a representative date."""
from __future__ import annotations

from datetime import UTC, date, timedelta

import numpy as np
from astral import Observer
from astral.sun import azimuth, elevation, sunset

from geo.coordinates import angle_difference
from geo.views import CURVATURE, EARTH_RADIUS_M


def monthly_paths(lon, lat, *, year=2026):
    observer = Observer(latitude=lat, longitude=lon)
    paths = []
    for month in range(1, 13):
        day = date(year, month, 15)
        end = sunset(observer, date=day, tzinfo=UTC)
        points = []
        for minutes in range(-50, 5, 2):
            when = end + timedelta(minutes=minutes)
            points.append({"azimuth_deg": round(azimuth(observer, when), 3),
                           "elevation_deg": round(elevation(observer, when, with_refraction=False), 3),
                           "time": when.isoformat(), "minutes_to_sunset": -minutes})
        paths.append({"month": month, "date": day.isoformat(), "sample_minutes": 2, "points": points})
    return paths


def horizon_by_floor(ground, bids, distances, heights, bases, eyes):
    surface = ground.astype(float).copy()
    mask = bids >= 0
    surface[mask] = bases[bids[mask]] + heights[bids[mask]]
    surface -= CURVATURE*distances[None, :]**2/(2*EARTH_RADIUS_M)
    values = np.degrees(np.arctan((surface[None, :, :]-eyes[:, None, None])/distances[None, None, :]))
    values = np.where(np.isfinite(values), values, -90)
    return np.max(values, axis=2)


def visible_sunset(paths, ray_azimuths, horizons, facade_azimuth, *, window_deg=75):
    output = []
    for path in paths:
        minutes = np.zeros(horizons.shape[0])
        sunset_azimuth = path["points"][-3]["azimuth_deg"]
        for point in path["points"]:
            # Sunset experience: the last 5 degrees above the geometric horizon.
            if point["elevation_deg"] < -.27 or point["elevation_deg"] > 5:
                continue
            if angle_difference(point["azimuth_deg"], facade_azimuth) > window_deg:
                continue
            nearest = int(np.argmin(angle_difference(ray_azimuths, point["azimuth_deg"])))
            minutes += (point["elevation_deg"]+.27 > horizons[:, nearest]) * path["sample_minutes"]
        output.append({"month": path["month"], "date": path["date"],
                       "azimuth_deg": sunset_azimuth, "visible_minutes": minutes.tolist(),
                       "available": (minutes >= 4).tolist()})
    return output
