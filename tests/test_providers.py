"""Network boundaries and source semantics tested entirely offline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from xiamen_marine_report.config import ProviderSettings, selected_sites
from xiamen_marine_report.providers import common, marine, tide
from xiamen_marine_report.providers.common import (
    Download,
    decode_json,
    download,
    parse_open_meteo,
    redact_request,
)
from xiamen_marine_report.providers.evidence import load_evidence


def api_response(variable="wave_height", unit="m", latitude=24.5, longitude=118.0):
    return {"latitude": latitude, "longitude": longitude,
            "timezone": "Asia/Shanghai", "utc_offset_seconds": 28800,
            "hourly_units": {"time": "iso8601", variable: unit},
            "hourly": {"time": ["2026-10-10T00:00", "2026-10-10T01:00"],
                       variable: [None, 0]}}


def downloaded(obj):
    return Download(json.dumps(obj).encode(), 200, "2026-10-09T23:00:00+00:00",
                    {"url": "https://example.org/data", "params": {}}, 1)


def tide_response(request_day="2026-10-10"):
    day = datetime.fromisoformat(request_day)
    return {"State": True, "Data": {
        "Site": {"Code": "T122", "Name": "厦门", "CoordX": 118.07, "CoordY": 24.44833},
        "Data": {"SiteCode": "T122", "Year": day.year, "Month": day.month,
                 "TimeArea": "-0800", "Benchmark": "在平均海面下328cm"},
        "SubData": {"Day": day.day, **{f"a{i}": 100 + i for i in range(24)},
                    "cs0": "05:48", "cg0": 104, "cs1": "11:52", "cg1": 621,
                    "cs2": "18:03", "cg2": 114,
                    "cs3": None, "cg3": None, "cs4": None, "cg4": None,
                    "cs5": None, "cg5": None}}}


def mocked_http(monkeypatch, handler):
    original_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(common.httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs))
    monkeypatch.setattr(common.time, "sleep", lambda seconds: None)


def test_timeout_is_bounded_and_error_does_not_expose_request(monkeypatch):
    calls = []

    def failing(request):
        calls.append(request)
        raise httpx.ReadTimeout("private key must not appear", request=request)

    mocked_http(monkeypatch, failing)
    result = download("https://example.org/data", {"apikey": "private key"}, timeout=1)
    assert len(calls) == result.attempts == 2
    assert result.error == "network_timeout_or_unavailable"
    assert "private key" not in repr(result)
    assert result.request["params"]["apikey"] == "[REDACTED]"


def test_retryable_http_is_capped_and_recovery_clears_error(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"value": 1})

    mocked_http(monkeypatch, handler)
    result = download("https://example.org/data")
    assert result.attempts == 2 and result.error is None
    assert decode_json(result) == {"value": 1}


def test_credential_echo_is_not_saved(monkeypatch):
    mocked_http(monkeypatch, lambda request: httpx.Response(400, text="apikey=do-not-save-this"))
    result = download("https://example.org/data", {"apikey": "do-not-save-this"})
    assert result.raw == b"" and result.error == "credential_echo_body_withheld"


def test_redaction_removes_url_credentials_fragment_and_query():
    record = redact_request("https://user:pass@example.org/data?token=abc&date=2026-10-10#secret",
                            {"Authorization": "Bearer secret", "date": "2026-10-10"})
    assert "abc" not in json.dumps(record)
    assert "user" not in record["url"] and "#" not in record["url"]
    assert record["params"]["Authorization"] == "[REDACTED]"


@pytest.mark.parametrize("body", [{"State": False, "Data": None}, {"State": True, "Data": None}, {"error": True}])
def test_http_200_does_not_imply_data(body):
    with pytest.raises(ValueError):
        decode_json(downloaded(body))


def test_empty_response_is_missing(monkeypatch):
    mocked_http(monkeypatch, lambda request: httpx.Response(200, content=b""))
    result = download("https://example.org/data")
    assert result.error == "empty_response"
    with pytest.raises(ValueError):
        decode_json(result)


def test_wrong_date_timezone_and_units_rejected():
    obj = api_response()
    site = selected_sites(["haicang"])[0]
    mapping = {"wave_height": ("wave_height", "m", None)}
    with pytest.raises(ValueError, match="wrong_request_date"):
        parse_open_meteo(obj, site, mapping, "source", "2026-10-11", resolution_km=8)
    obj["utc_offset_seconds"] = -28800
    with pytest.raises(ValueError, match="timezone_mismatch"):
        parse_open_meteo(obj, site, mapping, "source", "2026-10-10", resolution_km=8)
    obj["utc_offset_seconds"] = 28800
    obj["hourly_units"]["wave_height"] = "ft"
    with pytest.raises(ValueError, match="unexpected_unit"):
        parse_open_meteo(obj, site, mapping, "source", "2026-10-10", resolution_km=8)


def test_null_is_not_zero_and_current_speed_uses_response_units():
    site = selected_sites(["haicang"])[0]
    obj = api_response("ocean_current_velocity", "km/h")
    obj["hourly"]["ocean_current_velocity"] = [None, 3.6]
    row = parse_open_meteo(obj, site, {"ocean_current_velocity": ("current_speed", "m/s", None)},
                           "source", "2026-10-10", resolution_km=8)[0]
    assert row["values"] == [None, 1.0]
    assert row["original_unit"] == "km/h" and row["unit"] == "m/s"
    assert row["timestamps"][0].endswith("+08:00")


def test_coordinate_offset_computed_not_copied_from_target():
    site = selected_sites(["wuyuan"])[0]
    row = parse_open_meteo(api_response(latitude=24.0, longitude=119.0), site,
                           {"wave_height": ("wave_height", "m", None)}, "source", "2026-10-10", resolution_km=8)[0]
    assert row["grid"]["latitude"] == 24.0
    assert row["grid"]["distance_km"] > 50


def test_each_marine_variable_retains_own_returned_grid_and_toward(monkeypatch):
    requests = []

    def fake_download(url, params, **kwargs):
        variable = params["hourly"]
        requests.append(variable)
        definition = marine.MARINE_VARIABLES[variable]
        units = {"degrees": "°"}
        obj = api_response(variable, units.get(definition[1], definition[1]), latitude=24.4 + len(requests) / 100)
        return downloaded(obj)

    monkeypatch.setattr(marine, "download", fake_download)
    sources, raw, rows, gaps = marine.fetch_marine(selected_sites(["haicang"])[0], "2026-10-10", "2026-10-12", ProviderSettings())
    assert len(requests) == len(sources) == len(raw) == len(rows) == 6
    assert len({row["grid"]["latitude"] for row in rows}) == 6
    direction = next(row for row in rows if row["variable"] == "current_direction")
    assert direction["direction_convention"] == "toward"
    assert any(gap["code"] == "COASTAL_MODEL_UNSUITABLE" and gap["blocking"] for gap in gaps)


def test_tide_unused_extreme_slots_are_valid_and_timearea_is_quarantined():
    record = tide.parse_tide_day(tide_response(), "T122", "2026-10-10")
    assert len(record["hourly_values_cm"]) == 24 and len(record["extrema_local"]) == 3
    assert record["raw_time_area"] == "-0800" and record["timezone_verified"] is False
    assert all("+08:00" not in point["local_time"] for point in record["extrema_local"])


def test_tide_null_and_wrong_station_date_fail_without_replacement():
    obj = tide_response()
    with pytest.raises(ValueError, match="wrong_tide_date"):
        tide.parse_tide_day(obj, "T122", "2026-10-11")
    with pytest.raises(ValueError, match="wrong_tide_station"):
        tide.parse_tide_day(obj, "NT1405", "2026-10-10")
    obj["Data"]["SubData"]["a1"] = None
    assert tide.parse_tide_day(obj, "T122", "2026-10-10")["hourly_values_cm"][1] is None
    obj["Data"]["SubData"]["cs1"] = "nu:ll"
    with pytest.raises(ValueError, match="invalid_or_null"):
        tide.parse_tide_day(obj, "T122", "2026-10-10")


def test_cross_day_false_response_blocks_and_retains_raw(monkeypatch):
    def fake_download(url, params, **kwargs):
        requested = json.loads(params["ApiRequest"])["Data"]["date"]
        return downloaded({"State": False, "Data": None} if requested.endswith("11") else tide_response(requested))

    monkeypatch.setattr(tide, "download", fake_download)
    sources, raw, quarantined, gaps = tide.fetch_tides(selected_sites(["haicang"]), "2026-10-10")
    assert len(sources) == len(raw) == 3 and len(quarantined) == 2
    assert any(gap["code"] == "BLOCKED_CROSS_DAY_DATA" for gap in gaps)


def test_unneeded_third_probe_date_does_not_falsely_block_cross_day(monkeypatch):
    def fake_download(url, params, **kwargs):
        requested = json.loads(params["ApiRequest"])["Data"]["date"]
        if requested.endswith("12"):
            return downloaded({"State": False, "Data": None})
        obj = tide_response(requested)
        if requested.endswith("11"):
            obj["Data"]["SubData"].update(cs0="00:10", cg0=640, cs1="06:20", cg1=110,
                                         cs2="12:25", cg2=630, cs3="18:35", cg3=120)
        return downloaded(obj)

    monkeypatch.setattr(tide, "download", fake_download)
    sources, _, quarantined, gaps = tide.fetch_tides(selected_sites(["haicang"]), "2026-10-10")
    assert len(quarantined) == 2
    assert all(record["provisional_required_local_end_day"] == "2026-10-11" for record in quarantined)
    assert not any(gap["code"] == "BLOCKED_CROSS_DAY_DATA" for gap in gaps)
    supplemental = next(gap for gap in gaps if gap["code"] == "TIDE_SUPPLEMENTAL_DAY_UNAVAILABLE")
    assert supplemental["blocking"] is False
    optional_source = next(source for source in sources if source["id"].endswith("12"))
    assert optional_source["metadata"]["required_for_coverage"] is False
    assert any(gap["code"] == "TIDE_TIMEZONE_UNVERIFIED" and gap["blocking"] for gap in gaps)


def test_commercial_mode_requires_environment_key_and_hides_it(monkeypatch):
    monkeypatch.delenv("MARINE_REPORT_OPEN_METEO_API_KEY", raising=False)
    monkeypatch.setenv("MARINE_REPORT_OPEN_METEO_MODE", "noncommercial")
    with pytest.raises(ValueError, match="requires"):
        ProviderSettings.from_environment(commercial=True)
    monkeypatch.setenv("MARINE_REPORT_OPEN_METEO_API_KEY", "sample-secret")
    settings = ProviderSettings.from_environment(commercial=True)
    assert "sample-secret" not in repr(settings)
    assert settings.endpoint("/v1/marine", marine=True) == "https://customer-marine-api.open-meteo.com/v1/marine"


def write_bundle(tmp_path: Path):
    raw = b'{"original": "official local evidence"}'
    (tmp_path / "original.json").write_bytes(raw)
    bundle = {"request_date": "2026-10-10", "synthetic": False,
              "import_review": {"reviewer": "independent reviewer", "checked_at": "2026-10-10T07:00:00+08:00",
                                "original_source_values_checked": True},
              "sources": [{"id": "import_official", "file": "original.json", "sha256": hashlib.sha256(raw).hexdigest(),
                           "request": {"url": "https://example.org/official", "params": {}}, "http_status": 200,
                           "retrieved_at": "2026-10-09T23:00:00+00:00", "kind": "official",
                           "license": "Review before redistribution"}],
              "series": [], "extrema": [], "official_documents": []}
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    return bundle


def test_import_requires_independent_review_raw_hash_and_containment(tmp_path):
    bundle = write_bundle(tmp_path)
    loaded, raw = load_evidence(tmp_path, "2026-10-10", ["haicang"])
    assert raw["import_official"] and loaded["sources"][0]["metadata"]["import_review"]["reviewer"]
    with pytest.raises(ValueError, match="date mismatch"):
        load_evidence(tmp_path, "2026-10-11", ["haicang"])
    bundle["sources"][0]["file"] = "../escape.json"
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsafe"):
        load_evidence(tmp_path, "2026-10-10", ["haicang"])
    bundle["sources"][0]["file"] = "original.json"
    bundle["sources"][0]["sha256"] = "0" * 64
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_evidence(tmp_path, "2026-10-10", ["haicang"])


def test_imported_normalized_values_require_frozen_extraction_and_original(tmp_path):
    bundle = write_bundle(tmp_path)
    record = {"site_id": "haicang", "variable": "tide_height", "source_id": "import_extraction",
              "timestamps": ["2026-10-10T00:00:00+08:00"], "values": [1.0], "unit": "m"}
    extracted = json.dumps({"series": [record]}).encode()
    (tmp_path / "extracted.json").write_bytes(extracted)
    extraction_source = dict(bundle["sources"][0], id="import_extraction", file="extracted.json",
                             sha256=hashlib.sha256(extracted).hexdigest(),
                             metadata={"original_source_id": "import_official"})
    bundle["sources"].append(extraction_source)
    bundle["series"] = [record]
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    loaded, _ = load_evidence(tmp_path, "2026-10-10", ["haicang"])
    assert loaded["series"][0]["values"] == [1.0]
    bundle["series"][0]["values"] = [2.0]
    (tmp_path / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="not bound"):
        load_evidence(tmp_path, "2026-10-10", ["haicang"])
