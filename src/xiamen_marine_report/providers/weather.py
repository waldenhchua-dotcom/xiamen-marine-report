"""Pinned ECMWF weather and separately attributed GFS precipitation probability."""

from __future__ import annotations

from ..config import ProviderSettings
from .common import decode_json, download, parse_open_meteo

WEATHER_VARIABLES = {
    "wind_speed_10m": ("wind_speed", "m/s", None),
    "wind_gusts_10m": ("wind_gust", "m/s", None),
    "wind_direction_10m": ("wind_direction", "degrees", "from"),
    "precipitation": ("precipitation", "mm", None),
    "weather_code": ("weather_code", "WMO code", None),
    "temperature_2m": ("temperature", "°C", None),
}
PROBABILITY_VARIABLES = {"precipitation_probability": ("precipitation_probability", "%", None)}
OPEN_METEO_LICENSE = (
    "Data CC BY 4.0; attribution Open-Meteo and originating model agencies. "
    "Free hosted endpoint: non-commercial only; paid customer endpoint for commercial use. "
    "https://open-meteo.com/en/terms"
)


def fetch_weather(site: dict, request_date: str, end_date: str,
                  settings: ProviderSettings, *, timeout: float = 20) -> tuple[list, dict, list, list]:
    sources, raw, series, gaps = [], {}, [], []
    specs = [
        ("ecmwf", "/v1/ecmwf", "ecmwf_ifs025", WEATHER_VARIABLES, 25.0,
         "ECMWF IFS 0.25; native 3/6-hourly, Open-Meteo interpolated hourly",
         "https://open-meteo.com/en/docs/ecmwf-api"),
        ("probability", "/v1/forecast", "gfs_seamless", PROBABILITY_VARIABLES, 25.0,
         "NOAA GFS/GEFS precipitation probability, separate from ECMWF; hourly interpolation",
         "https://open-meteo.com/en/docs/gfs-api"),
    ]
    for label, path, model, variables, resolution, model_description, docs in specs:
        source_id = f"weather_{site['id']}_{label}"
        params = {"latitude": site["target_point"]["latitude"],
                  "longitude": site["target_point"]["longitude"],
                  "hourly": ",".join(variables), "models": model,
                  "wind_speed_unit": "ms", "temperature_unit": "celsius",
                  "precipitation_unit": "mm", "timezone": "Asia/Shanghai",
                  "start_date": request_date, "end_date": end_date,
                  "cell_selection": "nearest"}
        if settings.api_key:
            params["apikey"] = settings.api_key
        response = download(settings.endpoint(path), params, timeout=timeout)
        source = response.source(source_id, kind="forecast", license_text=OPEN_METEO_LICENSE)
        source["metadata"].update({
            "provider": "Open-Meteo", "documentation": docs,
            "model_requested": model, "model_description": model_description,
            "model_run": None, "model_run_verified": False,
            "model_identity_verification": "Explicit requested model; initialization time not returned",
            "native_temporal_resolution": "3/6 hours" if label == "ecmwf" else "ensemble probability; see GFS documentation",
            "hourly_temporal_resolution": "Platform hourly output; not proof of native hourly simulation",
            "time_convention": "Local ISO strings, verified utc_offset_seconds=28800; stored with +08:00",
            "unit_conventions": "Returned hourly_units validated; speed converted to m/s when needed",
            "direction_convention": "wind from, clockwise from true north",
            "precipitation_convention": "Amount/probability of preceding hour, not a daily accumulation",
            "access_mode": "commercial" if settings.commercial else "noncommercial",
        })
        try:
            obj = decode_json(response)
            rows = parse_open_meteo(obj, site, variables, source_id, request_date,
                                    resolution_km=resolution)
            source["metadata"].update({"units": obj["hourly_units"],
                                        "grid": rows[0]["grid"],
                                        "valid_from": rows[0]["timestamps"][0],
                                        "valid_to": rows[0]["timestamps"][-1]})
            series.extend(rows)
        except (ValueError, KeyError, TypeError):
            gaps.append({"code": "WEATHER_DATA_UNAVAILABLE", "site_id": site["id"],
                         "message": f"Weather source {label} failed date, timezone, grid, unit or response checks",
                         "blocking": True})
        sources.append(source)
        raw[source_id] = response.raw
    return sources, raw, series, gaps
