"""Select complete low-to-immediately-next-high intervals in Beijing time."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from .models import SHANGHAI, finite_number, parse_date, parse_time


def hourly_end(value: str | datetime) -> str:
    timestamp = parse_time(value) if isinstance(value, str) else value
    if timestamp.tzinfo is None:
        raise ValueError("High tide requires explicit UTC offset")
    local = timestamp.astimezone(SHANGHAI)
    return (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).isoformat()


def select_flood_segments(extrema: list[dict[str, Any]], request_date: str,
                          station_id: str) -> list[dict[str, Any]]:
    day = parse_date(request_date)
    entries = [entry for entry in extrema if entry.get("station_id") == station_id]
    if not entries:
        raise ValueError(f"No extrema for station {station_id}")
    parsed: list[tuple[datetime, dict[str, Any]]] = []
    for entry in entries:
        timestamp = parse_time(entry.get("time", "")).astimezone(SHANGHAI)
        if entry.get("type") not in {"low", "high"} or not finite_number(entry.get("height")):
            raise ValueError("Malformed tide extremum")
        parsed.append((timestamp, entry))
    parsed.sort(key=lambda item: item[0])
    for previous, current in zip(parsed, parsed[1:]):
        if previous[0] == current[0]:
            raise ValueError("Duplicate extremum timestamp")
        if previous[1]["type"] == current[1]["type"]:
            raise ValueError("Extrema must alternate low and high")
    midnight = datetime.combine(day, time(), tzinfo=SHANGHAI)
    starts = [index for index, (timestamp, entry) in enumerate(parsed)
              if timestamp >= midnight and entry["type"] == "low"]
    if not starts or parsed[starts[0]][0].date() != day:
        raise ValueError("Target date has no independently verified low tide")
    segments: list[dict[str, Any]] = []
    for index in starts:
        if index + 1 >= len(parsed):
            break
        low_time, low = parsed[index]
        high_time, high = parsed[index + 1]
        if high["type"] != "high" or high_time <= low_time:
            raise ValueError("Low tide must pair with immediately next high")
        if high["height"] <= low["height"]:
            raise ValueError("High tide must exceed preceding low height")
        segments.append({"low": dict(low), "high": dict(high),
                         "start": low_time.isoformat(), "end": high_time.isoformat()})
        if len(segments) == 3:
            return segments
    raise ValueError("Three complete flood segments require cross-day tide extrema")
