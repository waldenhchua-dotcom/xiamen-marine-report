"""Deterministic astronomical estimates; no terrain or local weather correction."""

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from astral import Observer
from astral.sun import sunset


def sunset_for_date(site: dict[str, Any], day: str | date) -> dict[str, Any]:
    """Estimate sunset at the documented WGS84 target, with explicit limitations."""
    requested = date.fromisoformat(day) if isinstance(day, str) else day
    point = site["target_point"]
    value = sunset(
        Observer(latitude=float(point["latitude"]), longitude=float(point["longitude"]), elevation=0),
        date=requested,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    return {
        "site_id": site["id"],
        "date": requested.isoformat(),
        "time": value.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "kind": "astronomical",
        "algorithm": "Astral 3.2 sunset, observer elevation 0 m, standard refraction",
        "documentation": "https://astral.readthedocs.io/en/latest/",
        "limitations": "约/估算；未校正地形、实际地平线、天气、气温或气压；不是返岸时间建议。",
    }
