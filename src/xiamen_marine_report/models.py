"""Shared deterministic contracts and time/unit helpers."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

SHANGHAI = timezone(timedelta(hours=8))
REQUIRED_VARIABLES = (
    "tide_height", "wind_speed", "wind_gust", "wind_direction",
    "precipitation", "precipitation_probability", "weather_code", "temperature",
    "wave_height", "swell_height", "swell_period", "current_speed",
    "current_direction", "sea_temperature",
)
EXPECTED_UNITS = {
    "tide_height": {"m"}, "wind_speed": {"m/s"}, "wind_gust": {"m/s"},
    "wind_direction": {"degrees", "degree", "deg", "°"},
    "precipitation": {"mm"}, "precipitation_probability": {"%"},
    "weather_code": {"WMO code", "wmo code", "code"},
    "temperature": {"°C", "C", "degC", "celsius"},
    "wave_height": {"m"}, "swell_height": {"m"}, "swell_period": {"s"},
    "current_speed": {"m/s"}, "current_direction": {"degrees", "degree", "deg", "°"},
    "sea_temperature": {"°C", "C", "degC", "celsius"},
}
REVIEW_CHECKLIST = (
    "request_and_source_dates", "source_hashes", "tide_extrema_and_hourly_values",
    "wind_and_gust_peaks", "hourly_and_total_precipitation",
    "weather_codes_and_official_warnings", "spatial_applicability",
    "three_flood_segments_cross_day_coverage", "tide_and_current_distinction",
    "all_pdf_pages_and_three_pngs_readable",
)


def parse_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if value != parsed.isoformat():
        raise ValueError("Date must use YYYY-MM-DD")
    return parsed


def parse_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO string with explicit UTC offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp lacks an independently verified explicit UTC offset")
    return parsed


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def haversine_km(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    a, b = math.radians(a_lat), math.radians(b_lat)
    dlat, dlon = b - a, math.radians(b_lon - a_lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(h)))

