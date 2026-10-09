"""Paginated, source-bound PDF pages; long raster charts are never shrunk to A4."""

import json
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

from ..sunset import sunset_for_date
from .charts import coverage_window, day_windows, series_map
from .maps import grid_description, grid_inventory
from .style import BLUE, FONT_BOLD_NAME, FONT_NAME, INK, MUTED, RED, status_label


def _paragraph(canvas, text: str, x: float, y: float, width: float, size: float = 10,
               color=INK, leading: float | None = None, bold: bool = False) -> float:
    style = ParagraphStyle("marine", fontName=FONT_BOLD_NAME if bold else FONT_NAME, fontSize=size, leading=leading or size * 1.55,
                           textColor=colors.HexColor(color))
    para = Paragraph(escape(text).replace("\n", "<br/>"), style)
    _, height = para.wrap(width, 10000)
    para.drawOn(canvas, x, y - height)
    return y - height


def _page_header(canvas, manifest, partial, title, width, height):
    canvas.setFillColor(colors.HexColor(INK))
    canvas.rect(0, height - 62, width, 62, fill=1, stroke=0)
    canvas.setFont(FONT_NAME, 20)
    canvas.setFillColor(colors.white)
    canvas.drawString(36, height - 33, title)
    canvas.setFont(FONT_NAME, 9)
    canvas.drawString(36, height - 51, f"目标日期 {manifest['request_date']} | 北京时间 Asia/Shanghai (+08:00)")
    _paragraph(canvas, status_label(manifest, partial), 36, height - 77, width - 72, 12, RED, bold=True)


def _footer(canvas, manifest, page_number, width):
    canvas.setStrokeColor(colors.HexColor("#dce7ec"))
    canvas.line(36, 34, width - 36, 34)
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(36, 20, f"UNAPPROVED_REFERENCE | analysis_provider={manifest.get('analysis_provider', 'none')} | 第 {page_number} 页")


def _stat(mapping: dict, variable: str, start, end) -> str:
    series = mapping.get(variable)
    if not series:
        return "未取得"
    values = [(datetime.fromisoformat(t), v) for t, v in zip(series["timestamps"], series["values"], strict=True)
              if v is not None and start <= datetime.fromisoformat(t) <= end]
    if not values:
        return "未取得"
    peak = max(values, key=lambda pair: pair[1])
    return f"{peak[1]:.2f} {series['unit']} @ {peak[0]:%m-%d %H:%M}"


def _overview_pages(canvas: Canvas, manifest, site, validation, map_path, partial, number):
    width, height = A4
    canvas.setPageSize(A4)
    _page_header(canvas, manifest, partial, f"{site['name']} · 三段涨潮证据概览", width, height)
    y = height - 112
    start, end = coverage_window(manifest, site, validation.get("coverage", {}))
    coverage = validation.get("coverage", {}).get(site["id"], {})
    y = _paragraph(canvas, f"校验状态: {validation['state']} | 覆盖 {start:%Y-%m-%d %H:%M} 至 {end:%Y-%m-%d %H:%M}\n"
                   "本候选未获独立审核；仅供取证与复核，不能证明现场安全可行。", 36, y, width - 72, 10, RED) - 12
    canvas.drawImage(str(map_path), 36, y - 230, width=width - 72, height=230, preserveAspectRatio=True, anchor="c")
    y -= 242
    station = site["reference_station"]
    y = _paragraph(canvas, f"参考潮位站: {station['name']} ({station['id']}) | 基准面: {station.get('datum') or '未核实'}\n"
                   "潮位是参考站预报/证据值，不是湾内实测、水深或潮流。高潮不是憩流。", 36, y, width - 72, 10) - 12
    for index, segment in enumerate(coverage.get("segments", []), 1):
        low, high = segment["low"], segment["high"]
        lt, ht = datetime.fromisoformat(low["time"]), datetime.fromisoformat(high["time"])
        estimate = sunset_for_date(site, ht.date())
        st = datetime.fromisoformat(estimate["time"])
        delta = (st - ht).total_seconds() / 60
        y = _paragraph(canvas, f"涨潮段 {index}: {lt:%m-%d %H:%M} 低潮 {low['height']:.2f} m → "
                       f"{ht:%m-%d %H:%M} 高潮 {high['height']:.2f} m\n"
                       f"高潮所在日的日落约 {st:%H:%M}；日落减高潮 {delta:+.0f} 分钟，仅时间差，非返岸余量建议。", 36, y, width - 72, 9) - 6
    if not coverage.get("segments"):
        y = _paragraph(canvas, "未核实三个完整低潮→紧邻高潮涨潮段。此部分版仅显示已取得的目标日证据。", 36, y, width - 72, 10, RED) - 10
    mapping = series_map(manifest, site["id"])
    y = _paragraph(canvas, f"持续风峰值: {_stat(mapping, 'wind_speed', start, end)}\n"
                   f"阵风峰值: {_stat(mapping, 'wind_gust', start, end)}", 36, y, width - 72, 10) - 8
    rain = mapping.get("precipitation", {})
    samples = [v for t, v in zip(rain.get("timestamps", []), rain.get("values", []), strict=True)
               if start <= datetime.fromisoformat(t) <= end]
    amount = f"{sum(v for v in samples if v is not None):.2f} mm" if samples and all(v is not None for v in samples) else "缺值，累计量未核实"
    y = _paragraph(canvas, f"覆盖内逐时样本降雨之和: {amount}；逐小时雨量另见图 (小时累积归属遵循源约定)。", 36, y, width - 72, 9)
    _footer(canvas, manifest, number, width)
    canvas.showPage()
    number += 1
    # Spatial applicability and official evidence get a dedicated readable page.
    _page_header(canvas, manifest, partial, f"{site['name']} · 位置适用性与官方材料", width, height)
    y = height - 115
    point = site["target_point"]
    y = _paragraph(canvas, f"目标 WGS84: {point['latitude']:.5f}, {point['longitude']:.5f} (约)\n"
                   f"坐标来源: {point.get('provenance') or '未核实'}\n"
                   f"限制: {point.get('limitations') or point.get('precision') or '未核实'}", 36, y, width - 72, 10) - 12
    y = _paragraph(canvas, "每变量实际网格 (纬度, 经度)，距离/分辨率必须逐项核实:", 36, y, width - 72, 11, BLUE) - 5
    for row in grid_inventory(manifest, site):
        y = _paragraph(canvas, grid_description(row), 36, y, width - 72, 8) - 4
        if y < 175:
            _footer(canvas, manifest, number, width)
            canvas.showPage()
            number += 1
            _page_header(canvas, manifest, partial, f"{site['name']} · 位置与材料 (续)", width, height)
            y = height - 115
    y -= 8
    documents = [d for d in manifest.get("official_documents", []) if d["site_id"] == site["id"]]
    for category, label in (("warning", "官方预警"), ("marine_forecast", "官方海洋预报")):
        docs = [d for d in documents if d["category"] == category]
        if not docs:
            docs = [{"status": "not_obtained", "title": "未取得目标日正文", "summary": "未确认，不能解释为没有预警。"}]
        for doc in docs:
            text = (f"{label}: {doc['status']} | {doc.get('title') or '未取得'}\n"
                    f"目标日: {doc.get('request_date') or manifest['request_date']}; 发布: {doc.get('published_at') or '未核实'}\n"
                    f"有效: {doc.get('valid_from') or '未核实'} 至 {doc.get('valid_to') or '未核实'}\n"
                    f"{doc.get('summary') or '正文未确认'} | 源: {doc.get('source_id') or '未取得'}")
            needed = 105
            if y < needed + 60:
                _footer(canvas, manifest, number, width)
                canvas.showPage()
                number += 1
                _page_header(canvas, manifest, partial, f"{site['name']} · 官方材料 (续)", width, height)
                y = height - 115
            y = _paragraph(canvas, text, 36, y, width - 72, 9, RED if doc["status"] != "verified" else INK) - 10
    for begin, _finish in day_windows(start, end):
        s = sunset_for_date(site, begin.date())
        y = _paragraph(canvas, f"{s['date']} 当地日落约 {datetime.fromisoformat(s['time']):%H:%M} 北京时间；Astral 3.2；未校正地形/地平线/天气。", 36, y, width - 72, 8) - 5
    _footer(canvas, manifest, number, width)
    canvas.showPage()
    return number + 1


def render_pdf(manifest: dict, validation: dict, site_assets: dict, destination: Path, partial: bool) -> Path:
    canvas = Canvas(str(destination), pagesize=A4, pageCompression=1)
    canvas.setTitle(f"厦门三地海况 {manifest['request_date']} UNAPPROVED_REFERENCE")
    canvas.setAuthor("xiamen-marine-report deterministic renderer; analysis_provider=none")
    page_number = 1
    for site in manifest["sites"]:
        assets = site_assets[site["id"]]
        page_number = _overview_pages(canvas, manifest, site, validation, assets["map"], partial, page_number)
        for chart in assets["charts"]:
            width, height = landscape(A3)
            with Image.open(chart) as source:
                split_y = int(source.info.get("marine_pdf_split_y", round(source.height * .47)))
                slices = [source.crop((0, 0, source.width, split_y)),
                          source.crop((0, split_y, source.width, source.height))]
                for index, panel in enumerate(slices, 1):
                    canvas.setPageSize(landscape(A3))
                    _page_header(canvas, manifest, partial,
                                 f"{site['name']} · {chart.stem.rsplit('_', 1)[-1]} 逐时图 ({index}/2)", width, height)
                    panel_width = width - 56
                    panel_height = panel.height * panel_width / panel.width
                    canvas.drawImage(ImageReader(panel), 28, height - 125 - panel_height,
                                     width=panel_width, height=panel_height)
                    _footer(canvas, manifest, page_number, width)
                    canvas.showPage()
                    page_number += 1
    width, height = A4
    canvas.setPageSize(A4)
    _page_header(canvas, manifest, partial, "来源、取证时间与校验缺口", width, height)
    y = height - 112
    for source in manifest.get("sources", []):
        if y < 185:
            _footer(canvas, manifest, page_number, width)
            canvas.showPage()
            page_number += 1
            _page_header(canvas, manifest, partial, "来源索引 (续)", width, height)
            y = height - 112
        metadata = source.get("metadata") or {}
        metadata_summary = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        if len(metadata_summary) > 650:
            metadata_summary = metadata_summary[:650] + "... 完整元数据见 sources.md / manifest.json"
        text = (f"{source['id']} | {source.get('kind', '未核实')} | UTC 抓取: {source.get('retrieved_at', '未核实')}\n"
                f"原件: {source.get('file', '未取得')} | SHA-256: {source.get('sha256', '未核实')}\n"
                f"URL: {source.get('request', {}).get('url', '未取得')}\n"
                f"授权/限制: {source.get('license', '未核实')}\n"
                f"元数据: {metadata_summary}")
        y = _paragraph(canvas, text, 36, y, width - 72, 8) - 12
    for item in validation.get("errors", []) + validation.get("warnings", []):
        if y < 105:
            _footer(canvas, manifest, page_number, width)
            canvas.showPage()
            page_number += 1
            _page_header(canvas, manifest, partial, "校验缺口 (续)", width, height)
            y = height - 112
        y = _paragraph(canvas, str(item), 36, y, width - 72, 9, RED) - 8
    _footer(canvas, manifest, page_number, width)
    canvas.save()
    return destination
