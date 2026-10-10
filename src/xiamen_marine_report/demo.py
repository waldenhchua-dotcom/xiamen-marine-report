"""Offline fixtures. No records here are observations or official forecasts."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .coverage import hourly_end
from .models import SHANGHAI, haversine_km, parse_date, parse_time
from .provenance import read_json, save_run, write_json
from .validate import validate_run


def generate_fixture(date: str = "2026-10-10", *, include_maps: bool = True) -> dict[str, Any]:
    """Return manifest plus raw byte objects for a clearly synthetic two-day run."""
    day = parse_date(date)
    midnight = datetime.combine(day, time(), SHANGHAI)
    retrieved = (midnight - timedelta(hours=1)).astimezone(timezone.utc).isoformat()
    valid_end = (midnight + timedelta(days=2)).isoformat()
    source_records: list[dict[str, Any]] = []
    raw_objects: dict[str, bytes] = {}

    def add_source(source_id: str, payload: dict[str, Any], *, category: str = "series") -> None:
        raw_objects[source_id] = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        source_records.append({
            "id": source_id, "retrieved_at": retrieved, "http_status": 200,
            "request": {"url": f"https://example.org/synthetic/{source_id}", "params": {"date": date}},
            "kind": "synthetic", "license": "Apache-2.0 synthetic test fixture; no real data",
            "metadata": {"synthetic": True, "category": category, "units": "explicit per variable",
                         "time_convention": "Synthetic ISO 8601 UTC+08:00; no TimeArea inference",
                         "timezone": "Asia/Shanghai", "valid_from": midnight.isoformat(),
                         "valid_to": valid_end, "model": "synthetic deterministic fixture",
                         "model_run_verified": False,
                         "limitations": "Entirely invented for software testing; not a marine forecast"},
        })

    points = {"haicang": ("海沧湾", 24.473, 118.041),
              "wuyuan": ("五缘湾", 24.526, 118.169),
              "huandong": ("环东", 24.622, 118.192)}
    stations = {
        "SYNTHETIC-T122": {"id": "SYNTHETIC-T122", "name": "合成厦门参考站（非真实 T122 数据）",
                           "source_id": "synthetic-tides-synthetic-t122",
                           "latitude": 24.48, "longitude": 118.05,
                           "datum": "SYNTHETIC datum (not an official vertical datum)",
                           "timezone_verified": True, "synthetic": True,
                           "provenance": "Invented fixture point, not independently verified station coordinates",
                           "limitations": "Synthetic reference station; tide height is not bay depth"},
        "SYNTHETIC-NT1405": {"id": "SYNTHETIC-NT1405", "name": "合成翔安参考站（非真实 NT1405 数据）",
                             "source_id": "synthetic-tides-synthetic-nt1405",
                             "latitude": 24.57, "longitude": 118.21,
                             "datum": "SYNTHETIC datum (not an official vertical datum)",
                             "timezone_verified": True, "synthetic": True,
                             "provenance": "Invented fixture point, not independently verified station coordinates",
                             "limitations": "Synthetic shared reference station; not a tide-current station"},
    }
    extrema: list[dict[str, Any]] = []
    station_extrema: dict[str, list[dict[str, Any]]] = {}
    for station_id, minutes in (("SYNTHETIC-T122", 0), ("SYNTHETIC-NT1405", 80)):
        source_id = "synthetic-tides-" + station_id.lower()
        times = [("high", 40), ("low", 160), ("high", 440),
                 ("low", 900), ("high", 1180), ("low", 1620), ("high", 1880)]
        entries = [{"station_id": station_id,
                    "time": (midnight + timedelta(minutes=offset + minutes)).isoformat(),
                    "height": 0.75 if kind == "low" else 3.15,
                    "type": kind, "unit": "m", "datum": stations[station_id]["datum"],
                    "source_id": source_id} for kind, offset in times]
        station_extrema[station_id] = entries
        extrema.extend(entries)
        add_source(source_id, {"synthetic": True, "extrema": entries, "station": stations[station_id]}, category="tides")
    sites: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    official: list[dict[str, Any]] = []
    for site_id, (name, latitude, longitude) in points.items():
        station_id = "SYNTHETIC-T122" if site_id == "haicang" else "SYNTHETIC-NT1405"
        station = dict(stations[station_id])
        point = {"latitude": latitude, "longitude": longitude, "crs": "WGS84",
                 "precision": "approximate planning point",
                 "provenance": "Synthetic fixture planning point, intentionally approximate",
                 "limitations": "Not a verified launch location; synthetic WGS84 coordinates"}
        sites.append({"id": site_id, "name": name, "target_point": point,
                      "reference_station": station, "synthetic": True})
        last_high = station_extrema[station_id][-1]["time"]
        end = parse_time(hourly_end(last_high))
        timestamps: list[str] = []
        tick = midnight
        while tick <= end:
            timestamps.append(tick.isoformat())
            tick += timedelta(hours=1)
        source_id = f"synthetic-series-{site_id}"
        grid = {"latitude": latitude + 0.015, "longitude": longitude + 0.015,
                "distance_km": round(haversine_km(latitude, longitude, latitude + 0.015, longitude + 0.015), 3),
                "resolution": 9, "verified": True, "synthetic": True,
                "provenance": "Invented per-variable grid for testing; not a real model grid"}
        tide_grid = {"latitude": station["latitude"], "longitude": station["longitude"],
                     "distance_km": round(haversine_km(latitude, longitude, station["latitude"], station["longitude"]), 3),
                     "resolution": 0, "verified": True, "synthetic": True,
                     "provenance": "Synthetic reference station, not a marine model grid"}
        nodes = [(parse_time(entry["time"]), entry["height"]) for entry in station_extrema[station_id]]
        # Test data only: one internally consistent synthetic curve, sampled hourly.
        # Real adapters must preserve original hourly values, never interpolate extrema.
        nodes.insert(0, (nodes[0][0] - timedelta(hours=6), 0.75))
        nodes.append((nodes[-1][0] + timedelta(hours=6), 0.75))
        tide_values = []
        for timestamp in timestamps:
            instant = parse_time(timestamp)
            for (left_time, left_height), (right_time, right_height) in zip(nodes, nodes[1:]):
                if left_time <= instant <= right_time:
                    fraction = (instant - left_time).total_seconds() / (right_time - left_time).total_seconds()
                    interpolated = left_height + (right_height - left_height) * (1 - math.cos(math.pi * fraction)) / 2
                    tide_values.append(round(interpolated, 3))
                    break
            else:
                raise ValueError("Synthetic tide generator lacks supporting extrema")
        values = {
            "tide_height": ("m", tide_values),
            "wind_speed": ("m/s", [round(2.4 + 0.6 * math.sin(index / 3), 2) for index in range(len(timestamps))]),
            "wind_gust": ("m/s", [round(4.2 + 0.8 * math.sin(index / 3), 2) for index in range(len(timestamps))]),
            "wind_direction": ("degrees", [270.0] * len(timestamps)),
            "precipitation": ("mm", [1.2 if 14 <= index <= 16 else 0.0 for index in range(len(timestamps))]),
            "precipitation_probability": ("%", [75 if 14 <= index <= 16 else 10 for index in range(len(timestamps))]),
            "weather_code": ("WMO code", [95 if index == 15 else 3 for index in range(len(timestamps))]),
            "temperature": ("°C", [round(25 + 2 * math.sin(index / 5), 1) for index in range(len(timestamps))]),
            "wave_height": ("m", [0.45] * len(timestamps)),
            "swell_height": ("m", [0.25] * len(timestamps)),
            "swell_period": ("s", [4.8] * len(timestamps)),
            "current_speed": ("m/s", [0.0 if index == 5 else 0.25 for index in range(len(timestamps))]),
            "current_direction": ("degrees", [90.0] * len(timestamps)),
            "sea_temperature": ("°C", [25.2] * len(timestamps)),
        }
        site_series: list[dict[str, Any]] = []
        for variable, (unit, observations) in values.items():
            entry = {"site_id": site_id, "variable": variable, "timestamps": list(timestamps),
                     "values": observations, "unit": unit, "source_id": source_id,
                     "kind": "synthetic", "grid": dict(tide_grid if variable == "tide_height" else grid),
                     "model_run": "synthetic", "local_applicability": "synthetic test only"}
            if variable == "wind_direction":
                entry["direction_convention"] = "from"
            elif variable == "current_direction":
                entry["direction_convention"] = "toward"
            if variable == "tide_height":
                entry["datum"] = station["datum"]
            site_series.append(entry)
        series.extend(site_series)
        add_source(source_id, {"synthetic": True, "site_id": site_id, "series": site_series})
        for category in ("warning", "marine_forecast"):
            official_id = f"synthetic-official-{site_id}-{category}"
            doc = {"site_id": site_id, "category": category, "status": "verified",
                   "source_id": official_id, "request_date": date,
                   "title": f"SYNTHETIC TEST {name} {category}; not an official publication",
                   "published_at": midnight.isoformat(), "valid_from": midnight.isoformat(),
                   "valid_to": valid_end,
                   "summary": "合成测试正文：模拟雷暴提示。不是官方预警，也不表示现实无风险。"}
            official.append(doc)
            add_source(official_id, {"synthetic": True, "document": doc,
                                     "body": "Synthetic body exercises exact-date warning audit, not a real warning."}, category="official_test_body")
    manifest = {"schema_version": 1, "run_id": "pending-save", "request_date": date,
                "timezone": "Asia/Shanghai", "state": "FETCHED", "synthetic": True,
                "analysis_provider": "none", "created_at": retrieved,
                "sites": sites, "sources": source_records, "extrema": extrema,
                "series": series, "official_documents": official, "gaps": []}
    if include_maps:
        from .providers.maps import load_basemap_sources
        map_sources, map_raw_objects = load_basemap_sources()
        manifest["sources"].extend(map_sources)
        raw_objects.update(map_raw_objects)
    return {"manifest": manifest, "raw_objects": raw_objects}


def write_fixture(path: Path | str, date: str = "2026-10-10") -> None:
    fixture = generate_fixture(date)
    map_source_ids = {source["id"] for source in fixture["manifest"]["sources"] if source.get("kind") == "map"}
    # Pin bundled static map bytes without duplicating a large original in the fixture.
    fixture["raw_objects"] = {
        key: {"bundled_map_source_id": key, "sha256": hashlib.sha256(value).hexdigest()}
        if key in map_source_ids else json.loads(value)
        for key, value in fixture["raw_objects"].items()
    }
    write_json(path, fixture)


def load_fixture(path: Path | str) -> dict[str, Any]:
    fixture = read_json(path)
    if not isinstance(fixture, dict) or not isinstance(fixture.get("raw_objects"), dict):
        raise ValueError("Fixture needs manifest and raw_objects")
    if fixture.get("manifest", {}).get("synthetic") is not True:
        raise ValueError("Offline demo accepts explicitly synthetic fixtures only")
    map_source_ids = {source["id"] for source in fixture["manifest"].get("sources", []) if source.get("kind") == "map"}
    bundled: dict[str, bytes] = {}
    if map_source_ids:
        from .providers.maps import load_basemap_sources
        _sources, bundled = load_basemap_sources()
    raw_objects: dict[str, bytes] = {}
    for key, value in fixture["raw_objects"].items():
        if key in map_source_ids:
            if (not isinstance(value, dict) or set(value) != {"bundled_map_source_id", "sha256"}
                    or value["bundled_map_source_id"] != key or key not in bundled
                    or hashlib.sha256(bundled[key]).hexdigest() != value["sha256"]):
                raise ValueError("Fixture static map reference is not a verified bundled source ID/hash")
            raw_objects[key] = bundled[key]
        else:
            raw_objects[key] = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if not map_source_ids.issubset(raw_objects):
        raise ValueError("Fixture is missing pinned static map evidence")
    fixture["raw_objects"] = raw_objects
    return fixture


def demo_run(output_root: Path | str, fixture_path: Path | str | None = None,
             date: str = "2026-10-10", *, allow_partial: bool = False) -> dict[str, Any]:
    fixture = load_fixture(fixture_path) if fixture_path else generate_fixture(date)
    output = Path(output_root)
    run_dir = save_run(output / "runs", fixture["manifest"], fixture["raw_objects"])
    result = validate_run(run_dir)
    write_json(run_dir / "validation.json", result)
    if result["state"] != "VALIDATED" and not allow_partial:
        return {"state": result["state"], "synthetic": True, "run": str(run_dir), "validation": result}
    from .render import render_run
    candidate = render_run(run_dir, output / "reports", allow_partial=allow_partial)
    return {"state": "AWAITING_REVIEW" if result["state"] == "VALIDATED" else "BLOCKED",
            "synthetic": True, "delivery_label": "UNAPPROVED_REFERENCE",
            "run": str(run_dir), "candidate": str(candidate), "validation_state": result["state"]}

