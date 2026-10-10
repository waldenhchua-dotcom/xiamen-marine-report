"""Receipt is distinct from absolute time verification and HTTP success."""
from __future__ import annotations

import csv
from copy import deepcopy
from datetime import datetime

import pytest

from xiamen_marine_report.demo import generate_fixture
from xiamen_marine_report.provenance import canonical_digest, load_manifest, save_run, sha256_file, write_json
from xiamen_marine_report.tide_status import bound_raw_tide_records, tide_receipt, write_raw_tide_csv


@pytest.fixture
def native_manifest() -> dict:
    day = "2026-10-11"
    sites = [{"id": site_id, "reference_station": {"id": station_id, "timezone_verified": False}}
             for site_id, station_id in (("haicang", "T122"), ("wuyuan", "NT1405"), ("huandong", "NT1405"))]
    sources, quarantined = [], []
    for station_id in ("T122", "NT1405"):
        source_id = f"tide_{station_id}_{day}"
        sources.append({"id": source_id, "http_status": 200, "kind": "forecast",
                        "sha256": "a" * 64, "file": f"evidence/{station_id}.json",
                        "metadata": {"station_id": station_id, "request_day": day, "raw_time_area": "-0800",
                                     "transport_error": None}})
        quarantined.append({"station_id": station_id, "request_day": day, "source_id": source_id,
                            "unit": "cm", "timezone_verified": False, "raw_time_area": "-0800",
                            "hourly_local_times": [f"{day}T{hour:02d}:00" for hour in range(24)],
                            "hourly_values_cm": [100.0 + hour for hour in range(24)]})
    return {"request_date": day, "synthetic": False, "sites": sites, "sources": sources,
            "series": [], "quarantined_tides": quarantined}


@pytest.fixture
def frozen_native(native_manifest, tmp_path):
    for source, record in zip(native_manifest["sources"], native_manifest["quarantined_tides"], strict=True):
        row = {f"a{hour}": value for hour, value in enumerate(record["hourly_values_cm"])}
        row.update(Day=11, cs0="02:00", cg0=90, cs1="08:00", cg1=280,
                   cs2="14:00", cg2=80, cs3="20:00", cg3=290)
        payload = {"State": True, "Data": {
            "Site": {"Code": record["station_id"], "Name": "mock official fixture", "CoordX": 118.1, "CoordY": 24.5},
            "Data": {"SiteCode": record["station_id"], "Year": 2026, "Month": 10, "TimeArea": "-0800", "Benchmark": "mock datum"},
            "SubData": row,
        }}
        path = tmp_path / source["file"]
        write_json(path, payload)
        source["sha256"] = sha256_file(path)
    return native_manifest, tmp_path


def test_received_raw_does_not_become_verified_or_missing(native_manifest):
    before = canonical_digest(native_manifest)
    receipt = tide_receipt(native_manifest, "haicang")
    assert receipt["status"] == "received_unverified"
    assert receipt["hourly_count"] == 24 and receipt["count"] == 24
    assert receipt["raw_time_area"] == "-0800" and receipt["timezone_verified"] is False
    assert receipt["source_id"] == "tide_T122_2026-10-11"
    assert receipt["body_status"] == "parsed_native_hourly_tide"
    assert "已取得" in receipt["label"] and "暂不按北京时间绘制" in receipt["label"]
    assert receipt["timestamps"] == [] and receipt["values"] == []
    assert all(datetime.fromisoformat(value).tzinfo is None for value in receipt["native_timestamps"])
    assert canonical_digest(native_manifest) == before


@pytest.mark.parametrize("time_area", ["-0800", "+0800", "0800", "UTC+8"])
def test_native_time_sign_never_promotes_to_verified(native_manifest, time_area):
    native_manifest["quarantined_tides"][0]["raw_time_area"] = time_area
    native_manifest["sources"][0]["metadata"]["raw_time_area"] = time_area
    assert tide_receipt(native_manifest, "haicang")["status"] == "received_unverified"


@pytest.mark.parametrize("damage", ["null", "nonfinite", "boolean", "short_values", "short_times", "duplicate_hour",
                                    "utc_offset", "wrong_date", "wrong_station", "missing_source", "bad_hash",
                                    "source_wrong_date", "source_wrong_station", "http_error", "state_false",
                                    "units", "duplicate_record"])
def test_invalid_or_mismatched_native_record_cannot_claim_received(native_manifest, damage):
    row = native_manifest["quarantined_tides"][0]
    source = native_manifest["sources"][0]
    if damage == "null":
        row["hourly_values_cm"][5] = None
    elif damage == "nonfinite":
        row["hourly_values_cm"][5] = float("nan")
    elif damage == "boolean":
        row["hourly_values_cm"][5] = True
    elif damage == "short_values":
        row["hourly_values_cm"].pop()
    elif damage == "short_times":
        row["hourly_local_times"].pop()
    elif damage == "duplicate_hour":
        row["hourly_local_times"][5] = row["hourly_local_times"][4]
    elif damage == "utc_offset":
        row["hourly_local_times"][5] += "+08:00"
    elif damage == "wrong_date":
        row["request_day"] = "2026-10-12"
    elif damage == "wrong_station":
        row["station_id"] = "OTHER"
    elif damage == "missing_source":
        row["source_id"] = "missing"
    elif damage == "bad_hash":
        source["sha256"] = "invalid"
    elif damage == "source_wrong_date":
        source["metadata"]["request_day"] = "2026-10-12"
    elif damage == "source_wrong_station":
        source["metadata"]["station_id"] = "OTHER"
    elif damage == "http_error":
        source["http_status"] = 503
    elif damage == "state_false":
        source["metadata"]["response_state"] = False
    elif damage == "units":
        row["unit"] = "m"
    else:
        native_manifest["quarantined_tides"].append(deepcopy(row))
    result = tide_receipt(native_manifest, "haicang")
    assert result["status"] == "unavailable" and result["hourly_count"] == 0


def test_http200_without_body_stays_unavailable(native_manifest):
    native_manifest["quarantined_tides"] = []
    result = tide_receipt(native_manifest, "haicang")
    assert result["status"] == "unavailable"
    assert result["http_status"] == 200 and result["source_kind"] == "forecast"
    assert result["body_status"] == "http_200_without_valid_tide_body"


def test_date_and_site_selection_are_exact(native_manifest):
    assert tide_receipt(native_manifest, "haicang", "2026-10-12")["status"] == "unavailable"
    assert tide_receipt(native_manifest, "unknown")["status"] == "unavailable"
    assert tide_receipt(native_manifest, "haicang", "bad-date")["status"] == "unavailable"


def test_verified_normalized_series_still_normal(tmp_path):
    fixture = generate_fixture(include_maps=False)
    run = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    manifest = load_manifest(run)
    result = tide_receipt(manifest, "haicang", run_dir=run)
    assert result["status"] == "verified" and result["timezone_verified"] is True
    assert result["hourly_count"] == 24 and result["unit"] == "m"
    assert all(stamp.endswith("+08:00") for stamp in result["timestamps"])
    assert result["native_timestamps"] == [] and result["heights_cm"] == []
    manifest["sites"][0]["reference_station"]["timezone_verified"] = False
    assert tide_receipt(manifest, "haicang", run_dir=run)["status"] == "unavailable"


def test_csv_preserves_native_time_cm_and_deduplicates_shared_station(frozen_native, tmp_path):
    native_manifest, run_dir = frozen_native
    before = canonical_digest(native_manifest)
    path = tmp_path / "2026-10-11_raw_tide_UNVERIFIED_TIMEZONE.csv"
    assert write_raw_tide_csv(native_manifest, path, run_dir=run_dir) == path
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 48
    assert {row["station_id"] for row in rows} == {"T122", "NT1405"}
    assert {row["timezone_verified"] for row in rows} == {"false"}
    assert {row["TimeArea"] for row in rows} == {"-0800"}
    assert {row["height_cm"] for row in rows} == {str(100.0 + hour) for hour in range(24)}
    assert all(datetime.fromisoformat(row["native_local_time"]).tzinfo is None for row in rows)
    assert canonical_digest(native_manifest) == before


def test_no_csv_without_valid_raw_receipt(native_manifest, tmp_path):
    native_manifest["quarantined_tides"] = []
    path = tmp_path / "missing.csv"
    assert write_raw_tide_csv(native_manifest, path, run_dir=tmp_path) is None
    assert not path.exists()


def test_raw_receipt_is_reparsed_and_bound_to_original_bytes(frozen_native):
    manifest, run_dir = frozen_native
    before = canonical_digest(manifest)
    records = bound_raw_tide_records(manifest, run_dir)
    assert len(records) == 2
    result = tide_receipt(manifest, "haicang", run_dir=run_dir)
    assert result["status"] == "received_unverified" and result["raw_source_bound"] is True
    assert result["heights_cm"] == manifest["quarantined_tides"][0]["hourly_values_cm"]
    assert canonical_digest(manifest) == before


@pytest.mark.parametrize("damage", ["q_values", "q_times", "q_TimeArea", "q_unit", "raw_edit", "null_body", "path_escape"])
def test_tampered_quarantine_or_raw_original_cannot_enter_csv(frozen_native, damage, tmp_path):
    manifest, run_dir = frozen_native
    record = manifest["quarantined_tides"][0]
    source = manifest["sources"][0]
    if damage == "q_values":
        record["hourly_values_cm"][8] += 99
    elif damage == "q_times":
        record["hourly_local_times"][8] = record["hourly_local_times"][9]
    elif damage == "q_TimeArea":
        record["raw_time_area"] = "+0800"
        source["metadata"]["raw_time_area"] = "+0800"
    elif damage == "q_unit":
        record["unit"] = "m"
    elif damage == "raw_edit":
        (run_dir / source["file"]).write_bytes(b"edited original")
    elif damage == "null_body":
        write_json(run_dir / source["file"], {"State": False, "Data": None})
        source["sha256"] = sha256_file(run_dir / source["file"])
    else:
        source["file"] = "../outside.json"
    assert tide_receipt(manifest, "haicang", run_dir=run_dir)["status"] == "unavailable"
    path = tmp_path / "raw.csv"
    write_raw_tide_csv(manifest, path, run_dir=run_dir)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["station_id"] for row in rows} == {"NT1405"}
    assert len(rows) == 24


def test_csv_requires_original_run_directory(native_manifest, tmp_path):
    with pytest.raises(TypeError):
        write_raw_tide_csv(native_manifest, tmp_path / "table.csv")


def test_unverified_native_source_cannot_be_promoted_by_forged_series(frozen_native):
    manifest, run_dir = frozen_native
    manifest["sites"][0]["reference_station"]["timezone_verified"] = True
    manifest["series"].append({"site_id": "haicang", "variable": "tide_height", "unit": "m",
                               "source_id": manifest["sources"][0]["id"],
                               "timestamps": [f"2026-10-11T{hour:02d}:00:00+08:00" for hour in range(24)],
                               "values": [999] * 24})
    receipt = tide_receipt(manifest, "haicang", run_dir=run_dir)
    assert receipt["status"] == "received_unverified" and receipt["timezone_verified"] is False
    assert receipt["heights_cm"] == [100.0 + hour for hour in range(24)]


def test_verified_series_and_station_evidence_both_bound(tmp_path):
    fixture = generate_fixture(include_maps=False)
    run = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    manifest = load_manifest(run)
    assert tide_receipt(manifest, "haicang", run_dir=run)["status"] == "verified"
    row = next(row for row in manifest["series"] if row["site_id"] == "haicang" and row["variable"] == "tide_height")
    original_values = row["values"][:]
    row["values"][5] = 999
    assert tide_receipt(manifest, "haicang", run_dir=run)["status"] == "unavailable"
    row["values"] = original_values
    manifest["sites"][0]["reference_station"]["datum"] = "forged datum"
    assert tide_receipt(manifest, "haicang", run_dir=run)["status"] == "unavailable"


def test_web_snapshot_exposes_bound_receipts_without_promoting_blocked_run(frozen_native, monkeypatch):
    from xiamen_marine_report.web import JobStore

    manifest, run_dir = frozen_native
    manifest.update(run_id="mock-native-run", schema_version=1, state="FETCHED", timezone="Asia/Shanghai",
                    created_at="2026-10-10T00:00:00Z", analysis_provider="none", extrema=[],
                    official_documents=[], gaps=[])
    write_json(run_dir / "manifest.json", manifest)
    before = sha256_file(run_dir / "manifest.json")
    monkeypatch.setattr("xiamen_marine_report.fetch.fetch_run", lambda *_args, **_kwargs: run_dir)
    store = JobStore(run_dir)
    monkeypatch.setattr(store.executor, "submit", lambda *_args: None)
    request = {"mode": "live", "date": "2026-10-11", "sites": ["haicang", "wuyuan", "huandong"], "allow_partial": False}
    try:
        job_id = store.create(request)
        store._run(job_id, request)
        job = store.snapshot(job_id)
        assert job["status"] == "blocked"
        assert len(job["tide_receipts"]) == 3
        assert all(receipt["status"] == "received_unverified" and receipt["hourly_count"] == 24
                   and receipt["raw_source_bound"] is True for receipt in job["tide_receipts"])
        assert sha256_file(run_dir / "manifest.json") == before
    finally:
        store.executor.shutdown(wait=True)
