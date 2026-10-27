"""Daily climatology definitions; model sample counts are not WMO normals."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date


def climate_days(daily, *, sunny_hours=6, rainy_mm=1, wet_month_days=12):
    months = []
    for month in range(1, 13):
        years = defaultdict(lambda: {"sunny": 0, "rainy": 0, "sun_samples": 0, "rain_samples": 0})
        for index, day in enumerate(daily.get("time", [])):
            stamp = date.fromisoformat(day)
            if stamp.month != month:
                continue
            values = years[stamp.year]
            for field, count, samples, threshold in [("sunshine_duration", "sunny", "sun_samples", sunny_hours*3600),
                                                      ("precipitation_sum", "rainy", "rain_samples", rainy_mm)]:
                series = daily.get(field, [])
                value = series[index] if index < len(series) else None
                if value is not None:
                    values[count] += value >= threshold
                    values[samples] += 1
        complete_sun = [r["sunny"] for year, r in years.items() if r["sun_samples"] == calendar.monthrange(year, month)[1]]
        complete_rain = [r["rainy"] for year, r in years.items() if r["rain_samples"] == calendar.monthrange(year, month)[1]]
        sunny = round(sum(complete_sun)/len(complete_sun), 2) if complete_sun else None
        rainy = round(sum(complete_rain)/len(complete_rain), 2) if complete_rain else None
        months.append({"month": month, "available": True if sunny is not None and rainy is not None else None,
                       "sunny_days": sunny, "rainy_days": rainy, "wet_month_proxy": rainy >= wet_month_days if rainy is not None else None,
                       "sunny_day_threshold_hours": sunny_hours, "rainy_day_threshold_mm": rainy_mm, "wet_month_threshold_days": wet_month_days,
                       "annual_samples": dict(years), "complete_sun_years": len(complete_sun), "complete_rain_years": len(complete_rain)})
    return months


def water_comfort(water_c, *, comfortable_c=22, cool_c=18):
    if water_c is None:
        return None
    return "обычно комфортно для купания" if water_c >= comfortable_c else "прохладная вода" if water_c >= cool_c else "холодная вода"
