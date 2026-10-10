"""Produce a reproducible, explicitly unapproved candidate from frozen evidence."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..provenance import load_manifest, sha256_file, write_json
from ..review import candidate_digest
from ..sunset import sunset_for_date
from ..tide_status import tide_receipt, write_raw_tide_csv
from ..validate import validate_run
from .charts import coverage_window, day_windows, render_day_chart, series_map
from .maps import grid_description, grid_inventory, load_map_bundle, map_sources_metadata, render_map
from .pdf import official_display, render_pdf
from .style import FONT_BOLD_PATH, FONT_PATH, INK, MUTED, RED, configure_fonts, status_label

__all__ = ["render_run"]


def _source_index(manifest: dict, validation: dict, *, run_dir=None) -> str:
    lines = [f"# 厦门三地海况来源索引 - {manifest['request_date']}", "",
             status_label(manifest, validation["state"] != "VALIDATED"), "",
             "所有数值来自本包的冻结原件；manifest.json 保留原始请求、时区、单位、格点和哈希。",
             "候选文件未获独立审核，不能证明现场安全可行。", "",
             "## 数值与原件的对应关系", ""]
    for item in manifest.get("series", []):
        grid = json.dumps(item.get("grid"), ensure_ascii=False, sort_keys=True)
        lines.append(f"- {item['site_id']} / {item['variable']} / {item['unit']}: {item['source_id']}; grid={grid}; direction={item.get('direction_convention') or 'n/a'}")
    lines.extend(["", "## 分钟级潮汐极值与原件的对应关系", ""])
    for item in manifest.get("extrema", []):
        lines.append(f"- {item['station_id']} / {item['time']} / {item['type']} / {item['height']} {item.get('unit', '未核实')}; datum={item.get('datum')}; source={item['source_id']}")
    lines.extend(["", "## 原始潮位接收与时间可用性", ""])
    seen_receipts = set()
    for site in manifest["sites"]:
        receipt = tide_receipt(manifest, site["id"], run_dir=run_dir)
        key = (receipt["station_id"], receipt["request_date"])
        if key in seen_receipts:
            continue
        seen_receipts.add(key)
        lines.append(f"- {receipt['station_id']} / {receipt['request_date']}: {receipt['label']}; source={receipt.get('source_id')}; SHA-256={receipt.get('source_sha256')}")
        if receipt["status"] == "received_unverified":
            lines.append(f"  源内 TimeArea={receipt.get('raw_time_area')}；下列字符串按原件保留，不转换北京时间，不计算涨潮区间。")
            for stamp, value in zip(receipt["native_timestamps"], receipt["heights_cm"], strict=True):
                lines.append(f"  - {stamp}: {value} cm")
    for source in manifest.get("sources", []):
        lines.extend(["", f"## {source['id']}", "",
                      f"- 原件: {source.get('file')}", f"- SHA-256: {source.get('sha256')}",
                      f"- 抓取 UTC: {source.get('retrieved_at')}", f"- HTTP: {source.get('http_status')}",
                      f"- 类型: {source.get('kind')}", f"- 许可/限制: {source.get('license')}",
                      f"- 请求: {json.dumps(source.get('request'), ensure_ascii=False, sort_keys=True)}",
                      f"- 元数据: {json.dumps(source.get('metadata'), ensure_ascii=False, sort_keys=True)}"])
    lines.extend(["", "## 日落算法", "", "Astral 3.2 sunset；WGS84 目标规划点；observer elevation 0 m；北京时间。",
                  "约/估算，未校正地形、实际地平线、天气、气温或气压。",
                  "https://astral.readthedocs.io/en/latest/", "", "## 校验缺口", ""])
    lines.extend(f"- {error}" for error in validation.get("errors", []) + validation.get("warnings", []))
    if not validation.get("errors") and not validation.get("warnings"):
        lines.append("本次确定性校验未发现结构缺口；仍须独立逐页审核及现场核实。")
    return "\n".join(lines) + "\n"


def _wrap_pixels(text: str, font, width: int) -> list[str]:
    lines, current = [], ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
        elif current and font.getlength(current + char) > width:
            lines.append(current)
            current = char
        else:
            current += char
    lines.append(current)
    return lines


def _summary_png(manifest: dict, site: dict, validation: dict, map_path: Path, partial: bool, width=2700, *, run_dir=None):
    title_font = ImageFont.truetype(str(FONT_BOLD_PATH), 70)
    label_font = ImageFont.truetype(str(FONT_BOLD_PATH), 43)
    body_font = ImageFont.truetype(str(FONT_PATH), 34)
    small_font = ImageFont.truetype(str(FONT_PATH), 29)
    margin = 110
    text_width = width - 2 * margin
    start, end = coverage_window(manifest, site, validation.get("coverage", {}))
    coverage = validation.get("coverage", {}).get(site["id"], {})
    sections = [
        (f"{site['name']} · {manifest['request_date']} 综合证据图", title_font, INK),
        (status_label(manifest, partial), label_font, RED),
        (f"北京时间 +08:00 | 状态 {validation['state']} | 覆盖 {start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}", body_font, INK),
        ("未获独立审核；仅供证据复核。图表和预报不能证明现场可下水。", body_font, RED),
    ]
    station = site["reference_station"]
    receipt = tide_receipt(manifest, site["id"], run_dir=run_dir)
    sections.extend([
        (f"参考潮位站 {station['name']} ({station['id']})；基准面 {station.get('datum') or '未核实'}。潮位不是湾内实测或水深；高潮不是憩流。", body_font, INK),
        (f"原始潮位接收状态: {receipt['label']}；源 {receipt.get('source_id') or '未取得有效目标日源'}。", body_font,
         RED if receipt["status"] != "verified" else INK),
        (f"目标点: WGS84 {site['target_point']['latitude']:.5f}, {site['target_point']['longitude']:.5f} (约)；来源 {site['target_point'].get('provenance') or '未核实'}；{site['target_point'].get('limitations') or '规划点不是获授权的下水口'}", small_font, MUTED),
    ])
    if receipt["status"] == "received_unverified":
        sections.append((f"源内 TimeArea={receipt.get('raw_time_area')}；原始本地时间字符串及 cm 值已保留至 PDF 原始潮位附录/CSV。时区定义未核实，不转换北京时间，不计算涨潮区间。", small_font, RED))
    for index, segment in enumerate(coverage.get("segments", []), 1):
        low, high = segment["low"], segment["high"]
        lt, ht = datetime.fromisoformat(low["time"]), datetime.fromisoformat(high["time"])
        st = datetime.fromisoformat(sunset_for_date(site, ht.date())["time"])
        sections.append((f"涨潮段 {index}: {lt:%m-%d %H:%M} 低潮 {low['height']:.2f} m → {ht:%m-%d %H:%M} 高潮 {high['height']:.2f} m；当日日落约 {st:%H:%M}；日落减高潮 {(st - ht).total_seconds()/60:+.0f} 分钟 (非返岸余量建议)。", body_font, INK))
    if not coverage.get("segments"):
        sections.append(("三个完整涨潮段未核实；PARTIAL 仅显示已取得的目标日证据。", body_font, RED))
    for begin, _finish in day_windows(start, end):
        record = sunset_for_date(site, begin.date())
        sections.append((f"{record['date']} 当地日落约 {datetime.fromisoformat(record['time']):%H:%M} 北京时间；Astral 3.2；未校正地形/地平线/天气。", body_font, MUTED))
    mapping = series_map(manifest, site["id"])
    for variable, label in (("wind_speed", "持续风峰值"), ("wind_gust", "阵风峰值")):
        s = mapping.get(variable, {})
        values = [(t, v) for t, v in zip(s.get("timestamps", []), s.get("values", []), strict=True)
                  if v is not None and start <= datetime.fromisoformat(t) <= end]
        value = max(values, key=lambda pair: pair[1]) if values else None
        text = f"{value[1]:.2f} {s['unit']} @ {value[0]}" if value else "未取得"
        sections.append((f"{label}: {text} (分别求峰值)。", body_font, INK))
    rain = mapping.get("precipitation", {})
    values = [v for t, v in zip(rain.get("timestamps", []), rain.get("values", []), strict=True)
              if start <= datetime.fromisoformat(t) <= end]
    cumulative = f"{sum(values):.2f} mm" if values and all(v is not None for v in values) else "缺值，累计量未核实"
    sections.append((f"覆盖内逐时降雨样本之和 {cumulative}；逐小时量见下图。小时累积归属遵循源约定。", body_font, INK))
    sections.append(("位置适用性：每变量实际格点 (纬度, 经度)", label_font, INK))
    for row in grid_inventory(manifest, site):
        sections.append((grid_description(row), small_font, MUTED))
    docs = [d for d in manifest.get("official_documents", []) if d["site_id"] == site["id"]]
    for category, title in (("warning", "官方预警"), ("marine_forecast", "官方海洋预报")):
        found = [d for d in docs if d["category"] == category]
        if not found:
            shown = official_display(manifest, {"category": category, "status": "not_obtained", "request_date": manifest["request_date"]}, run_dir=run_dir)
            sections.append((f"{title}: {shown['display_status']}；{shown['summary']}；页面原件源 {shown.get('source_id') or '未取得'}", body_font, RED))
        for d in found:
            shown = official_display(manifest, d, run_dir=run_dir)
            sections.append((f"{title}: {shown['display_status']}；{shown.get('title') or '正文未取得'}；发布 {shown.get('published_at') or '未核实'}；有效 {shown.get('valid_from') or '未核实'} 至 {shown.get('valid_to') or '未核实'}；{shown.get('summary') or '未确认'}；页面/正文源 {shown.get('source_id') or '未取得'}", small_font, INK if d["status"] == "verified" else RED))
    for error in validation.get("errors", []) + validation.get("warnings", []):
        if not isinstance(error, dict) or error.get("site_id") in (None, site["id"]):
            sections.append((f"校验缺口: {error}", small_font, RED))
    sections.append(("冻结来源与抓取时间 (UTC)；完整参数、单位、有效期和哈希见随附 sources.md/manifest.json", label_font, INK))
    used = {s["source_id"] for s in mapping.values()}
    used.update(d.get("source_id") for d in docs)
    for document in docs:
        used.update(official_display(manifest, document, run_dir=run_dir).get("display_source_ids", []))
    used.update(e.get("source_id") for e in manifest.get("extrema", []) if e["station_id"] == station["id"])
    if receipt.get("source_id"):
        used.add(receipt["source_id"])
    for source in manifest.get("sources", []):
        if source["id"] in used:
            sections.append((f"{source['id']}: {source.get('kind')}；UTC {source.get('retrieved_at')}；{source.get('request', {}).get('url')}；SHA-256 {source.get('sha256')}；许可 {source.get('license')}；元数据 {json.dumps(source.get('metadata'), ensure_ascii=False, sort_keys=True)}", small_font, MUTED))
    wrapped = [(line, font, color) for text, font, color in sections for line in _wrap_pixels(text, font, text_width)]
    height = margin * 2 + sum(int(font.size * 1.7) for _, font, _ in wrapped)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    y = margin
    for line, font, color in wrapped:
        draw.text((margin, y), line, font=font, fill=color)
        y += int(font.size * 1.7)
    map_image = Image.open(map_path).convert("RGB")
    map_height = round((width - 2 * margin) / map_image.width * map_image.height)
    map_image = map_image.resize((width - 2 * margin, map_height), Image.Resampling.LANCZOS)
    with_map = Image.new("RGB", (width, height + map_height + 40), "white")
    with_map.paste(image, (0, 0))
    with_map.paste(map_image, (margin, height))
    return with_map


def _combined_png(summary, charts: list[Path], destination: Path) -> None:
    panels = [summary]
    for path in charts:
        with Image.open(path) as source:
            panel = source.convert("RGB")
            if panel.width != summary.width:
                panel = panel.resize((summary.width, round(panel.height * summary.width / panel.width)), Image.Resampling.LANCZOS)
            panels.append(panel)
    image = Image.new("RGB", (summary.width, sum(panel.height for panel in panels)), "white")
    top = 0
    for panel in panels:
        image.paste(panel, (0, top))
        top += panel.height
    image.save(destination, dpi=(180, 180), optimize=True)


def render_run(run_dir: Path, output_dir: Path, *, allow_partial: bool = False) -> Path:
    """Render fresh candidates; default refuses blocked or damaged source evidence.

    Canonical candidates live inside the frozen run so review pointers never
    escape their evidence root. ``output_dir`` receives an identical delivery
    copy; return the canonical candidate path for detached review.
    """
    run_dir, output_dir = Path(run_dir).resolve(), Path(output_dir).resolve()
    manifest = load_manifest(run_dir)
    validation = validate_run(run_dir)
    partial = validation["state"] != "VALIDATED"
    if partial and not allow_partial:
        raise ValueError(f"Rendering blocked ({validation['state']}); --allow-partial is required for an explicitly limited reference view")
    # Unknown tide timezone must not produce absolute-tide interval conclusions.
    # Keep the frozen manifest intact; only suppress the unsafe display coverage.
    display_coverage = {site_id: record for site_id, record in validation.get("coverage", {}).items()
                        if next(s for s in manifest["sites"] if s["id"] == site_id)["reference_station"].get("timezone_verified") is True}
    validation = {**validation, "coverage": display_coverage}
    # Corrupted evidence cannot be presented as the frozen source package, even in partial mode.
    for source in manifest.get("sources", []):
        evidence = (run_dir / source["file"]).resolve()
        if not evidence.is_relative_to(run_dir) or not evidence.is_file() or sha256_file(evidence) != source["sha256"]:
            raise ValueError("Evidence path or hash invalid; refusing to render corrupted evidence")
    map_bundle = load_map_bundle(run_dir, manifest)
    configure_fonts()
    suffix = "PARTIAL_UNAPPROVED_REFERENCE" if partial else "UNAPPROVED_REFERENCE"
    # Keep the container short for Windows deep checkouts; deliverable filenames
    # below still contain the complete requested date and unapproved/partial label.
    candidate_id = f"c_{uuid.uuid4().hex[:12]}"
    candidate_dir = run_dir / "candidates" / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=False)
    build_dir = candidate_dir / "_build"
    build_dir.mkdir()
    assets = {}
    sunsets = []
    for site in manifest["sites"]:
        map_path = render_map(manifest, site,
                              candidate_dir / f"{manifest['request_date']}_{site['id']}_map_{suffix}.png",
                              map_bundle, partial=partial)
        start, end = coverage_window(manifest, site, validation.get("coverage", {}))
        charts = []
        for begin, finish in day_windows(start, end):
            sunsets.append(sunset_for_date(site, begin.date()))
            charts.append(render_day_chart(manifest, site, validation.get("coverage", {}), begin, finish,
                                          build_dir / f"{site['id']}_{begin.date()}.png", partial, run_dir=run_dir))
        assets[site["id"]] = {"map": map_path, "charts": charts}
        summary = _summary_png(manifest, site, validation, map_path, partial, run_dir=run_dir)
        _combined_png(summary, charts, candidate_dir / f"{manifest['request_date']}_{site['id']}_{suffix}.png")
    render_pdf(manifest, validation, assets, candidate_dir / f"{manifest['request_date']}_xiamen_{suffix}.pdf", partial, run_dir=run_dir)
    shutil.copyfile(run_dir / "manifest.json", candidate_dir / "manifest.json")
    for source in manifest.get("sources", []):
        dest = candidate_dir / source["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_dir / source["file"], dest)
    write_json(candidate_dir / "validation.json", validation)
    write_json(candidate_dir / "sunsets.json", sunsets)
    write_raw_tide_csv(manifest, candidate_dir / f"{manifest['request_date']}_raw_tide_UNVERIFIED_TIMEZONE.csv", run_dir=run_dir)
    write_json(candidate_dir / "map_sources.json", map_sources_metadata(manifest, map_bundle))
    (candidate_dir / "sources.md").write_text(_source_index(manifest, validation, run_dir=run_dir), encoding="utf-8")
    # Known, locally created build directory only; no user paths are recursively removed.
    shutil.rmtree(build_dir)
    map_files = {Path(item["map"]).name: site_id for site_id, item in assets.items()}
    artifacts = []
    for p in sorted(candidate_dir.rglob("*")):
        if not p.is_file():
            continue
        artifact = {"file": p.relative_to(candidate_dir).as_posix(), "sha256": sha256_file(p),
                    "kind": "pdf" if p.suffix == ".pdf" else "png" if p.suffix == ".png" else "evidence" if "evidence" in p.parts else "index"}
        if p.name in map_files:
            artifact.update(kind="map_png", role="map", site_id=map_files[p.name],
                            map_source_id="map_osm_geojson", source_ids=["map_osm_geojson", "map_osm_raw"])
        elif p.name == "map_sources.json":
            artifact.update(kind="map_metadata", role="source_attribution")
        elif artifact["kind"] == "png":
            artifact.update(role="comprehensive")
        artifacts.append(artifact)
    candidate = {
        "candidate_id": candidate_id, "request_date": manifest["request_date"],
        "manifest_sha256": sha256_file(run_dir / "manifest.json"), "synthetic": bool(manifest.get("synthetic")),
        "state": "BLOCKED" if partial else "AWAITING_REVIEW", "validation_state": validation["state"],
        "delivery_label": "UNAPPROVED_REFERENCE", "artifacts": artifacts,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    candidate["candidate_digest"] = candidate_digest(candidate)
    candidate_path = candidate_dir / "candidate.json"
    write_json(candidate_path, candidate)
    write_json(run_dir / "latest_candidate.json", {"file": candidate_path.relative_to(run_dir).as_posix()})
    output_dir.mkdir(parents=True, exist_ok=True)
    delivery_dir = output_dir / candidate_id
    if delivery_dir.resolve() != candidate_dir:
        shutil.copytree(candidate_dir, delivery_dir)
    return candidate_path
