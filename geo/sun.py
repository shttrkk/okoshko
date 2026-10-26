"""Monthly solar geometry; every value is tied to a representative date."""
from __future__ import annotations

from datetime import UTC, date, timedelta

import numpy as np
from astral import Observer
from astral.sun import azimuth, elevation, sunrise, sunset

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


def monthly_exposure_paths(lon, lat, *, period, year=2026, sample_minutes=5):
    """Three-hour daylight windows, sampled at interval midpoints in UTC.

    These are solar geometry, without clouds, glass, room ventilation or a
    thermal model. The representative date is always explicit in the output.
    """
    if period not in {"morning", "evening"}:
        raise ValueError("period must be morning or evening")
    if not isinstance(sample_minutes, int) or sample_minutes <= 0 or 180 % sample_minutes:
        raise ValueError("sample_minutes must divide 180")
    observer = Observer(latitude=lat, longitude=lon)
    paths = []
    for month in range(1, 13):
        day = date(year, month, 15)
        rise = sunrise(observer, date=day, tzinfo=UTC)
        setting = sunset(observer, date=day, tzinfo=UTC)
        start = rise if period == "morning" else setting-timedelta(hours=3)
        end = min(rise+timedelta(hours=3), setting) if period == "morning" else setting
        points = []
        when = start+timedelta(minutes=sample_minutes/2)
        while when < end:
            points.append({"azimuth_deg": round(azimuth(observer, when), 3),
                           "elevation_deg": round(elevation(observer, when, with_refraction=False), 3),
                           "time": when.isoformat()})
            when += timedelta(minutes=sample_minutes)
        paths.append({"month": month, "date": day.isoformat(), "period": period,
                      "start": start.isoformat(), "end": end.isoformat(),
                      "sample_minutes": sample_minutes, "points": points})
    return paths


def facade_exposure(path, ray_azimuths, horizons, facade_azimuth, *, window_deg=75):
    """Visible direct-sun minutes and dimensionless vertical-facade incidence.

    The index averages max(0, cos(elevation)*cos(relative azimuth)) over the
    window, with blocked samples set to zero. It is not irradiance or degrees C.
    Missing horizon cells remain unknown instead of being treated as clear sky.
    """
    horizons = np.asarray(horizons, dtype=float)
    if horizons.ndim != 2 or horizons.shape[1] != len(ray_azimuths):
        raise ValueError("horizons must have one column per azimuth")
    minutes = np.zeros(horizons.shape[0])
    exposure = np.zeros(horizons.shape[0])
    known = np.ones(horizons.shape[0], dtype=bool)
    for point in path["points"]:
        difference = float(angle_difference(point["azimuth_deg"], facade_azimuth))
        if point["elevation_deg"] <= 0 or difference > window_deg:
            continue
        nearest = int(np.argmin(angle_difference(ray_azimuths, point["azimuth_deg"])))
        known &= np.isfinite(horizons[:, nearest])
        visible = point["elevation_deg"] > horizons[:, nearest]
        minutes += visible*path["sample_minutes"]
        incidence = max(0, np.cos(np.radians(point["elevation_deg"]))*np.cos(np.radians(difference)))
        exposure += visible*incidence
    exposure /= max(len(path["points"]), 1)
    return [{"visible_minutes": round(float(m), 1), "exposure_index": round(float(e), 4)} if k else None
            for m, e, k in zip(minutes, exposure, known, strict=True)]


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
