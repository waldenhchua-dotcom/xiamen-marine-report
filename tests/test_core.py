"""Deterministic fail-closed boundary and evidence-binding regression tests."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from xiamen_marine_report.cli import main
from xiamen_marine_report.coverage import hourly_end, select_flood_segments
from xiamen_marine_report.demo import generate_fixture as generate_complete_fixture
from xiamen_marine_report.demo import load_fixture
from xiamen_marine_report.models import MAP_REVIEW_CHECKLIST, REQUIRED_VARIABLES, REVIEW_CHECKLIST
from xiamen_marine_report.provenance import (
    load_manifest,
    read_json,
    safe_relative_path,
    sanitize_request,
    sanitize_url,
    save_run,
    sha256_file,
    write_json,
)
from xiamen_marine_report.review import candidate_digest, review_candidate, review_status, verify_candidate
from xiamen_marine_report.validate import validate_run


def generate_fixture(date: str = "2026-10-10") -> dict:
    """Core regressions isolate marine contracts; map integration tests use full fixtures."""
    return generate_complete_fixture(date, include_maps=False)


@pytest.fixture
def frozen_run(tmp_path: Path) -> Path:
    fixture = generate_fixture()
    return save_run(tmp_path / "runs", fixture["manifest"], fixture["raw_objects"])


def edit_manifest(run: Path, edit) -> None:
    manifest = load_manifest(run)
    edit(manifest)
    write_json(run / "manifest.json", manifest)


def candidate(run: Path, name: str = "candidate-one") -> tuple[Path, dict]:
    directory = run / "candidates" / name
    directory.mkdir(parents=True)
    artifact = directory / "synthetic_UNAPPROVED_REFERENCE.png"
    artifact.write_bytes(b"test artifact bytes")
    manifest = load_manifest(run)
    result = {"candidate_id": name, "request_date": manifest["request_date"],
              "state": "AWAITING_REVIEW", "validation_state": "VALIDATED",
              "synthetic": True, "manifest_sha256": sha256_file(run / "manifest.json"),
              "artifacts": [{"file": artifact.name, "sha256": sha256_file(artifact), "kind": "png"}]}
    result["candidate_digest"] = candidate_digest(result)
    path = directory / "candidate.json"
    write_json(path, result)
    write_json(run / "latest_candidate.json", {"file": path.relative_to(run).as_posix()})
    return path, result


def checklist_file(run: Path, record: dict) -> Path:
    path = run / "review-input.json"
    write_json(path, {"reviewer": {"id": "independent-human"},
                      "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                      "candidate_digest": record["candidate_digest"],
                      "checklist": dict.fromkeys(REVIEW_CHECKLIST, True)})
    return path


def map_candidate(run: Path) -> tuple[Path, dict]:
    """Small real PNG structures and mock source bytes isolate the map review gate."""
    from PIL import Image

    manifest = load_manifest(run)
    for source_id, original in (("map-test-original", None), ("map-test-derived", "map-test-original")):
        evidence = run / "evidence" / f"{source_id}.json"
        write_json(evidence, {"test_only": True, "type": "FeatureCollection", "features": []})
        metadata = {"crs": "WGS84", "time_convention": "Static mock snapshot; no forecast validity"}
        if original:
            metadata["original_source_id"] = original
        manifest["sources"].append({"id": source_id, "file": evidence.relative_to(run).as_posix(),
                                    "sha256": sha256_file(evidence), "retrieved_at": "2026-10-10T00:00:00Z",
                                    "request": {"url": "https://maps.example.org/test-only", "params": {}},
                                    "http_status": 200, "kind": "map", "metadata": metadata,
                                    "license": "Mock ODbL provenance for tests, not a real geographic dataset"})
    write_json(run / "manifest.json", manifest)
    path, record = candidate(run)
    directory = path.parent
    record["maps_required"] = True
    for site in manifest["sites"]:
        site_id = site["id"]
        if site_id != "haicang":
            png = directory / f"{site_id}_comprehensive.png"
            png.write_bytes(b"test comprehensive placeholder")
            record["artifacts"].append({"file": png.name, "sha256": sha256_file(png), "kind": "png"})
        png = directory / f"{site_id}_map.png"
        Image.new("RGB", (1200, 600), "white").save(png)
        record["artifacts"].append({"file": png.name, "sha256": sha256_file(png), "kind": "map_png",
                                    "role": "map", "site_id": site_id, "map_source_id": "map-test-derived"})
    pdf = directory / "combined.pdf"
    pdf.write_bytes(b"%PDF-1.4 test placeholder")
    record["artifacts"].append({"file": pdf.name, "sha256": sha256_file(pdf), "kind": "pdf"})
    metadata_path = directory / "map_sources.json"
    write_json(metadata_path, {"maps": [{"site_id": site["id"], "source_id": "map-test-derived"}
                                        for site in manifest["sites"]]})
    record["artifacts"].append({"file": metadata_path.name, "sha256": sha256_file(metadata_path), "kind": "map_metadata"})
    for source in manifest["sources"][-2:]:
        copied = directory / source["file"]
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes((run / source["file"]).read_bytes())
        record["artifacts"].append({"file": source["file"], "sha256": source["sha256"], "kind": "evidence"})
    record["candidate_digest"] = candidate_digest(record)
    write_json(path, record)
    return path, record


def write_changed_candidate(path: Path, record: dict) -> None:
    record["candidate_digest"] = candidate_digest(record)
    write_json(path, record)


def test_fixture_full_cross_day_distinct_station_coverage(frozen_run: Path):
    result = validate_run(frozen_run)
    assert result["state"] == "VALIDATED", result["errors"]
    assert result["coverage"]["haicang"]["end"] == "2026-10-11T08:00:00+08:00"
    assert result["coverage"]["wuyuan"]["end"] == "2026-10-11T09:00:00+08:00"
    assert result["coverage"]["huandong"]["end"] == result["coverage"]["wuyuan"]["end"]
    assert all(len(value["segments"]) == 3 for value in result["coverage"].values())
    assert any("THUNDERSTORM MODEL SIGNAL" in item for item in result["warnings"])


def test_fixture_file_is_offline_and_synthetic():
    fixture = load_fixture(Path(__file__).parent / "fixtures" / "demo.json")
    assert fixture["manifest"]["synthetic"] is True
    assert len(fixture["manifest"]["sites"]) == 3
    assert all(isinstance(value, bytes) for value in fixture["raw_objects"].values())
    maps = [source for source in fixture["manifest"]["sources"] if source["kind"] == "map"]
    assert len(maps) == 2 and all(source["metadata"]["crs"] == "WGS84" for source in maps)


def test_after_midnight_high_does_not_start_segment():
    fixture = generate_fixture()["manifest"]
    segments = select_flood_segments(fixture["extrema"], "2026-10-10", "SYNTHETIC-T122")
    assert segments[0]["low"]["time"] == "2026-10-10T02:40:00+08:00"
    assert segments[0]["high"]["time"] == "2026-10-10T07:20:00+08:00"


@pytest.mark.parametrize("high,end", [("2026-10-11T08:00:00+08:00", "2026-10-11T09:00:00+08:00"),
                                    ("2026-10-11T08:40:00+08:00", "2026-10-11T09:00:00+08:00"),
                                    ("2026-10-10T16:00:00Z", "2026-10-11T01:00:00+08:00")])
def test_strictly_later_hour(high: str, end: str):
    assert hourly_end(high) == end


@pytest.mark.parametrize("malformation", ["duplicate", "nonalternating", "no_timezone", "null_height", "no_third_high", "wrong_date"])
def test_extrema_fail_closed(malformation: str):
    entries = generate_fixture()["manifest"]["extrema"]
    station = "SYNTHETIC-T122"
    relevant = [entry for entry in entries if entry["station_id"] == station]
    if malformation == "duplicate":
        relevant.append(dict(relevant[-1]))
    elif malformation == "nonalternating":
        relevant[2]["type"] = "low"
    elif malformation == "no_timezone":
        relevant[2]["time"] = "2026-10-10T08:00:00"
    elif malformation == "null_height":
        relevant[2]["height"] = None
    elif malformation == "no_third_high":
        relevant.pop()
    else:
        relevant = [item for item in relevant if item["time"].startswith("2026-10-11")]
    with pytest.raises(ValueError):
        select_flood_segments(relevant, "2026-10-10", station)


@pytest.mark.parametrize("variable", REQUIRED_VARIABLES)
def test_every_variable_must_reach_own_next_hour(frozen_run: Path, variable: str):
    def edit(manifest):
        series = next(s for s in manifest["series"] if s["site_id"] == "wuyuan" and s["variable"] == variable)
        series["values"][-1] = None
    edit_manifest(frozen_run, edit)
    result = validate_run(frozen_run)
    assert result["state"] == "BLOCKED_CROSS_DAY_DATA"
    assert any(f"wuyuan/{variable}: missing" in message for message in result["errors"])


def test_missing_third_day_hourly_data_blocks(frozen_run: Path):
    def edit(manifest):
        extrema = [entry for entry in manifest["extrema"] if entry["station_id"] == "SYNTHETIC-T122"]
        for entry in extrema[-2:]:
            entry["time"] = (datetime.fromisoformat(entry["time"]) + timedelta(days=1)).isoformat()
    edit_manifest(frozen_run, edit)
    result = validate_run(frozen_run)
    assert result["state"] == "BLOCKED_CROSS_DAY_DATA"
    assert result["coverage"]["haicang"]["end"].startswith("2026-10-12")


def test_http_200_false_data_is_not_success(frozen_run: Path):
    manifest = load_manifest(frozen_run)
    source = manifest["sources"][0]
    path = frozen_run / source["file"]
    write_json(path, {"State": False, "Data": None})
    source["sha256"] = sha256_file(path)
    write_json(frozen_run / "manifest.json", manifest)
    assert validate_run(frozen_run)["state"] == "BLOCKED_CROSS_DAY_DATA"


@pytest.mark.parametrize("mutation", ["units", "direction", "grid", "station_timezone", "official_body", "length", "duplicate_hour", "source_hash", "datum"])
def test_metadata_and_evidence_errors_block(frozen_run: Path, mutation: str):
    def edit(manifest):
        if mutation == "units":
            manifest["series"][1]["unit"] = "km/h"
        elif mutation == "direction":
            next(s for s in manifest["series"] if s["variable"] == "wind_direction")["direction_convention"] = "toward"
        elif mutation == "grid":
            manifest["series"][1]["grid"]["longitude"] = 119.5
        elif mutation == "station_timezone":
            manifest["sites"][0]["reference_station"]["timezone_verified"] = False
        elif mutation == "official_body":
            manifest["official_documents"][0].update(status="not_obtained", source_id=None)
        elif mutation == "length":
            manifest["series"][0]["values"].pop()
        elif mutation == "duplicate_hour":
            manifest["series"][0]["timestamps"][3] = manifest["series"][0]["timestamps"][2]
        elif mutation == "source_hash":
            manifest["sources"][0]["sha256"] = "0" * 64
        else:
            manifest["series"][0]["datum"] = "unrelated datum"
    edit_manifest(frozen_run, edit)
    assert validate_run(frozen_run)["state"].startswith("BLOCKED")


def test_thunderstorm_with_missing_warning_never_claims_no_warning(frozen_run: Path):
    edit_manifest(frozen_run, lambda m: m.update(official_documents=[]))
    result = validate_run(frozen_run)
    assert result["state"] == "BLOCKED"
    assert any("THUNDERSTORM MODEL SIGNAL" in warning for warning in result["warnings"])
    assert any("warning status unconfirmed" in error for error in result["errors"])


def test_evidence_tamper_detected(frozen_run: Path):
    manifest = load_manifest(frozen_run)
    path = frozen_run / manifest["sources"][0]["file"]
    path.write_bytes(path.read_bytes() + b" ")
    assert any("SHA-256 mismatch" in error for error in validate_run(frozen_run)["errors"])


def test_save_run_always_new_version(tmp_path: Path):
    fixture = generate_fixture()
    first = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    digest = sha256_file(first / "manifest.json")
    second = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    assert first != second and sha256_file(first / "manifest.json") == digest


def test_credentials_sanitized_in_full_url_and_parameters():
    request = sanitize_request({"url": "https://user:password@weather.example.org/api?date=2026-10-10&api_key=do-not-publish#secret-fragment",
                                "params": {"token": "do-not-publish", "nested": {"Authorization": "Bearer do-not-publish"}, "date": "2026-10-10"}})
    text = json.dumps(request)
    assert "do-not-publish" not in text and "password@" not in text and "secret-fragment" not in text
    assert "2026-10-10" in text
    assert sanitize_url("http://127.0.0.1:8000/internal") == "[REDACTED_PRIVATE_URL]"
    fictional_local_url = "file:///C:" + "/Users" + "/private/evidence"
    assert sanitize_url(fictional_local_url) == "[REDACTED_NON_HTTP_URL]"


@pytest.mark.parametrize("path", ["../outside.json", "a/../../outside.json", "C:" + "/Users" + "/private.json", "/private.json", "\\\\server\\share\\secret.json", "a\\..\\private.json", "secret.json:stream", "./a.json", "evidence/CON", "evidence/NUL.txt", "evidence/com1.json", "evidence/file.json.", "evidence/file.json "])
def test_dangerous_relative_paths_rejected(tmp_path: Path, path: str):
    with pytest.raises(ValueError):
        safe_relative_path(tmp_path, path)


def test_review_tamper_invalidates_approval(frozen_run: Path):
    path, record = candidate(frozen_run)
    review = review_candidate(frozen_run, "approve", checklist_file(frozen_run, record))
    assert review["state"] == "APPROVED_REPORT"
    assert review["delivery_label"] == "UNAPPROVED_REFERENCE"
    artifact = path.parent / record["artifacts"][0]["file"]
    artifact.write_bytes(b"edited PNG")
    assert review_status(frozen_run)["valid"] is False
    with pytest.raises(ValueError, match="artifact changed"):
        review_candidate(frozen_run, "approve", checklist_file(frozen_run, record))


def test_new_candidate_invalidates_old_review(frozen_run: Path):
    _, record = candidate(frozen_run)
    review_candidate(frozen_run, "approve", checklist_file(frozen_run, record))
    candidate(frozen_run, "candidate-two")
    assert review_status(frozen_run)["valid"] is False


def test_manifest_tamper_invalidates_review(frozen_run: Path):
    _, record = candidate(frozen_run)
    review_candidate(frozen_run, "approve", checklist_file(frozen_run, record))
    edit_manifest(frozen_run, lambda manifest: manifest.update(analysis_provider="forged"))
    assert review_status(frozen_run)["valid"] is False


def test_review_cannot_self_approve_or_skip_checklist(frozen_run: Path):
    _, record = candidate(frozen_run)
    path = checklist_file(frozen_run, record)
    review = read_json(path)
    review["reviewer"] = "none"
    write_json(path, review)
    with pytest.raises(ValueError, match="own report"):
        review_candidate(frozen_run, "approve", path)
    review["reviewer"] = "independent-human"
    review["checklist"][REVIEW_CHECKLIST[-1]] = False
    write_json(path, review)
    with pytest.raises(ValueError, match="every independent"):
        review_candidate(frozen_run, "approve", path)
    assert not (frozen_run / "review.json").exists()


def test_review_reject_can_record_failed_checklist(frozen_run: Path):
    _, record = candidate(frozen_run)
    path = checklist_file(frozen_run, record)
    review = read_json(path)
    review["checklist"][REVIEW_CHECKLIST[-1]] = False
    write_json(path, review)
    assert review_candidate(frozen_run, "reject", path)["state"] == "REJECTED"


def test_review_dangerous_pointer_rejected(frozen_run: Path):
    _, record = candidate(frozen_run)
    path = checklist_file(frozen_run, record)
    write_json(frozen_run / "latest_candidate.json", {"file": "../../private/candidate.json"})
    with pytest.raises(ValueError, match="Unsafe"):
        review_candidate(frozen_run, "approve", path)


def test_cli_validate_returns_json_and_exit_code(frozen_run: Path, capsys):
    assert main(["validate", "--run", str(frozen_run)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "VALIDATED"
    assert (frozen_run / "validation.json").is_file()
    edit_manifest(frozen_run, lambda m: m.update(official_documents=[]))
    assert main(["validate", "--run", str(frozen_run)]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "BLOCKED"


def test_json_duplicate_keys_are_rejected(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text('{"state":"BLOCKED","state":"APPROVED_REPORT"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        read_json(path)


@pytest.mark.parametrize("record", ["series", "extremum", "official"])
def test_normalized_numbers_and_text_are_bound_to_raw_bytes(frozen_run: Path, record: str):
    def edit(manifest):
        if record == "series":
            manifest["series"][1]["values"][5] += 0.1
        elif record == "extremum":
            manifest["extrema"][2]["height"] += 0.1
        else:
            manifest["official_documents"][0]["summary"] = "Falsely claimed absence of warnings"
    edit_manifest(frozen_run, edit)
    result = validate_run(frozen_run)
    assert result["state"] == "BLOCKED"
    assert any("frozen" in error for error in result["errors"])


@pytest.mark.parametrize("field,value", [("sites", [{"id": []}]), ("extrema", [{"source_id": []}]),
                                        ("series", {}), ("gaps", [{"code": [], "blocking": True}])])
def test_malicious_manifest_shapes_fail_closed(frozen_run: Path, field: str, value):
    edit_manifest(frozen_run, lambda manifest: manifest.update({field: value}))
    assert validate_run(frozen_run)["state"].startswith("BLOCKED")


def test_manifest_never_modified_by_validation(frozen_run: Path):
    before = sha256_file(frozen_run / "manifest.json")
    validate_run(frozen_run)
    assert sha256_file(frozen_run / "manifest.json") == before


def test_unused_optional_tide_probe_failure_does_not_invent_cross_day_gap(tmp_path: Path):
    fixture = generate_fixture()
    source = deepcopy(fixture["manifest"]["sources"][0])
    source.update(id="optional-third-day-probe", http_status=503)
    source["metadata"]["required_for_coverage"] = False
    fixture["manifest"]["sources"].append(source)
    fixture["raw_objects"][source["id"]] = b'{"State":false,"Data":null}'
    run = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    result = validate_run(run)
    assert result["state"] == "VALIDATED", result["errors"]
    assert any("optional unused probe" in warning for warning in result["warnings"])
    evidence = load_manifest(run)["sources"][-1]
    (run / evidence["file"]).write_bytes(b"corrupted")
    assert validate_run(run)["state"] == "BLOCKED"


def test_referenced_required_source_cannot_bypass_gate_by_claiming_optional(frozen_run: Path):
    manifest = load_manifest(frozen_run)
    source = manifest["sources"][0]
    source["metadata"]["required_for_coverage"] = False
    raw = frozen_run / source["file"]
    raw.write_bytes(b'{"State":false,"Data":null}')
    source["sha256"] = sha256_file(raw)
    write_json(frozen_run / "manifest.json", manifest)
    assert validate_run(frozen_run)["state"] == "BLOCKED_CROSS_DAY_DATA"


@pytest.mark.parametrize("wrong_hourly_value,kind", [(0.0, "low"), (10.0, "high")])
def test_source_bound_tide_curve_must_agree_with_minute_extrema(tmp_path: Path, wrong_hourly_value: float, kind: str):
    fixture = generate_fixture()
    tide = next(row for row in fixture["manifest"]["series"]
                if row["site_id"] == "haicang" and row["variable"] == "tide_height")
    tide["values"] = [wrong_hourly_value] * len(tide["timestamps"])
    source = json.loads(fixture["raw_objects"][tide["source_id"]])
    source["series"] = [row for row in fixture["manifest"]["series"] if row["site_id"] == "haicang"]
    fixture["raw_objects"][tide["source_id"]] = json.dumps(source, ensure_ascii=False).encode("utf-8")
    run = save_run(tmp_path, fixture["manifest"], fixture["raw_objects"])
    result = validate_run(run)
    assert result["state"] == "BLOCKED"
    assert any(f"{kind} extremum/curve contradiction" in error for error in result["errors"])
    assert not any("normalized values" in error for error in result["errors"])


def test_independent_maps_and_attribution_need_explicit_review(frozen_run: Path):
    _, record = map_candidate(frozen_run)
    path = checklist_file(frozen_run, record)
    with pytest.raises(ValueError, match="map images and source attribution"):
        review_candidate(frozen_run, "approve", path)
    review = read_json(path)
    review["checklist"].update(dict.fromkeys(MAP_REVIEW_CHECKLIST, True))
    write_json(path, review)
    assert review_candidate(frozen_run, "approve", path)["state"] == "APPROVED_REPORT"
    assert review_status(frozen_run)["valid"] is True


@pytest.mark.parametrize("edit", ["delete_map", "erase_map_kind", "erase_source", "erase_metadata", "erase_all_maps", "low_resolution", "erase_source_copy"])
def test_map_candidate_cannot_downgrade_or_omit_bound_outputs(frozen_run: Path, edit: str):
    from PIL import Image

    path, record = map_candidate(frozen_run)
    maps = [artifact for artifact in record["artifacts"] if artifact["kind"] == "map_png"]
    if edit == "delete_map":
        record["artifacts"].remove(maps[0])
    elif edit == "erase_map_kind":
        maps[0]["kind"] = "index"
    elif edit == "erase_source":
        maps[0].pop("map_source_id")
    elif edit == "erase_metadata":
        record["artifacts"] = [artifact for artifact in record["artifacts"] if artifact["kind"] != "map_metadata"]
    elif edit == "erase_all_maps":
        record["artifacts"] = [artifact for artifact in record["artifacts"] if artifact["kind"] not in {"map_png", "map_metadata"}]
        record.pop("maps_required")
    elif edit == "low_resolution":
        image_path = path.parent / maps[0]["file"]
        Image.new("RGB", (200, 100), "white").save(image_path)
        maps[0]["sha256"] = sha256_file(image_path)
    else:
        record["artifacts"] = [artifact for artifact in record["artifacts"] if artifact["kind"] != "evidence"]
    write_changed_candidate(path, record)
    with pytest.raises(ValueError):
        verify_candidate(frozen_run)


def test_map_image_and_source_metadata_edit_invalidate_review(frozen_run: Path):
    path, record = map_candidate(frozen_run)
    review_path = checklist_file(frozen_run, record)
    review = read_json(review_path)
    review["checklist"].update(dict.fromkeys(MAP_REVIEW_CHECKLIST, True))
    write_json(review_path, review)
    review_candidate(frozen_run, "approve", review_path)
    (path.parent / "map_sources.json").write_text("{}", encoding="utf-8")
    assert review_status(frozen_run)["valid"] is False


@pytest.mark.parametrize("change", ["unknown_id", "wrong_hash"])
def test_fixture_map_references_use_only_pinned_bundled_ids(tmp_path: Path, change: str):
    fixture = read_json(Path(__file__).parent / "fixtures" / "demo.json")
    source_id = next(source["id"] for source in fixture["manifest"]["sources"] if source["kind"] == "map")
    reference = fixture["raw_objects"][source_id]
    if change == "unknown_id":
        reference["bundled_map_source_id"] = "../../private/source"
    else:
        reference["sha256"] = "0" * 64
    path = tmp_path / "fixture.json"
    write_json(path, fixture)
    with pytest.raises(ValueError, match="verified bundled source"):
        load_fixture(path)
