"""Public planning points and environment-only provider configuration.

These approximate WGS84 points identify report areas. They are not surveyed
launch locations or tide-station coordinates.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from datetime import date

TIMEZONE = "Asia/Shanghai"

_SITES = {
    "haicang": ("海沧湾", 24.490, 118.040, "T122", "厦门（待核参考站）"),
    "wuyuan": ("五缘湾", 24.534, 118.169, "NT1405", "翔安（待核参考站）"),
    "huandong": ("环东", 24.603, 118.174, "NT1405", "翔安（待核参考站）"),
}


def selected_sites(site_ids: list[str]) -> list[dict]:
    """Return independent site records; reject duplicates and unknown names."""
    if not site_ids or len(set(site_ids)) != len(site_ids):
        raise ValueError("Select at least one unique site")
    sites = []
    for site_id in site_ids:
        if site_id not in _SITES:
            raise ValueError(f"Unknown site: {site_id}")
        name, lat, lon, station_id, station_name = _SITES[site_id]
        sites.append({
            "id": site_id,
            "name": name,
            "target_point": {
                "latitude": lat, "longitude": lon, "crs": "WGS84",
                "precision": "approximate planning point",
                "provenance": "Project planning point, manually chosen; not a surveyed launch site",
                "limitations": "Approximate area marker only; no surveyed accuracy, launch permission or local safety applicability",
            },
            "reference_station": {
                "id": station_id, "name": station_name,
                "latitude": None, "longitude": None, "datum": "unverified",
                "timezone_verified": False, "station_relation_verified": False,
                "provenance": "Candidate station from the project brief; requires independent official evidence",
                "limitations": "Reference station only; TimeArea symbol, source coordinate CRS and bay applicability remain unverified",
            },
        })
    return copy.deepcopy(sites)


def parse_request_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Date must have YYYY-MM-DD format")
    return parsed


@dataclass(frozen=True)
class ProviderSettings:
    """Credentials stay outside serialized configuration and repr()."""

    commercial: bool = False
    api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, commercial: bool = False) -> ProviderSettings:
        mode = os.environ.get("MARINE_REPORT_OPEN_METEO_MODE", "noncommercial")
        if mode not in {"noncommercial", "commercial"}:
            raise ValueError("MARINE_REPORT_OPEN_METEO_MODE must be noncommercial or commercial")
        paid = commercial or mode == "commercial"
        key = os.environ.get("MARINE_REPORT_OPEN_METEO_API_KEY") if paid else None
        if paid and not key:
            raise ValueError("Commercial mode requires MARINE_REPORT_OPEN_METEO_API_KEY")
        return cls(commercial=paid, api_key=key)

    def endpoint(self, path: str, *, marine: bool = False) -> str:
        host = "marine-api.open-meteo.com" if marine else "api.open-meteo.com"
        if self.commercial:
            host = "customer-" + host
        return f"https://{host}{path}"
