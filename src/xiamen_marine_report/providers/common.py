"""Bounded transport, redacted request records, and strict response parsing."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

SENSITIVE_KEYS = {"apikey", "api_key", "key", "token", "access_token", "authorization",
                  "cookie", "password", "secret", "signature"}
SENSITIVE_PATTERN = re.compile(r"token|secret|password|passwd|api.?key|credential|cookie|authorization|signature", re.I)
MAX_BYTES = 8 * 1024 * 1024


def redact_request(url: str, params: dict | None = None) -> dict:
    parts = urlsplit(url)
    # Remove URL user-info, fragment, and secret values even on failure paths.
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    query = [(k, "[REDACTED]" if _sensitive_key(k) else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    clean_url = urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))
    def clean(value, key=""):
        if _sensitive_key(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(k): clean(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    clean_params = clean(params or {})
    return {"url": clean_url, "params": clean_params}


def _sensitive_key(key: str) -> bool:
    return key.lower() in SENSITIVE_KEYS or bool(SENSITIVE_PATTERN.search(key))


def is_public_evidence_url(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        return False
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
        return False
    try:
        if not ipaddress.ip_address(host).is_global:
            return False
    except ValueError:
        pass
    return not any(_sensitive_key(k) for k, _ in parse_qsl(parts.query))


@dataclass
class Download:
    raw: bytes
    http_status: int | None
    retrieved_at: str
    request: dict
    attempts: int
    error: str | None = None

    def source(self, source_id: str, *, kind: str, license_text: str) -> dict:
        return {
            "id": source_id, "file": "", "sha256": "",
            "retrieved_at": self.retrieved_at, "request": self.request,
            "http_status": self.http_status, "kind": kind,
            "license": license_text,
            "metadata": {"attempts": self.attempts, "transport_error": self.error},
        }


def download(url: str, params: dict | None = None, *, timeout: float = 20,
             attempts: int = 2, method: str = "GET") -> Download:
    """At most two requests, one short backoff, no response-error strings in logs.

    The provider origin is explicit; this function never substitutes a mirror.
    A body echoing credentials is withheld, rather than leaking the credential
    into an evidence package or pretending redacted bytes are the original.
    """
    if timeout <= 0 or timeout > 120 or attempts not in {1, 2} or method not in {"GET", "POST"}:
        raise ValueError("Timeout must be in (0,120] and attempts must be 1 or 2")
    params = params or {}
    safe = redact_request(url, params)
    safe["method"] = method
    secrets = [str(v).encode() for k, v in params.items() if _sensitive_key(str(k)) and v]
    secrets.extend(v.encode() for k, v in parse_qsl(urlsplit(url).query) if _sensitive_key(k) and v)
    raw = b""
    status = None
    error = None
    for count in range(1, attempts + 1):
        try:
            headers = {"User-Agent": "XiamenMarineReport/0.1 (+public-evidence)"}
            kwargs = {"params": params} if method == "GET" else {"data": params}
            # Use the documented origin directly. An inherited proxy setting
            # must not silently reroute domestic official evidence requests.
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, headers=headers) as client:
                with client.stream(method, url, **kwargs) as response:
                    status = response.status_code
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BYTES:
                            break
                    raw = bytes(chunks)
            if len(raw) > MAX_BYTES:
                raw, error = b"", "response_too_large"
            elif not raw:
                error = "empty_response"
            elif status != 200:
                error = f"http_{status}"
                if status in {429, 500, 502, 503, 504} and count < attempts:
                    time.sleep(0.25)
                    continue
            else:
                error = None
            break
        except (httpx.RequestError, TimeoutError, OSError):
            error = "network_timeout_or_unavailable"
            if count == attempts:
                break
        time.sleep(0.25)
    if any(secret in raw for secret in secrets):
        raw, error = b"", "credential_echo_body_withheld"
    return Download(raw, status, datetime.now(timezone.utc).isoformat(), safe, count, error)


def decode_json(downloaded: Download) -> dict:
    if downloaded.error or downloaded.http_status != 200 or not downloaded.raw:
        raise ValueError(downloaded.error or "missing_response")
    try:
        obj = json.loads(downloaded.raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(obj, dict) or obj.get("error") is True:
        raise ValueError("provider_error_or_invalid_object")
    if obj.get("State") is False or ("Data" in obj and obj["Data"] is None):
        raise ValueError("provider_state_false_or_data_null")
    return obj


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b - a, math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(h)))


def parse_open_meteo(obj: dict, site: dict, variable_map: dict,
                     source_id: str, request_date: str, *, resolution_km: float) -> list[dict]:
    """Require the requested date, units, returned grid, and explicit +08:00."""
    if obj.get("timezone") != "Asia/Shanghai" or obj.get("utc_offset_seconds") != 28800:
        raise ValueError("timezone_mismatch")
    try:
        lat, lon = float(obj["latitude"]), float(obj["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("missing_returned_grid") from exc
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("invalid_returned_grid")
    hourly, units = obj.get("hourly"), obj.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("missing_hourly_data")
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise ValueError("empty_hourly_time")
    times = []
    for value in raw_times:
        if not isinstance(value, str):
            raise ValueError("malformed_timestamp")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        if parsed.utcoffset() != timedelta(hours=8):
            raise ValueError("timestamp_offset_mismatch")
        times.append(parsed.isoformat())
    if times != sorted(times) or len(set(times)) != len(times):
        raise ValueError("unordered_or_duplicate_timestamps")
    if not any(value.startswith(request_date + "T") for value in times):
        raise ValueError("wrong_request_date")
    point = site["target_point"]
    grid = {"latitude": lat, "longitude": lon,
            "distance_km": round(distance_km(point["latitude"], point["longitude"], lat, lon), 4),
            "resolution": resolution_km, "resolution_unit": "km",
            "verified": True, "provenance": "This variable's actual API response coordinates"}
    series = []
    for api_var, (variable, expected, direction) in variable_map.items():
        values = hourly.get(api_var)
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError(f"missing_or_misaligned_{api_var}")
        raw_unit = units.get(api_var)
        aliases = {"°": "degrees", "wmo code": "WMO code", "°C": "°C"}
        unit = aliases.get(raw_unit, raw_unit)
        factor = 1.0
        if expected == "m/s" and unit == "km/h":
            unit, factor = "m/s", 1 / 3.6
        if unit != expected:
            raise ValueError(f"unexpected_unit_{api_var}")
        normalized = []
        for value in values:
            if value is None:
                normalized.append(None)
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"invalid_value_{api_var}")
            else:
                normalized.append(value * factor if factor != 1 else value)
        row = {"site_id": site["id"], "variable": variable,
               "timestamps": times[:], "values": normalized, "unit": unit,
               "source_id": source_id, "kind": "forecast", "grid": grid.copy(),
               "original_unit": raw_unit, "unit_conversion_factor": factor}
        if direction:
            row["direction_convention"] = direction
        series.append(row)
    return series
