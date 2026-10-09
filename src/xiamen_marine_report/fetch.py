"""Deterministic automatic acquisition into a new immutable evidence package."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ProviderSettings, parse_request_date, selected_sites
from .coverage import hourly_end, select_flood_segments
from .provenance import save_run
from .providers.evidence import load_evidence
from .providers.marine import fetch_marine
from .providers.official import fetch_official
from .providers.tide import fetch_tides
from .providers.weather import fetch_weather


def fetch_run(request_date: str, site_ids: list[str], output_root: Path, *,
              timeout: float = 20, evidence_dir: Path | None = None,
              commercial: bool = False) -> Path:
    """Automatically acquire tides, weather, marine variables and official pages.

    No AI is called. Semantic gaps block complete output while retaining genuine
    downloaded bytes. The default probe is the requested date plus two dates;
    verified imported extrema can determine a later per-site forecast endpoint.
    Each invocation creates a unique run even after a previous blocked fetch.
    """
    day = parse_request_date(request_date)
    sites = selected_sites(site_ids)
    settings = ProviderSettings.from_environment(commercial)
    if timeout <= 0 or timeout > 120:
        raise ValueError("Timeout must be in (0,120]")
    manifest = {
        "schema_version": 1, "request_date": request_date, "timezone": "Asia/Shanghai",
        "state": "FETCHED", "synthetic": False, "analysis_provider": "none",
        "created_at": datetime.now(timezone.utc).isoformat(), "sites": sites,
        "sources": [], "extrema": [], "series": [], "official_documents": [], "gaps": [],
        "acquisition": {"mode": "automatic_public_providers",
                        "probe_start": request_date,
                        "probe_end": (day + timedelta(days=2)).isoformat(),
                        "open_meteo_access": "commercial" if settings.commercial else "noncommercial"},
    }
    raw_objects = {}
    imported = None
    if evidence_dir is not None:
        imported, imported_raw = load_evidence(Path(evidence_dir), request_date, site_ids)
        for update in imported.get("stations", []):
            site = next(site for site in sites if site["id"] == update["site_id"])
            allowed = {key: value for key, value in update.items() if key != "site_id"}
            site["reference_station"] = allowed
        manifest["extrema"].extend(imported.get("extrema", []))
        manifest["series"].extend(imported.get("series", []))
        manifest["sources"].extend(imported["sources"])
        raw_objects.update(imported_raw)
        manifest["acquisition"]["local_evidence_imported"] = True

    sources, raw, quarantined, gaps = fetch_tides(sites, request_date, timeout=timeout)
    manifest["sources"].extend(sources)
    raw_objects.update(raw)
    manifest["quarantined_tides"] = quarantined
    manifest["gaps"].extend(gaps)
    if imported:
        # Automatic observations are retained in their source metadata. Reviewed
        # semantic/CRS evidence is applied after the unqualified automatic data.
        for update in imported.get("stations", []):
            site = next(site for site in sites if site["id"] == update["site_id"])
            site["reference_station"] = {key: value for key, value in update.items() if key != "site_id"}
    for site in sites:
        end_date = (day + timedelta(days=2)).isoformat()
        station = site["reference_station"]
        if station.get("timezone_verified"):
            try:
                segments = select_flood_segments(manifest["extrema"], request_date, station["id"])
                endpoint = hourly_end(segments[-1]["end"])
                end_date = endpoint[:10] if isinstance(endpoint, str) else endpoint.date().isoformat()
            except (ValueError, KeyError, TypeError):
                manifest["gaps"].append({"code": "BLOCKED_CROSS_DAY_DATA", "site_id": site["id"],
                                         "message": "Verified imported tides do not determine three complete flood segments",
                                         "blocking": True})
        for provider in (fetch_weather, fetch_marine):
            sources, raw, rows, gaps = provider(site, request_date, end_date, settings, timeout=timeout)
            existing = {(row["site_id"], row["variable"]) for row in manifest["series"]}
            manifest["series"].extend(row for row in rows if (row["site_id"], row["variable"]) not in existing)
            manifest["sources"].extend(sources)
            raw_objects.update(raw)
            manifest["gaps"].extend(gaps)
    sources, raw, documents, gaps = fetch_official(sites, request_date, timeout=timeout)
    manifest["sources"].extend(sources)
    raw_objects.update(raw)
    imported_documents = imported.get("official_documents", []) if imported else []
    provided = {(doc["site_id"], doc["category"]) for doc in imported_documents}
    manifest["official_documents"].extend(imported_documents)
    manifest["official_documents"].extend(doc for doc in documents if (doc["site_id"], doc["category"]) not in provided)
    manifest["gaps"].extend(gaps)
    if imported:
        resolved = set()
        for site in sites:
            station = site["reference_station"]
            if station.get("timezone_verified") and station.get("timezone_evidence_source_id"):
                resolved.add((site["id"], "TIDE_TIMEZONE_UNVERIFIED"))
            if (station.get("station_relation_verified") and station.get("relation_evidence_source_id")
                    and station.get("coordinate_crs") == "WGS84"):
                resolved.add((site["id"], "STATION_BAY_RELATION_UNVERIFIED"))
            if all(any(doc["site_id"] == site["id"] and doc["category"] == category
                       and doc["status"] == "verified" for doc in imported_documents)
                   for category in ("warning", "marine_forecast")):
                resolved.add((site["id"], "OFFICIAL_BODY_NOT_VERIFIED"))
        manifest["gaps"] = [gap for gap in manifest["gaps"] if (gap.get("site_id"), gap["code"]) not in resolved]
    # Offshore-grid applicability is an inherent outstanding gap; an import
    # cannot remove it by setting a generic boolean.
    return save_run(output_root, manifest, raw_objects)
