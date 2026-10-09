"""Automatic official NMDIS tide acquisition, with unresolved times quarantined.

The endpoint and form were verified in the official site's BLLData.js. The
TimeArea=-0800 convention has NOT been independently documented. Therefore this
adapter freezes genuine hourly/extreme values but deliberately does not assert
absolute +08:00 timestamps or inject them into the normalized report series.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta

from .common import decode_json, download

TIDE_ENDPOINT = "https://global-tide.nmdis.org.cn/Api/Service.ashx"
TIDE_DOCUMENTATION = "https://global-tide.nmdis.org.cn/script/BLLData.js"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_tide_day(obj: dict, station_id: str, request_day: str) -> dict:
    """Check station/date and preserve unqualified local time strings and cm.

    Nullable unused extrema slots are normal; a null hourly value remains null.
    Source chart labels cm. No curve is synthesized from extrema.
    """
    if obj.get("State") is not True or not isinstance(obj.get("Data"), dict):
        raise ValueError("provider_state_false_or_data_null")
    data = obj["Data"]
    site, report, row = data.get("Site"), data.get("Data"), data.get("SubData")
    if not all(isinstance(value, dict) for value in (site, report, row)):
        raise ValueError("missing_tide_sections")
    requested = date.fromisoformat(request_day)
    if site.get("Code") != station_id or report.get("SiteCode") != station_id:
        raise ValueError("wrong_tide_station")
    if (report.get("Year"), report.get("Month"), row.get("Day")) != (requested.year, requested.month, requested.day):
        raise ValueError("wrong_tide_date")
    hourly = []
    for hour in range(24):
        key = f"a{hour}"
        if key not in row:
            raise ValueError("missing_hourly_tide_field")
        value = row[key]
        if value is not None and not _number(value):
            raise ValueError("invalid_hourly_tide_value")
        hourly.append(value)
    extremes = []
    for index in range(6):
        moment, height = row.get(f"cs{index}"), row.get(f"cg{index}")
        if moment is None and height is None:
            continue
        if not isinstance(moment, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", moment) or not _number(height):
            raise ValueError("invalid_or_null_tide_extremum")
        extremes.append({"local_time": request_day + "T" + moment,
                         "height_cm": height, "slot": index,
                         "type": "unclassified"})
    if not extremes:
        raise ValueError("empty_tide_extrema")
    times = [extreme["local_time"] for extreme in extremes]
    if len(set(times)) != len(times):
        raise ValueError("duplicate_tide_extremum")
    extremes.sort(key=lambda value: value["local_time"])
    return {
        "station_id": station_id, "request_day": request_day,
        "site_name": site.get("Name"), "longitude_reported": site.get("CoordX"),
        "latitude_reported": site.get("CoordY"), "coordinate_crs": "not specified by source",
        "datum_description": report.get("Benchmark"), "unit": "cm",
        "raw_time_area": report.get("TimeArea"), "timezone_verified": False,
        "hourly_local_times": [f"{request_day}T{hour:02d}:00" for hour in range(24)],
        "hourly_values_cm": hourly, "extrema_local": extremes,
        "compiled_at_unqualified": report.get("RecordTime"),
        "classification": "No absolute timestamps inferred from TimeArea sign",
    }


def fetch_tides(sites: list[dict], request_date: str, *, timeout: float = 20) -> tuple[list, dict, list, list]:
    sources, raw, quarantined, gaps = [], {}, [], []
    start = date.fromisoformat(request_date)
    for station_id in sorted({site["reference_station"]["id"] for site in sites}):
        station_days = []
        missing_days = []
        for day_offset in range(3):
            request_day = (start + timedelta(days=day_offset)).isoformat()
            source_id = f"tide_{station_id}_{request_day}"
            payload = {"Server": "User", "Command": "GetData",
                       "Data": {"code": station_id, "date": request_day}}
            response = download(TIDE_ENDPOINT, {"ApiRequest": json.dumps(payload, separators=(",", ":"))},
                                method="POST", timeout=timeout)
            source = response.source(source_id, kind="forecast", license_text=(
                "Official NMDIS Global Tide public service. Redistribution license not established; "
                "local evidence only, no downloaded real bodies in public repository."
            ))
            source["metadata"].update({
                "provider": "国家海洋信息中心全球潮汐预报服务平台",
                "documentation": TIDE_DOCUMENTATION,
                "units": "cm, verified from official BLLChart.js chart label",
                "unit_conventions": "raw hourly and minute extrema in cm; not depth",
                "time_convention": "TimeArea raw sign unresolved; local time strings quarantined",
                "timezone_verified": False, "request_day": request_day,
                "station_id": station_id, "forecast_type": "official tide prediction, not observed water level",
            })
            try:
                parsed = parse_tide_day(decode_json(response), station_id, request_day)
                parsed["source_id"] = source_id
                source["metadata"].update({"raw_time_area": parsed["raw_time_area"],
                                            "datum": parsed["datum_description"],
                                            "returned_station": {k: parsed[k] for k in
                                                ("site_name", "latitude_reported", "longitude_reported", "coordinate_crs")},
                                            "compiled_at_unqualified": parsed["compiled_at_unqualified"]})
                station_days.append(parsed)
                for site in sites:
                    station = site["reference_station"]
                    if station["id"] == station_id:
                        station.update(name=parsed["site_name"], latitude=parsed["latitude_reported"],
                                       longitude=parsed["longitude_reported"],
                                       datum=parsed["datum_description"] or "unverified",
                                       coordinate_crs="not specified by source",
                                       provenance=f"Official NMDIS response; source_id={source_id}; bay relation unverified")
                if any(value is None for value in parsed["hourly_values_cm"]):
                    gaps.append({"code": "TIDE_HOURLY_NULL", "site_id": None,
                                 "message": f"{station_id} {request_day} contains missing hourly tide values",
                                 "blocking": True})
            except (ValueError, KeyError, TypeError):
                missing_days.append((day_offset, request_day))
            sources.append(source)
            raw[source_id] = response.raw
        # Classification is derived from neighbouring *official extrema heights*,
        # never from slot number or four-point interpolation of the hourly curve.
        points = sorted([point for day in station_days for point in day["extrema_local"]],
                        key=lambda point: point["local_time"])
        for index in range(1, len(points) - 1):
            previous, current, following = points[index - 1:index + 2]
            height = current["height_cm"]
            if height < previous["height_cm"] and height < following["height_cm"]:
                current["type"] = "low"
            elif height > previous["height_cm"] and height > following["height_cm"]:
                current["type"] = "high"
            current["classification_method"] = "Derived comparison of neighbouring official extreme heights; not explicit API type"
        # For edge extrema, verify type against bracketing actual hourly values
        # when available. These naive local strings are only used to determine
        # which *local date* is needed, never to assert an absolute UTC offset.
        hourly_values = {moment: height for record in station_days
                         for moment, height in zip(record["hourly_local_times"], record["hourly_values_cm"], strict=True)}
        for point in points:
            if point["type"] != "unclassified":
                continue
            moment = datetime.fromisoformat(point["local_time"])
            hour = moment.replace(minute=0, second=0)
            before = hour if moment.minute else hour - timedelta(hours=1)
            after = hour + timedelta(hours=1)
            first = hourly_values.get(before.strftime("%Y-%m-%dT%H:%M"))
            second = hourly_values.get(after.strftime("%Y-%m-%dT%H:%M"))
            height = point["height_cm"]
            if _number(first) and _number(second):
                if height <= first and height <= second and (height < first or height < second):
                    point["type"] = "low"
                elif height >= first and height >= second and (height > first or height > second):
                    point["type"] = "high"
                point["classification_method"] = "Derived comparison to bracketing original hourly values; no curve interpolation"
        selected = []
        for index, point in enumerate(points[:-1]):
            following = points[index + 1]
            if (point["type"] == "low" and following["type"] == "high"
                    and point["local_time"][:10] >= request_date):
                selected.append((point, following))
                if len(selected) == 3:
                    break
        required_end_day = None
        if len(selected) == 3:
            final_high = datetime.fromisoformat(selected[-1][1]["local_time"])
            # A final high at 23:xx requires the next date's 00:00 hourly value.
            required_end_day = (final_high.replace(minute=0, second=0) + timedelta(hours=1)).date().isoformat()
        # The source is still quarantined, even if a local-date probe determines
        # that the extra third date is not needed by the first three segments.
        for record in station_days:
            record["provisional_required_local_end_day"] = required_end_day
            record["provisional_coverage_absolute_time_verified"] = False
        for source in sources:
            metadata = source["metadata"]
            if metadata.get("station_id") == station_id:
                metadata["provisional_required_local_end_day"] = required_end_day
                metadata["provisional_coverage_absolute_time_verified"] = False
                metadata["required_for_coverage"] = required_end_day is None or metadata["request_day"] <= required_end_day
        for day_offset, missing_day in missing_days:
            supplemental = required_end_day is not None and missing_day > required_end_day
            code = ("TIDE_SUPPLEMENTAL_DAY_UNAVAILABLE" if supplemental else
                    "BLOCKED_CROSS_DAY_DATA" if day_offset else "TIDE_DATA_UNAVAILABLE")
            gaps.append({"code": code, "site_id": None,
                         "message": f"Official {station_id} {missing_day} response unavailable; "
                                    + ("supplemental date after provisional third local high" if supplemental else "required local-date coverage not established"),
                         "blocking": not supplemental})
        quarantined.extend(station_days)
    for site in sites:
        gaps.extend([
            {"code": "TIDE_TIMEZONE_UNVERIFIED", "site_id": site["id"], "blocking": True,
             "message": "Official TimeArea sign definition independently unverified; absolute tide times quarantined"},
            {"code": "STATION_BAY_RELATION_UNVERIFIED", "site_id": site["id"], "blocking": True,
             "message": "Candidate reference-station applicability to this bay and source coordinate CRS are unverified"},
        ])
    return sources, raw, quarantined, gaps
