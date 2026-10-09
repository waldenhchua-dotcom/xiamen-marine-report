"""One request per marine variable so model-specific returned grids stay distinct."""

from __future__ import annotations

from ..config import ProviderSettings
from .common import decode_json, download, parse_open_meteo
from .weather import OPEN_METEO_LICENSE

MARINE_VARIABLES = {
    "wave_height": ("wave_height", "m", None, "meteofrance_wave", "3-hourly"),
    "swell_wave_height": ("swell_height", "m", None, "meteofrance_wave", "3-hourly"),
    "swell_wave_period": ("swell_period", "s", None, "meteofrance_wave", "3-hourly"),
    "ocean_current_velocity": ("current_speed", "m/s", None, "meteofrance_currents", "hourly"),
    "ocean_current_direction": ("current_direction", "degrees", "toward", "meteofrance_currents", "hourly"),
    "sea_surface_temperature": ("sea_temperature", "°C", None, "meteofrance_currents", "6-hourly"),
}


def fetch_marine(site: dict, request_date: str, end_date: str,
                 settings: ProviderSettings, *, timeout: float = 20) -> tuple[list, dict, list, list]:
    sources, raw, series, gaps = [], {}, [], []
    for api_variable, (variable, unit, direction, model, native_step) in MARINE_VARIABLES.items():
        source_id = f"marine_{site['id']}_{variable}"
        params = {"latitude": site["target_point"]["latitude"],
                  "longitude": site["target_point"]["longitude"], "hourly": api_variable,
                  "models": model, "timezone": "Asia/Shanghai", "start_date": request_date,
                  "end_date": end_date, "cell_selection": "sea", "length_unit": "metric"}
        if settings.api_key:
            params["apikey"] = settings.api_key
        response = download(settings.endpoint("/v1/marine", marine=True), params, timeout=timeout)
        source = response.source(source_id, kind="forecast", license_text=OPEN_METEO_LICENSE)
        source["metadata"].update({
            "provider": "Open-Meteo; Météo-France / Copernicus Marine",
            "documentation": "https://open-meteo.com/en/docs/marine-weather-api",
            "model_requested": model, "model_run": None, "model_run_verified": False,
            "model_identity_verification": "Explicit requested model; initialization time not returned",
            "native_temporal_resolution": native_step,
            "hourly_temporal_resolution": "Open-Meteo hourly output, interpolation where needed",
            "time_convention": "Local ISO strings, verified utc_offset_seconds=28800; stored with +08:00",
            "unit_conventions": "Returned hourly_units validated; current km/h divided by 3.6 into m/s",
            "direction_convention": "current toward, clockwise from true north",
            "coastal_suitability": "Unverified for enclosed Xiamen bays; blocks local safety conclusions",
            "access_mode": "commercial" if settings.commercial else "noncommercial",
        })
        try:
            obj = decode_json(response)
            rows = parse_open_meteo(obj, site, {api_variable: (variable, unit, direction)},
                                    source_id, request_date, resolution_km=8.0)
            source["metadata"].update({"units": obj["hourly_units"], "grid": rows[0]["grid"],
                                        "valid_from": rows[0]["timestamps"][0],
                                        "valid_to": rows[0]["timestamps"][-1]})
            series.extend(rows)
        except (ValueError, KeyError, TypeError):
            gaps.append({"code": "MARINE_DATA_UNAVAILABLE", "site_id": site["id"],
                         "message": f"Marine variable {variable} failed date, timezone, grid, unit or response checks",
                         "blocking": True})
        sources.append(source)
        raw[source_id] = response.raw
    gaps.append({"code": "COASTAL_MODEL_UNSUITABLE", "site_id": site["id"],
                 "message": "8 km open-ocean model applicability inside this bay is unverified; no local safety conclusion",
                 "blocking": True})
    return sources, raw, series, gaps
