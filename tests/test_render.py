"""Meaningful offline artifact, hourly-axis, direction and fail-closed tests."""

import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema import validate
from PIL import Image
from pypdf import PdfReader

from xiamen_marine_report.demo import generate_fixture
from xiamen_marine_report.provenance import canonical_digest, read_json, save_run, sha256_file, write_json
from xiamen_marine_report.render import render_run
from xiamen_marine_report.render.charts import (
    direction_vector,
    hourly_ticks,
    render_day_chart,
    series_map,
    wind_from_name,
)
from xiamen_marine_report.render.maps import _valid_point, coastline_land_cells, current_arrow_sample
from xiamen_marine_report.render.pdf import _raw_tide_appendix, official_display
from xiamen_marine_report.render.style import BLUE, FONT_PATH, configure_fonts, status_label
from xiamen_marine_report.review import review_candidate, review_status, verify_candidate
from xiamen_marine_report.sunset import sunset_for_date
from xiamen_marine_report.tide_status import write_raw_tide_csv


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
    schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "candidate.schema.json")
    validate(candidate, schema)
    assert verified["candidate_digest"] == candidate["candidate_digest"]
    assert path.is_relative_to(run / "candidates")
    artifacts = candidate["artifacts"]
    pdfs = [path.parent / a["file"] for a in artifacts if a["kind"] == "pdf"]
    pngs = [path.parent / a["file"] for a in artifacts if a["kind"] == "png"]
    assert len(pdfs) == 1 and len(pngs) == 3
    assert pdfs[0].read_bytes().startswith(b"%PDF-")
    reader = PdfReader(pdfs[0])
    assert len(reader.pages) >= 21
    text = "\n".join(page.extract_text() for page in reader.pages)
    for label in ("海沧湾", "五缘湾", "环东", "2026-10-10", "2026-10-11", "UNAPPROVED_REFERENCE", "SYNTHETIC", "雷暴"):
        assert label in text
    assert any(float(page.mediabox.width) > 1100 for page in reader.pages)
    for png in pngs:
        assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(png) as image:
            assert image.width >= 2400
            assert image.height >= 6000
    maps = [a for a in artifacts if a["kind"] == "map_png"]
    assert len(maps) == 3
    assert {a["site_id"] for a in maps} == {"haicang", "wuyuan", "huandong"}
    for artifact in maps:
        assert artifact["role"] == "map"
        assert artifact["map_source_id"] == "map_osm_geojson"
        with Image.open(path.parent / artifact["file"]) as image:
            assert image.width >= 3000 and image.height >= 1800
            assert len(image.resize((120, 80)).getcolors(9600) or []) > 100
    assert len([a for a in artifacts if a["kind"] == "map_metadata"]) == 1
    metadata = read_json(path.parent / "map_sources.json")
    assert metadata["static_basemap"] is True
    assert metadata["overlays_synthetic"] is True
    assert "OpenStreetMap" in metadata["attribution"]
    assert metadata["geometry_source_id"] == "map_osm_geojson"
    assert metadata["original_source_id"] == "map_osm_raw"
    assert "海岸、道路与证据点地图" in text
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
    assert "PARTIAL" in text and "目标日正文未取得" in text
    assert "SYNTHETIC" in status_label(manifest, True)


def test_unverified_station_crs_is_never_overlaid_on_wgs84():
    assert _valid_point({"latitude": 24.5, "longitude": 118.1, "crs": "WGS84"}, station=True)
    assert not _valid_point({"latitude": 24.5, "longitude": 118.1, "crs": "unverified"}, station=True)
    assert not _valid_point({"latitude": 24.5, "longitude": 118.1}, station=True)


@pytest.mark.parametrize("latitude,longitude", [(math.nan, 118), (24, math.inf), (True, 118), (95, 118), (24, 200)])
def test_invalid_coordinates_are_not_mapped_or_used_for_plot_limits(latitude, longitude):
    assert not _valid_point({"latitude": latitude, "longitude": longitude})


def test_current_arrow_never_combines_speed_and_direction_from_different_grids():
    manifest = generate_fixture()["manifest"]
    mapping = series_map(manifest, "haicang")
    assert current_arrow_sample(mapping)[0] is not None
    mapping["current_speed"] = {**mapping["current_speed"],
                                "grid": {**mapping["current_speed"]["grid"], "longitude": 118.2}}
    assert current_arrow_sample(mapping)[0] is None
    assert "不同格点" in current_arrow_sample(mapping)[1]


def test_directed_coast_clipping_fills_only_documented_land_left():
    from shapely.geometry import Point
    features = [{"type": "Feature", "properties": {"layer": "coastline"},
                 "geometry": {"type": "LineString", "coordinates": [[0, 2], [0, -2]]}}]
    land = coastline_land_cells(features, (-1, -1, 1, 1))
    # Facing south, the documented left side is east. The map border closes the
    # clipped color region, without adding a fake coast between source endpoints.
    assert any(p.contains(Point(.5, 0)) for p in land)
    assert not any(p.contains(Point(-.5, 0)) for p in land)


def test_missing_coastline_gap_is_never_connected_into_land():
    features = [{"type": "Feature", "properties": {"layer": "coastline"},
                 "geometry": {"type": "LineString", "coordinates": [[0, .5], [0, -.5]]}}]
    assert coastline_land_cells(features, (-1, -1, 1, 1)) == []


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


@pytest.fixture
def native_render_fixture(tmp_path):
    """Two hash-bound native mock tables; shared NT1405 is intentionally reused."""
    day = "2026-10-11"
    manifest = {"request_date": day, "synthetic": True, "analysis_provider": "none", "series": [],
                "sources": [], "extrema": [], "quarantined_tides": [], "sites": []}
    for site_id, station_id, name in (("haicang", "T122", "海沧湾"), ("wuyuan", "NT1405", "五缘湾"), ("huandong", "NT1405", "环东")):
        manifest["sites"].append({"id": site_id, "name": name,
                                  "target_point": {"latitude": 24.5, "longitude": 118.1},
                                  "reference_station": {"id": station_id, "name": "合成原始表测试站",
                                                        "timezone_verified": False, "datum": "合成 cm 基准面"}})
    for station_id in ("T122", "NT1405"):
        source_id = f"tide_{station_id}_{day}"
        values = [100.0 + hour for hour in range(24)]
        row = {f"a{hour}": value for hour, value in enumerate(values)}
        row.update(Day=11, cs0="02:00", cg0=90, cs1="08:00", cg1=280, cs2="14:00", cg2=80, cs3="20:00", cg3=290)
        payload = {"State": True, "Data": {"Site": {"Code": station_id, "Name": "mock native table", "CoordX": 118.1, "CoordY": 24.5},
                    "Data": {"SiteCode": station_id, "Year": 2026, "Month": 10, "TimeArea": "-0800", "Benchmark": "mock datum"}, "SubData": row}}
        file = f"evidence/{station_id}.json"
        write_json(tmp_path / file, payload)
        manifest["sources"].append({"id": source_id, "kind": "synthetic", "http_status": 200,
                                    "file": file, "sha256": sha256_file(tmp_path / file),
                                    "metadata": {"synthetic": True, "station_id": station_id, "request_day": day,
                                                 "raw_time_area": "-0800", "transport_error": None}})
        manifest["quarantined_tides"].append({"station_id": station_id, "request_day": day, "source_id": source_id,
                                             "unit": "cm", "timezone_verified": False, "raw_time_area": "-0800",
                                             "hourly_local_times": [f"{day}T{h:02d}:00" for h in range(24)], "hourly_values_cm": values})
    return manifest, tmp_path


def test_raw_tide_appendix_is_native_centimetres_deduped_and_bound(native_render_fixture):
    from reportlab.pdfgen.canvas import Canvas
    manifest, root = native_render_fixture
    before = canonical_digest(manifest)
    configure_fonts()
    pdf = root / "raw-table.pdf"
    canvas = Canvas(str(pdf))
    assert _raw_tide_appendix(canvas, manifest, True, 1, run_dir=root) == 3
    canvas.save()
    reader = PdfReader(pdf)
    assert len(reader.pages) == 2
    texts = [page.extract_text() for page in reader.pages]
    for text in texts:
        assert "原件本地时间字段" in text and "时区未核实，未转换" in text
        assert "北京时间 Asia/Shanghai" not in text
        assert "2026-10-11T00:00" in text and "2026-10-11T23:00" in text
        assert "100.0" in text and "123.0" in text and "原始潮高 (cm)" in text
        assert "不转换北京时间，不计算涨潮区间" in text and "-0800" in text
        assert "PARTIAL" in text and "UNAPPROVED_REFERENCE" in text
    assert sum("tide_NT1405_2026-10-11" in text for text in texts) == 1
    csv_path = write_raw_tide_csv(manifest, root / "raw.csv", run_dir=root)
    with csv_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 48 and all(r["timezone_verified"] == "false" for r in rows)
    assert canonical_digest(manifest) == before


def test_acquired_unverified_tide_chart_has_count_source_and_no_absolute_curve(native_render_fixture, monkeypatch):
    import matplotlib.pyplot as plt
    manifest, root = native_render_fixture
    configure_fonts()
    captured = []
    original_close = plt.close
    def capture(fig):
        if hasattr(fig, "axes"):
            captured.append(fig)
        original_close(fig)
    monkeypatch.setattr(plt, "close", capture)
    tz = ZoneInfo("Asia/Shanghai")
    render_day_chart(manifest, manifest["sites"][0], {}, datetime(2026, 10, 11, tzinfo=tz),
                     datetime(2026, 10, 11, 23, tzinfo=tz), root / "chart.png", True, run_dir=root)
    tide = captured[-1].axes[0]
    text = "\n".join(t.get_text() for t in tide.texts)
    assert "原始逐时潮位已取得（24 个值）" in text
    assert "时区定义未核实，暂不按北京时间绘制" in text
    assert "tide_T122_2026-10-11" in tide.get_title(loc="left")
    assert not any(line.get_color() == BLUE for line in tide.lines)
    assert len(tide.get_yticks()) == 0


def test_official_acquisition_label_requires_nonempty_hash_bound_page(native_render_fixture):
    manifest, root = native_render_fixture
    file = root / "evidence" / "official.html"
    file.write_bytes(b"<html><body>Unverified target-date page</body></html>")
    source = {"id": "xiamen_official_forecast_page", "kind": "official", "http_status": 200,
              "file": "evidence/official.html", "sha256": sha256_file(file),
              "metadata": {"request_date": manifest["request_date"], "body_verified": False, "transport_error": None}}
    manifest["sources"].append(source)
    document = {"category": "marine_forecast", "status": "not_obtained", "source_id": None,
                "request_date": manifest["request_date"]}
    before = canonical_digest(manifest)
    shown = official_display(manifest, document, run_dir=root)
    assert "官方页面原件已取得" in shown["display_status"]
    assert "核验未完成" in shown["display_status"] and shown["status"] == "not_obtained"
    assert canonical_digest(manifest) == before
    file.write_bytes(b"")
    source["sha256"] = sha256_file(file)
    assert "原件已取得" not in official_display(manifest, document, run_dir=root)["display_status"]
    assert document["source_id"] is None
    assert before != canonical_digest(manifest)  # Only this test's explicit source edit changed it.
