"""Fail-closed deterministic evidence and full-range coverage gate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .coverage import hourly_end, select_flood_segments
from .models import (
    EXPECTED_UNITS,
    REQUIRED_VARIABLES,
    SHANGHAI,
    finite_number,
    haversine_km,
    parse_date,
    parse_time,
)
from .provenance import canonical_digest, load_manifest, safe_relative_path, sanitize_request, sha256_file

API_VARIABLES = {
    "wind_speed": "wind_speed_10m", "wind_gust": "wind_gusts_10m",
    "wind_direction": "wind_direction_10m", "precipitation": "precipitation",
    "precipitation_probability": "precipitation_probability", "weather_code": "weather_code",
    "temperature": "temperature_2m", "wave_height": "wave_height",
    "swell_height": "swell_wave_height", "swell_period": "swell_wave_period",
    "current_speed": "ocean_current_velocity", "current_direction": "ocean_current_direction",
    "sea_temperature": "sea_surface_temperature",
}


def _series_matches_raw(series: dict[str, Any], raw: Any) -> bool:
    """Reconstruct normalized numbers from frozen bytes, never from a manifest hash alone."""
    if not isinstance(raw, dict):
        return False
    envelope = raw.get("normalized", raw)
    if isinstance(envelope, dict) and isinstance(envelope.get("series"), list):
        return any(isinstance(item, dict) and canonical_digest(item) == canonical_digest(series)
                   for item in envelope["series"])
    variable = API_VARIABLES.get(series.get("variable"))
    hourly, units = raw.get("hourly"), raw.get("hourly_units")
    if not variable or not isinstance(hourly, dict) or not isinstance(units, dict):
        return False
    if raw.get("timezone") != "Asia/Shanghai" or raw.get("utc_offset_seconds") != 28800:
        return False
    times, values = hourly.get("time"), hourly.get(variable)
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        return False
    converted_times = []
    try:
        for value in times:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=SHANGHAI)
            converted_times.append(timestamp.astimezone(SHANGHAI).isoformat())
        recorded_times = [parse_time(value).astimezone(SHANGHAI).isoformat() for value in series["timestamps"]]
    except (ValueError, TypeError, KeyError):
        return False
    aliases = {"°": "degrees", "wmo code": "WMO code"}
    raw_unit = aliases.get(units.get(variable), units.get(variable))
    factor = 1 / 3.6 if series.get("unit") == "m/s" and raw_unit == "km/h" else 1
    normalized_unit = "m/s" if factor != 1 else raw_unit
    if converted_times != recorded_times or normalized_unit != series.get("unit"):
        return False
    recorded = series.get("values", [])
    if len(recorded) != len(values):
        return False
    for original, normalized in zip(values, recorded):
        if original is None or normalized is None:
            if original is not normalized:
                return False
        elif not finite_number(original) or not finite_number(normalized) or abs(original * factor - normalized) > 1e-8:
            return False
    grid = series.get("grid", {})
    return (isinstance(grid, dict) and finite_number(raw.get("latitude")) and finite_number(raw.get("longitude"))
            and grid.get("latitude") == raw["latitude"] and grid.get("longitude") == raw["longitude"])


def _record_matches_raw(record: dict[str, Any], raw: Any, key: str) -> bool:
    if not isinstance(raw, dict):
        return False
    envelope = raw.get("normalized", raw)
    if not isinstance(envelope, dict):
        return False
    values = envelope.get(key)
    if isinstance(values, dict):
        values = [values]
    return isinstance(values, list) and any(isinstance(value, dict)
                                           and canonical_digest(value) == canonical_digest(record) for value in values)


def _station_matches_raw(station: dict[str, Any], site_id: str, raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    envelope = raw.get("normalized", raw)
    if not isinstance(envelope, dict):
        return False
    singular = envelope.get("station")
    if isinstance(singular, dict) and canonical_digest(singular) == canonical_digest(station):
        return True
    records = envelope.get("stations", [])
    if not isinstance(records, list):
        return False
    return any(isinstance(record, dict) and record.get("site_id") == site_id
               and canonical_digest({key: value for key, value in record.items() if key != "site_id"})
               == canonical_digest(station) for record in records)


def _validate_run(run_dir: Path | str) -> dict[str, Any]:
    run_dir = Path(run_dir)
    errors: list[str] = []
    warnings: list[str] = []
    coverage: dict[str, Any] = {}
    cross_day = False
    digest: str | None = None
    try:
        digest = sha256_file(run_dir / "manifest.json")
        manifest = load_manifest(run_dir)
        day = parse_date(manifest.get("request_date", ""))
    except (OSError, ValueError, TypeError) as exc:
        return {"state": "BLOCKED", "errors": [f"Invalid manifest: {type(exc).__name__}"],
                "warnings": [], "coverage": {}, "manifest_sha256": digest}
    if manifest.get("schema_version") != 1 or manifest.get("timezone") != "Asia/Shanghai":
        errors.append("Unsupported schema or timezone")
    if manifest.get("state") != "FETCHED":
        errors.append("Frozen manifest must have FETCHED state")
    if not isinstance(manifest.get("synthetic"), bool):
        errors.append("Synthetic classification must be explicit")
    if manifest.get("synthetic"):
        warnings.append("SYNTHETIC: marine data, stations and grids are synthetic test values, not a marine forecast; static OpenStreetMap geography is real")
    if manifest.get("analysis_provider") != "none":
        errors.append("This deterministic release supports analysis_provider=none only")
    try:
        created_at = parse_time(manifest.get("created_at", ""))
        if created_at.utcoffset() != timedelta(0):
            errors.append("Evidence creation time must be UTC")
    except (ValueError, TypeError):
        errors.append("Invalid evidence creation time")
    sources: dict[str, Any] = {}
    raw_sources: dict[str, Any] = {}
    referenced_source_ids: set[str] = set()
    for key in ("series", "extrema", "official_documents"):
        records = manifest.get(key, [])
        if isinstance(records, list):
            referenced_source_ids.update(record["source_id"] for record in records
                                         if isinstance(record, dict) and isinstance(record.get("source_id"), str))
    if isinstance(manifest.get("sites"), list):
        for site in manifest["sites"]:
            station = site.get("reference_station", {}) if isinstance(site, dict) else {}
            if isinstance(station, dict) and isinstance(station.get("source_id"), str):
                referenced_source_ids.add(station["source_id"])
    source_items = manifest.get("sources", [])
    if not isinstance(source_items, list) or not source_items:
        errors.append("No source evidence")
        source_items = []
    for source in source_items:
        if not isinstance(source, dict):
            errors.append("Malformed source record")
            continue
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id or source_id in sources:
            errors.append("Missing or duplicate source ID")
            continue
        sources[source_id] = source
        source_metadata = source.get("metadata", {})
        optional = (isinstance(source_metadata, dict) and source_metadata.get("required_for_coverage") is False
                    and source_id not in referenced_source_ids)
        try:
            path = safe_relative_path(run_dir, source.get("file", ""))
            if not path.is_file() or sha256_file(path) != source.get("sha256"):
                errors.append(f"Source {source_id}: raw evidence SHA-256 mismatch or missing file")
            if path.is_file():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    raw = None  # HTML/PDF evidence may be frozen in the same package.
                raw_sources[source_id] = raw
                if isinstance(raw, dict) and (raw.get("State") is False or ("Data" in raw and raw["Data"] is None)):
                    (warnings if optional else errors).append(f"Source {source_id}: HTTP success does not establish data availability"
                                                             + (" (optional unused probe)" if optional else ""))
                    if not optional:
                        cross_day = True
        except (ValueError, OSError):
            errors.append(f"Source {source_id}: unsafe or unreadable evidence file")
        status = source.get("http_status")
        if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status < 300:
            (warnings if optional else errors).append(f"Source {source_id}: HTTP status is not successful"
                                                     + (" (optional unused probe)" if optional else ""))
        try:
            if parse_time(source.get("retrieved_at", "")).utcoffset() != timedelta(0):
                errors.append(f"Source {source_id}: retrieval time must be UTC")
        except (ValueError, TypeError):
            errors.append(f"Source {source_id}: retrieval UTC time missing")
        if source.get("kind") not in {"forecast", "synthetic", "astronomical", "official", "observed", "historical", "cartographic", "map"}:
            errors.append(f"Source {source_id}: unknown data kind")
        if source.get("kind") == "synthetic" and manifest.get("synthetic") is not True:
            errors.append(f"Source {source_id}: synthetic evidence in a real-data run")
        if not source.get("license") or not isinstance(source.get("metadata"), dict):
            errors.append(f"Source {source_id}: missing license or source metadata")
        if not isinstance(source.get("request"), dict) or not source["request"].get("url"):
            errors.append(f"Source {source_id}: request URL provenance missing")
        elif sanitize_request(source["request"]) != source["request"]:
            errors.append(f"Source {source_id}: unsanitized credentials or private URL in provenance")
        metadata = source.get("metadata", {})
        if not isinstance(metadata, dict) or not metadata.get("time_convention"):
            errors.append(f"Source {source_id}: time convention not documented")
    gaps = manifest.get("gaps", [])
    if not isinstance(gaps, list):
        errors.append("Malformed gap list")
        gaps = []
    for gap in gaps:
        if not isinstance(gap, dict):
            errors.append("Malformed gap record")
            continue
        message = f"{gap.get('site_id') or 'run'}: {gap.get('code', 'GAP')} — {gap.get('message', 'missing evidence')}"
        (errors if gap.get("blocking", True) else warnings).append(message)
        if gap.get("blocking", True) and any(word in gap.get("code", "").upper()
                                             for word in ("CROSS_DAY", "TIDE_UNAVAILABLE", "TIDE_MISSING")):
            cross_day = True
    sites = manifest.get("sites", [])
    if not isinstance(sites, list) or not sites:
        errors.append("No selected sites")
        sites = []
    site_ids: set[str] = set()
    extrema = manifest.get("extrema", [])
    all_series = manifest.get("series", [])
    official = manifest.get("official_documents", [])
    if not isinstance(extrema, list) or not isinstance(all_series, list) or not isinstance(official, list):
        errors.append("Extrema, series and official documents must be arrays")
        extrema, all_series, official = [], [], []
    for row in all_series:
        if not isinstance(row, dict) or row.get("variable") not in REQUIRED_VARIABLES:
            errors.append("Unrecognized or malformed series variable cannot be reported")
    for extremum in extrema:
        if not isinstance(extremum, dict):
            errors.append("Malformed extremum record")
            continue
        if extremum.get("source_id") not in sources or extremum.get("unit") != "m" or not extremum.get("datum"):
            errors.append("Tide extremum requires a verified source, metre unit and datum")
        elif not _record_matches_raw(extremum, raw_sources.get(extremum["source_id"]), "extrema"):
            errors.append("Tide extremum does not match its independently frozen normalized evidence")
    for site in sites:
        if not isinstance(site, dict):
            errors.append("Malformed site record")
            continue
        site_id = site.get("id")
        if site_id not in {"haicang", "wuyuan", "huandong"} or site_id in site_ids:
            errors.append("Unknown or duplicate site ID")
            continue
        site_ids.add(site_id)
        point = site.get("target_point", {})
        station = site.get("reference_station", {})
        if not isinstance(point, dict) or not isinstance(station, dict):
            errors.append(f"{site_id}: target or station metadata invalid")
            continue
        point_valid = (finite_number(point.get("latitude")) and -90 <= point["latitude"] <= 90
                       and finite_number(point.get("longitude")) and -180 <= point["longitude"] <= 180)
        if not point_valid or point.get("crs") != "WGS84" or not point.get("precision"):
            errors.append(f"{site_id}: target WGS84 coordinate or precision missing")
        if not point.get("provenance") or not point.get("limitations"):
            errors.append(f"{site_id}: coordinate provenance and limitations missing")
        if station.get("timezone_verified") is not True:
            errors.append(f"{site_id}: reference station timezone unverified; TimeArea is not an ISO offset")
        if not station.get("datum") or str(station.get("datum")).lower() in {"unknown", "unverified"}:
            errors.append(f"{site_id}: reference station tide datum unverified")
        if not (finite_number(station.get("latitude")) and finite_number(station.get("longitude"))):
            errors.append(f"{site_id}: reference station position unverified")
        station_source = station.get("source_id")
        if (station_source not in sources or not _station_matches_raw(station, site_id, raw_sources.get(station_source))):
            errors.append(f"{site_id}: station coordinates/datum/timezone not bound to independently frozen station evidence")
        elif sources[station_source].get("kind") not in {"official", "synthetic"}:
            errors.append(f"{site_id}: station verification requires official or explicitly synthetic fixture evidence")
        if not station.get("provenance") or not station.get("limitations"):
            errors.append(f"{site_id}: station provenance and limitations missing")
        warnings.append(f"{site_id}: reference-station tide height is neither bay water depth nor slack current")
        try:
            segments = select_flood_segments([item for item in extrema if isinstance(item, dict)],
                                            day.isoformat(), station.get("id", ""))
            start = parse_time(segments[0]["start"]).replace(minute=0, second=0, microsecond=0)
            end = parse_time(hourly_end(segments[-1]["end"]))
            coverage[site_id] = {"start": start.isoformat(), "end": end.isoformat(), "segments": segments}
        except (ValueError, TypeError, KeyError):
            errors.append(f"{site_id}: three complete low-to-next-high tide segments unavailable or malformed")
            # A quarantined clock convention cannot establish a cross-day absence.
            # Required next-day HTTP/gap failures still set cross_day independently.
            if station.get("timezone_verified") is True:
                cross_day = True
            start, end = None, None
        selected = [item for item in all_series if isinstance(item, dict) and item.get("site_id") == site_id]
        for variable in REQUIRED_VARIABLES:
            matches = [item for item in selected if item.get("variable") == variable]
            if len(matches) != 1:
                errors.append(f"{site_id}/{variable}: exactly one independently sourced series is required")
                if start is not None:
                    cross_day = True
                continue
            item = matches[0]
            source_id = item.get("source_id")
            if source_id not in sources:
                errors.append(f"{site_id}/{variable}: source evidence missing")
            elif not _series_matches_raw(item, raw_sources.get(source_id)):
                errors.append(f"{site_id}/{variable}: normalized values or metadata do not match frozen source bytes")
            if item.get("unit") not in EXPECTED_UNITS[variable]:
                errors.append(f"{site_id}/{variable}: wrong or unspecified units")
            if item.get("kind") not in {"forecast", "synthetic", "observed", "historical", "astronomical"}:
                errors.append(f"{site_id}/{variable}: unknown data classification")
            if item.get("kind") == "synthetic" and manifest.get("synthetic") is not True:
                errors.append(f"{site_id}/{variable}: synthetic series in real-data run")
            expected_direction = {"wind_direction": "from", "current_direction": "toward"}.get(variable)
            if expected_direction and item.get("direction_convention") != expected_direction:
                errors.append(f"{site_id}/{variable}: direction must be {expected_direction}")
            if variable == "tide_height" and item.get("datum", station.get("datum")) != station.get("datum"):
                errors.append(f"{site_id}/{variable}: curve and extrema datum mismatch")
            grid = item.get("grid", {})
            if not isinstance(grid, dict) or grid.get("verified") is not True:
                errors.append(f"{site_id}/{variable}: actual variable grid position unverified")
            elif not (finite_number(grid.get("latitude")) and finite_number(grid.get("longitude"))
                      and -90 <= grid["latitude"] <= 90 and -180 <= grid["longitude"] <= 180
                      and finite_number(grid.get("distance_km")) and grid["distance_km"] >= 0
                      and grid.get("resolution") is not None):
                errors.append(f"{site_id}/{variable}: grid coordinates/distance/resolution invalid")
            elif point_valid:
                actual_distance = haversine_km(point["latitude"], point["longitude"],
                                               grid["latitude"], grid["longitude"])
                if abs(actual_distance - grid["distance_km"]) > 2:
                    errors.append(f"{site_id}/{variable}: stated target-grid offset inconsistent")
                if actual_distance > 25:
                    errors.append(f"{site_id}/{variable}: target-grid offset exceeds conservative 25 km gate")
            resolution = grid.get("resolution") if isinstance(grid, dict) else None
            if (isinstance(resolution, bool) or not isinstance(resolution, (str, int, float))
                    or isinstance(resolution, str) and not resolution.strip()
                    or finite_number(resolution) and (resolution < 0 or resolution == 0 and variable != "tide_height")):
                errors.append(f"{site_id}/{variable}: model resolution must be documented and positive (station points may be zero)")
            times, values = item.get("timestamps", []), item.get("values", [])
            if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values) or not times:
                errors.append(f"{site_id}/{variable}: timestamps and values lengths invalid")
                continue
            by_time: dict[datetime, Any] = {}
            malformed = False
            for timestamp, value in zip(times, values):
                try:
                    parsed = parse_time(timestamp).astimezone(SHANGHAI)
                    if parsed.minute or parsed.second or parsed.microsecond or parsed in by_time:
                        raise ValueError("Duplicate or nonhourly timestamp")
                    by_time[parsed] = value
                except (ValueError, TypeError):
                    malformed = True
                    continue
                if value is not None and not finite_number(value):
                    malformed = True
                elif value is not None:
                    if variable in {"wind_speed", "wind_gust", "precipitation", "wave_height", "swell_height", "swell_period", "current_speed"} and value < 0:
                        malformed = True
                    if variable in {"wind_direction", "current_direction"} and not 0 <= value <= 360:
                        malformed = True
                    if variable == "precipitation_probability" and not 0 <= value <= 100:
                        malformed = True
                    if variable == "weather_code" and (int(value) != value or not 0 <= value <= 99):
                        malformed = True
            if malformed:
                errors.append(f"{site_id}/{variable}: malformed timestamps or physically invalid values")
            if list(by_time) != sorted(by_time):
                errors.append(f"{site_id}/{variable}: hourly timestamps must increase")
            if variable == "tide_height" and site_id in coverage:
                for segment in coverage[site_id]["segments"]:
                    for kind in ("low", "high"):
                        extremum = segment[kind]
                        instant = parse_time(extremum["time"]).astimezone(SHANGHAI)
                        before = instant.replace(minute=0, second=0, microsecond=0)
                        after = before + timedelta(hours=1)
                        brackets = [by_time.get(before), by_time.get(after)]
                        if all(finite_number(value) for value in brackets):
                            if ((kind == "low" and extremum["height"] > min(brackets) + 0.15)
                                    or (kind == "high" and extremum["height"] < max(brackets) - 0.15)):
                                errors.append(f"{site_id}/tide_height: {kind} extremum/curve contradiction at {instant.isoformat()} (0.15 m tolerance)")
            if start is not None and end is not None:
                expected = start
                missing: list[datetime] = []
                while expected <= end:
                    if expected not in by_time or by_time[expected] is None:
                        missing.append(expected)
                    expected += timedelta(hours=1)
                if missing:
                    errors.append(f"{site_id}/{variable}: missing {len(missing)} required hourly values through {end.isoformat()}")
                    if any(timestamp.date() > day for timestamp in missing):
                        cross_day = True
            if variable == "weather_code" and any(value in {95, 96, 99} for value in values if finite_number(value)):
                warnings.append(f"{site_id}: THUNDERSTORM MODEL SIGNAL; weather codes are not official warnings")
        for category in ("warning", "marine_forecast"):
            matches = [doc for doc in official if isinstance(doc, dict) and doc.get("site_id") == site_id
                       and doc.get("category") == category and doc.get("request_date") == day.isoformat()]
            if not matches:
                errors.append(f"{site_id}/{category}: target-date official body not obtained; warning status unconfirmed")
                continue
            for document in matches:
                if document.get("status") != "verified" or document.get("source_id") not in sources:
                    errors.append(f"{site_id}/{category}: official body not independently verified; warning status unconfirmed")
                    continue
                source = sources[document["source_id"]]
                if not (_record_matches_raw(document, raw_sources.get(document["source_id"]), "document")
                        or _record_matches_raw(document, raw_sources.get(document["source_id"]), "official_documents")):
                    errors.append(f"{site_id}/{category}: structured official record is not bound to frozen normalized evidence")
                if source.get("kind") not in {"official", "synthetic"}:
                    errors.append(f"{site_id}/{category}: forecast grid cannot substitute for official warning body")
                if source.get("kind") == "synthetic" and not source.get("metadata", {}).get("synthetic"):
                    errors.append(f"{site_id}/{category}: synthetic official evidence must be explicitly marked")
                if not document.get("title") or not document.get("summary"):
                    errors.append(f"{site_id}/{category}: official title or body summary missing")
                try:
                    published = parse_time(document.get("published_at", ""))
                    valid_from = parse_time(document.get("valid_from", ""))
                    valid_to = parse_time(document.get("valid_to", ""))
                    if valid_to <= valid_from or published > valid_to:
                        raise ValueError("Invalid official validity")
                    if start is not None and end is not None and (valid_from > start or valid_to < end):
                        errors.append(f"{site_id}/{category}: official validity does not cover all selected intervals")
                except (ValueError, TypeError):
                    errors.append(f"{site_id}/{category}: official publication or validity times unverified")
    state = "BLOCKED_CROSS_DAY_DATA" if errors and cross_day else "BLOCKED" if errors else "VALIDATED"
    return {"state": state, "errors": errors, "warnings": warnings,
            "coverage": coverage, "manifest_sha256": digest}


def validate_run(run_dir: Path | str) -> dict[str, Any]:
    """Return a blocked machine-readable result even for malicious manifest shapes."""
    try:
        return _validate_run(run_dir)
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError):
        try:
            digest = sha256_file(Path(run_dir) / "manifest.json")
        except (OSError, TypeError, ValueError):
            digest = None
        return {"state": "BLOCKED", "errors": ["Malformed evidence contract; validation failed closed"],
                "warnings": [], "coverage": {}, "manifest_sha256": digest}

