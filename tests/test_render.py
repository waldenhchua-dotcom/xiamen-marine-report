"""Meaningful offline artifact, hourly-axis, direction and fail-closed tests."""

import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from PIL import Image
from pypdf import PdfReader

from xiamen_marine_report.demo import generate_fixture
from xiamen_marine_report.provenance import read_json, save_run, sha256_file, write_json
from xiamen_marine_report.render import render_run
from xiamen_marine_report.render.charts import direction_vector, hourly_ticks, series_map, wind_from_name
from xiamen_marine_report.render.style import FONT_PATH, status_label
from xiamen_marine_report.review import review_candidate, review_status, verify_candidate
from xiamen_marine_report.sunset import sunset_for_date


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    root = tmp_path_factory.mktemp("rendered")
    fixture = generate_fixture()
    run = save_run(root / "runs", fixture["manifest"], fixture["raw_objects"])
    path = render_run(run, root / "delivery")
    return run, path, read_json(path)


def test_from_west_and_toward_east_both_point_east():
    assert direction_vector(270, 3, "from") == pytest.approx((1, 0), abs=1e-10)
    assert wind_from_name(270) == "西风"
    assert direction_vector(90, .5, "toward") == pytest.approx((1, 0), abs=1e-10)


@pytest.mark.parametrize("direction,speed", [(90, 0), (None, 2), (90, None), (math.nan, 2), (90, math.nan)])
def test_calm_missing_or_nonfinite_has_no_direction_arrow(direction, speed):
    assert direction_vector(direction, speed, "toward") is None


def test_every_clock_hour_and_cross_day_midnight():
    tz = ZoneInfo("Asia/Shanghai")
    ticks = hourly_ticks(datetime(2026, 10, 10, 22, 10, tzinfo=tz), datetime(2026, 10, 11, 8, tzinfo=tz))
    assert [t.hour for t in ticks] == [23, 0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert ticks[1].date().isoformat() == "2026-10-11"


def test_partial_display_suppresses_unverified_units_directions_and_tide_timezone():
    manifest = generate_fixture()["manifest"]
    next(s for s in manifest["series"] if s["site_id"] == "haicang" and s["variable"] == "wind_speed")["unit"] = "kn"
    next(s for s in manifest["series"] if s["site_id"] == "haicang" and s["variable"] == "current_direction")["direction_convention"] = "from"
    manifest["sites"][0]["reference_station"]["timezone_verified"] = False
    display = series_map(manifest, "haicang")
    for variable in ("wind_speed", "current_direction", "tide_height"):
        assert display[variable]["timestamps"] == []
        assert display[variable]["render_suppressed"] is True
    assert wind_from_name(math.nan) == "缺测"


def test_portable_openly_licensed_chinese_font():
    assert FONT_PATH.is_file()
    license_text = (FONT_PATH.parent / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text


def test_local_sunset_each_covered_date():
    fixture = generate_fixture()
    for site in fixture["manifest"]["sites"]:
        for date in ("2026-10-10", "2026-10-11"):
            record = sunset_for_date(site, date)
            assert record["time"].endswith("+08:00")
            assert record["date"] == date
            assert datetime.fromisoformat(record["time"]).hour in (17, 18)
            assert "约/估算" in record["limitations"]


def test_real_pdf_three_comprehensive_pngs_and_frozen_bundle(rendered):
    run, path, candidate = rendered
    assert candidate["state"] == "AWAITING_REVIEW"
    assert candidate["synthetic"] is True
    assert candidate["delivery_label"] == "UNAPPROVED_REFERENCE"
    _, verified = verify_candidate(run)
    assert verified["candidate_digest"] == candidate["candidate_digest"]
    assert path.is_relative_to(run / "candidates")
    artifacts = candidate["artifacts"]
    pdfs = [path.parent / a["file"] for a in artifacts if a["kind"] == "pdf"]
    pngs = [path.parent / a["file"] for a in artifacts if a["kind"] == "png"]
    assert len(pdfs) == 1 and len(pngs) == 3
    assert pdfs[0].read_bytes().startswith(b"%PDF-")
    reader = PdfReader(pdfs[0])
    assert len(reader.pages) >= 18
    text = "\n".join(page.extract_text() for page in reader.pages)
    for label in ("海沧湾", "五缘湾", "环东", "2026-10-10", "2026-10-11", "UNAPPROVED_REFERENCE", "SYNTHETIC", "雷暴"):
        assert label in text
    assert any(float(page.mediabox.width) > 1100 for page in reader.pages)
    for png in pngs:
        assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(png) as image:
            assert image.width >= 2400
            assert image.height >= 6000
    for artifact in artifacts:
        assert sha256_file(path.parent / artifact["file"]) == artifact["sha256"]
    assert (path.parent / "manifest.json").read_bytes() == (run / "manifest.json").read_bytes()
    assert len(read_json(path.parent / "sunsets.json")) == 6
    assert not (path.parent / "_build").exists()


def test_full_render_refuses_blocked_run(tmp_path):
    fixture = generate_fixture()
    fixture["manifest"]["official_documents"] = []
    run = save_run(tmp_path / "runs", fixture["manifest"], fixture["raw_objects"])
    with pytest.raises(ValueError, match="allow-partial"):
        render_run(run, tmp_path / "reports")
    assert not (run / "candidates").exists()


def test_corrupt_source_refused_even_when_partial_allowed(tmp_path):
    fixture = generate_fixture()
    run = save_run(tmp_path / "runs", fixture["manifest"], fixture["raw_objects"])
    manifest = read_json(run / "manifest.json")
    (run / manifest["sources"][0]["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash invalid"):
        render_run(run, tmp_path / "reports", allow_partial=True)


def test_partial_single_site_is_explicit_and_never_awaiting_review(tmp_path):
    fixture = generate_fixture()
    manifest = fixture["manifest"]
    manifest["sites"] = [s for s in manifest["sites"] if s["id"] == "haicang"]
    manifest["series"] = [s for s in manifest["series"] if s["site_id"] == "haicang"]
    manifest["official_documents"] = []
    run = save_run(tmp_path / "runs", manifest, fixture["raw_objects"])
    path = render_run(run, tmp_path / "reports", allow_partial=True)
    candidate = read_json(path)
    assert candidate["state"] == "BLOCKED"
    pdf = next(path.parent.glob("*.pdf"))
    assert "PARTIAL" in pdf.name
    text = "\n".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "PARTIAL" in text and "未取得目标日正文" in text
    assert "SYNTHETIC" in status_label(manifest, True)


def test_artifact_change_invalidates_candidate_hash(rendered):
    # An independent copied run demonstrates tampering without altering shared render fixtures.
    run, path, candidate = rendered
    file = next(a for a in candidate["artifacts"] if a["kind"] == "png")
    artifact = path.parent / file["file"]
    original = artifact.read_bytes()
    try:
        artifact.write_bytes(original + b"modified")
        with pytest.raises(ValueError, match="artifact changed"):
            verify_candidate(run)
    finally:
        artifact.write_bytes(original)
    assert verify_candidate(run)[1]["candidate_digest"] == candidate["candidate_digest"]


def test_rerender_creates_new_candidate_and_invalidates_old_detached_review(rendered, tmp_path):
    run, old_path, original = rendered
    review_file = tmp_path / "review-input.json"
    write_json(review_file, {"reviewer": "unit-test-independent-reviewer",
                            "checked_at": datetime.now(timezone.utc).isoformat(),
                            "candidate_digest": original["candidate_digest"], "checklist": {}})
    review_candidate(run, "reject", review_file)
    assert review_status(run)["valid"] is True
    new_path = render_run(run, tmp_path / "rerendered-delivery")
    new = read_json(new_path)
    assert new_path != old_path
    assert new["manifest_sha256"] == original["manifest_sha256"]
    assert new["candidate_digest"] != original["candidate_digest"]
    assert review_status(run)["valid"] is False
    assert verify_candidate(run, old_path)[1]["candidate_digest"] == original["candidate_digest"]
