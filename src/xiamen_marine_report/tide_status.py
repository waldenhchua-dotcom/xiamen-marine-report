"""Separate raw tide receipt from verified absolute-time report coverage."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import SHANGHAI, finite_number, parse_date, parse_time
from .provenance import read_json, safe_relative_path, sha256_file

_HASH = re.compile(r"^[a-f0-9]{64}$")
_SOURCE_KINDS = {"official", "forecast", "synthetic", "observed", "historical"}


def _read_bound_source(source: dict[str, Any], run_dir: Path | str) -> Any:
    try:
        path = safe_relative_path(run_dir, source["file"])
        if path.is_file() and sha256_file(path) == source["sha256"]:
            return read_json(path)
    except (KeyError, OSError, TypeError, ValueError):
        pass
    return None


def _verified_series_bound(manifest: dict[str, Any], site_id: str, station: dict[str, Any],
                           row: dict[str, Any], source: dict[str, Any], run_dir: Path | str | None) -> bool:
    from .validate import _series_matches_raw, _station_matches_raw

    if run_dir is None:
        return False
    station_source = _source(manifest, station.get("source_id"))
    if station_source is None or station_source.get("kind") not in {"official", "synthetic"}:
        return False
    try:
        return (_series_matches_raw(row, _read_bound_source(source, run_dir))
                and _station_matches_raw(station, site_id, _read_bound_source(station_source, run_dir)))
    except (KeyError, TypeError, ValueError):
        return False


def _source(manifest: dict[str, Any], source_id: Any) -> dict[str, Any] | None:
    if not isinstance(source_id, str) or not source_id:
        return None
    matches = [source for source in manifest.get("sources", [])
               if isinstance(source, dict) and source.get("id") == source_id]
    if len(matches) != 1:
        return None
    source = matches[0]
    if (type(source.get("http_status")) is not int or source["http_status"] != 200 or source.get("kind") not in _SOURCE_KINDS
            or not isinstance(source.get("sha256"), str) or not _HASH.fullmatch(source["sha256"])
            or not isinstance(source.get("file"), str) or not source["file"]):
        return None
    metadata = source.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("transport_error"):
        return None
    if metadata.get("response_state") is False or metadata.get("body_verified") is False:
        return None
    if source.get("kind") == "synthetic" and manifest.get("synthetic") is not True:
        return None
    return source


def _unavailable(site_id: str, day: str, station_id: str | None = None) -> dict[str, Any]:
    return {"site_id": site_id, "station_id": station_id, "date": day, "request_date": day,
            "status": "unavailable", "count": 0, "hourly_count": 0, "source_id": None,
            "source_sha256": None, "raw_time_area": None, "source_kind": None,
            "http_status": None, "body_status": "not_obtained_or_invalid",
            "timezone_verified": False, "native_timestamps": [], "heights_cm": [],
            "raw_source_bound": False,
            "timestamps": [], "values": [], "unit": None,
            "label": "原始逐时潮位未取得，或未通过日期、站点、来源及完整值校验"}


def bound_raw_tide_records(manifest: dict[str, Any], run_dir: Path | str) -> list[dict[str, Any]]:
    """Reparse hash-checked originals and reject altered quarantine summaries."""
    from .providers.tide import parse_tide_day

    result = []
    records = manifest.get("quarantined_tides", [])
    if not isinstance(records, list):
        return result
    for record in records:
        if not isinstance(record, dict):
            continue
        source = _source(manifest, record.get("source_id"))
        if source is None:
            continue
        metadata = source.get("metadata", {})
        station_id, day = record.get("station_id"), record.get("request_day")
        if metadata.get("station_id") != station_id or metadata.get("request_day") != day:
            continue
        try:
            path = safe_relative_path(run_dir, source["file"])
            if not path.is_file() or sha256_file(path) != source["sha256"]:
                continue
            parsed = parse_tide_day(read_json(path), station_id, day)
        except (KeyError, OSError, TypeError, ValueError):
            continue
        fields = ("station_id", "request_day", "hourly_local_times", "hourly_values_cm",
                  "raw_time_area", "unit", "timezone_verified")
        if any(record.get(field) != parsed.get(field) for field in fields):
            continue
        if (parsed.get("raw_time_area") != metadata.get("raw_time_area")
                or len(parsed["hourly_values_cm"]) != 24
                or not all(finite_number(value) for value in parsed["hourly_values_cm"])):
            continue
        parsed["source_id"] = source["id"]
        result.append(parsed)
    return result


def tide_receipt(manifest: dict[str, Any], site_id: str,
                 request_date: str | None = None, *, run_dir: Path | str | None = None) -> dict[str, Any]:
    """Count only a complete requested-day series; never infer TimeArea's sign.

    ``received_unverified`` proves a parsed native-time quarantined record exists.
    It does not claim UTC+08:00, three complete floods, or a validated report.
    The source file and SHA bind this receipt to the immutable evidence manifest.
    """
    day = request_date if request_date is not None else manifest.get("request_date", "")
    try:
        parse_date(day)
    except (TypeError, ValueError):
        return _unavailable(site_id, str(day))
    sites = [site for site in manifest.get("sites", [])
             if isinstance(site, dict) and site.get("id") == site_id]
    if len(sites) != 1 or not isinstance(sites[0].get("reference_station"), dict):
        return _unavailable(site_id, day)
    station = sites[0]["reference_station"]
    station_id = station.get("id")
    if not isinstance(station_id, str) or not station_id:
        return _unavailable(site_id, day)
    unavailable = _unavailable(site_id, day, station_id)
    attempts = [source for source in manifest.get("sources", []) if isinstance(source, dict)
                and isinstance(source.get("metadata"), dict)
                and source["metadata"].get("station_id") == station_id
                and source["metadata"].get("request_day") == day]
    if len(attempts) == 1:
        source = attempts[0]
        unavailable.update(source_id=source.get("id"), source_sha256=source.get("sha256"),
                           source_kind=source.get("kind"), http_status=source.get("http_status"),
                           raw_time_area=source["metadata"].get("raw_time_area"),
                           body_status="http_200_without_valid_tide_body" if source.get("http_status") == 200 else "http_error")
    expected_native = [f"{day}T{hour:02d}:00" for hour in range(24)]
    if station.get("timezone_verified") is True:
        rows = [row for row in manifest.get("series", [])
                if isinstance(row, dict) and row.get("site_id") == site_id and row.get("variable") == "tide_height"]
        if len(rows) == 1:
            row = rows[0]
            source = _source(manifest, row.get("source_id"))
            times, values = row.get("timestamps"), row.get("values")
            if (source is not None and row.get("unit") == "m" and row.get("station_id", station_id) == station_id
                    and _verified_series_bound(manifest, site_id, station, row, source, run_dir)
                    and isinstance(times, list) and isinstance(values, list) and len(times) == len(values)):
                try:
                    parsed = [parse_time(stamp).astimezone(SHANGHAI) for stamp in times]
                    daily = [(stamp, value) for stamp, value in zip(parsed, values, strict=True) if stamp.date().isoformat() == day]
                    if ([stamp.strftime("%Y-%m-%dT%H:%M") for stamp, _ in daily] == expected_native
                            and all(stamp.second == 0 and stamp.microsecond == 0 and finite_number(value) for stamp, value in daily)):
                        return {**unavailable, "status": "verified", "count": 24, "hourly_count": 24,
                                "source_id": source["id"], "source_sha256": source["sha256"],
                                "raw_time_area": source.get("metadata", {}).get("raw_time_area"),
                                "source_kind": source["kind"], "http_status": source["http_status"],
                                "body_status": "normalized_hourly_tide", "timezone_verified": True,
                                "raw_source_bound": True,
                                "timestamps": [stamp.isoformat() for stamp, _ in daily],
                                "values": [value for _, value in daily], "unit": "m",
                                "label": "逐时潮位已取得（24 个值），时区已经独立核实"}
                except (TypeError, ValueError):
                    pass
    quarantined = bound_raw_tide_records(manifest, run_dir) if run_dir is not None else manifest.get("quarantined_tides", [])
    records = [record for record in quarantined
               if isinstance(record, dict) and record.get("station_id") == station_id and record.get("request_day") == day]
    if len(records) != 1:
        return unavailable
    record = records[0]
    source = _source(manifest, record.get("source_id"))
    if source is None:
        return unavailable
    metadata = source.get("metadata", {})
    if metadata.get("station_id") != station_id or metadata.get("request_day") != day:
        return unavailable
    if record.get("unit") != "cm" or record.get("timezone_verified") is not False:
        return unavailable
    times, heights = record.get("hourly_local_times"), record.get("hourly_values_cm")
    if (times != expected_native or not isinstance(heights, list) or len(heights) != 24
            or not all(finite_number(height) for height in heights)):
        return unavailable
    if record.get("raw_time_area") != metadata.get("raw_time_area"):
        return unavailable
    # Native strings have no UTC offset; even a recognized-looking sign stays raw.
    if any(datetime.fromisoformat(stamp).tzinfo is not None for stamp in times):
        return unavailable
    return {**unavailable, "status": "received_unverified", "count": 24, "hourly_count": 24,
            "source_id": source["id"], "source_sha256": source["sha256"],
            "raw_time_area": record.get("raw_time_area"), "source_kind": source["kind"],
            "http_status": source["http_status"], "body_status": "parsed_native_hourly_tide",
            "raw_source_bound": run_dir is not None,
            "native_timestamps": list(times), "heights_cm": list(heights), "unit": "cm",
            "label": "原始逐时潮位已取得（24 个值）；时区定义未核实，暂不按北京时间绘制"}


def write_raw_tide_csv(manifest: dict[str, Any], destination: Path | str, *, run_dir: Path | str) -> Path | None:
    """Export only valid acquired native-time tables; shared stations appear once."""
    receipts = [tide_receipt(manifest, site["id"], run_dir=run_dir) for site in manifest.get("sites", []) if isinstance(site, dict)]
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for receipt in receipts:
        if receipt["status"] == "received_unverified":
            key = (receipt["station_id"], receipt["date"], receipt["source_id"])
            records[key] = receipt
    if not records:
        return None
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("station_id", "date", "native_local_time", "height_cm", "source_id", "TimeArea", "timezone_verified", "source_sha256")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key in sorted(records):
            receipt = records[key]
            for stamp, height in zip(receipt["native_timestamps"], receipt["heights_cm"], strict=True):
                writer.writerow({"station_id": receipt["station_id"], "date": receipt["date"],
                                 "native_local_time": stamp, "height_cm": height,
                                 "source_id": receipt["source_id"], "TimeArea": receipt["raw_time_area"],
                                 "timezone_verified": "false", "source_sha256": receipt["source_sha256"]})
    return path
