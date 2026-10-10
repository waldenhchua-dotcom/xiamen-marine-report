"""Hourly source values, with separate wind-from and current-toward conventions."""

import math
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from ..models import EXPECTED_UNITS
from ..sunset import sunset_for_date
from ..tide_status import tide_receipt
from .style import AMBER, BLUE, INK, MUTED, PALE, RED, TEAL, status_label

TZ = ZoneInfo("Asia/Shanghai")
VARIABLE_LABELS = {
    "tide_height": "参考站潮位", "wind_speed": "持续风", "wind_gust": "阵风",
    "wind_direction": "风向", "precipitation": "逐时降雨", "precipitation_probability": "降雨概率",
    "weather_code": "天气代码", "temperature": "气温", "wave_height": "有效波高",
    "swell_height": "涌浪高", "swell_period": "涌浪周期", "current_speed": "表层流速",
    "current_direction": "表层流向", "sea_temperature": "海温",
}
WEATHER_LABELS = {
    0: "晴", 1: "少云", 2: "多云", 3: "阴", 45: "雾", 48: "冰雾",
    51: "毛雨", 53: "毛雨", 55: "毛雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "雪", 73: "雪", 75: "雪", 77: "雪粒", 80: "阵雨", 81: "阵雨",
    82: "强阵雨", 85: "阵雪", 86: "阵雪", 95: "雷暴", 96: "雷暴雹", 99: "雷暴雹",
}


def direction_vector(direction: Any, speed: Any, convention: str, calm_threshold: float = 0.1):
    """Return east/north unit components; calm and unknown have no arrow."""
    if direction is None or speed is None:
        return None
    try:
        direction, speed = float(direction), float(speed)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(direction) or not math.isfinite(speed) or speed <= calm_threshold:
        return None
    if convention not in ("from", "toward"):
        raise ValueError("Direction convention must be 'from' or 'toward'")
    bearing = direction + (180 if convention == "from" else 0)
    return (math.sin(math.radians(bearing)), math.cos(math.radians(bearing)))


def wind_from_name(value: Any) -> str:
    if value is None or not math.isfinite(float(value)):
        return "缺测"
    labels = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
    return labels[int((float(value) + 22.5) % 360 // 45)] + "风"


def series_map(manifest: dict, site_id: str) -> dict[str, dict]:
    result = {}
    site = next((s for s in manifest["sites"] if s["id"] == site_id), {})
    for item in manifest.get("series", []):
        if item["site_id"] != site_id:
            continue
        variable = item["variable"]
        expected_direction = "from" if variable == "wind_direction" else "toward" if variable == "current_direction" else None
        unsupported = (item.get("unit") not in EXPECTED_UNITS.get(variable, {item.get("unit")})
                       or (expected_direction and item.get("direction_convention") != expected_direction)
                       or (variable == "tide_height" and site.get("reference_station", {}).get("timezone_verified") is not True))
        if unsupported:
            result[variable] = {**item, "timestamps": [], "values": [], "render_suppressed": True}
        else:
            result[variable] = item
    return result


def coverage_window(manifest: dict, site: dict, coverage: dict) -> tuple[datetime, datetime]:
    record = coverage.get(site["id"], {})
    if record.get("start") and record.get("end"):
        return (datetime.fromisoformat(record["start"]).astimezone(TZ),
                datetime.fromisoformat(record["end"]).astimezone(TZ))
    start = datetime.combine(datetime.fromisoformat(manifest["request_date"]).date(), time(), TZ)
    # A partial view only shows timestamps actually present on the requested day.
    available = [datetime.fromisoformat(t).astimezone(TZ) for s in series_map(manifest, site["id"]).values()
                 for t in s.get("timestamps", []) if t[:10] == manifest["request_date"]]
    return (min(available, default=start), max(available, default=start + timedelta(hours=23)))


def day_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    windows = []
    current = datetime.combine(start.date(), time(), TZ)
    while current <= end:
        last = current + timedelta(days=1) - timedelta(seconds=1)
        windows.append((max(start, current), min(end, last)))
        current += timedelta(days=1)
    return windows


def hourly_ticks(start: datetime, end: datetime) -> list[datetime]:
    tick = start.replace(minute=0, second=0, microsecond=0)
    if tick < start:
        tick += timedelta(hours=1)
    result = []
    while tick <= end:
        result.append(tick)
        tick += timedelta(hours=1)
    return result


def _samples(series: dict | None, start: datetime, end: datetime):
    if not series:
        return [], []
    pairs = [(datetime.fromisoformat(t).astimezone(TZ), v)
             for t, v in zip(series.get("timestamps", []), series.get("values", []), strict=True)
             if start <= datetime.fromisoformat(t).astimezone(TZ) <= end]
    return [t for t, _ in pairs], [np.nan if v is None else float(v) for _, v in pairs]


def _source_note(mapping: dict, variables: tuple[str, ...]) -> str:
    return " / ".join(f"{VARIABLE_LABELS[v]}: {mapping[v]['source_id']}"
                      + (" (定义未核实，不绘制)" if mapping[v].get("render_suppressed") else "") if v in mapping
                      else f"{VARIABLE_LABELS[v]}: 未取得" for v in variables)


def _plot_line(ax, mapping, variable, start, end, color, label):
    ts, vals = _samples(mapping.get(variable), start, end)
    if ts:
        ax.plot(ts, vals, color=color, linewidth=2, marker=".", markersize=4, label=label)
    else:
        ax.text(.5, .45, f"{label}未取得", transform=ax.transAxes, ha="center", color=RED)
    return ts, vals


def _direction_row(ax, mapping, direction, speed, convention, ticks, start, end):
    ds, dvs = _samples(mapping.get(direction), start, end)
    ss, svs = _samples(mapping.get(speed), start, end)
    speeds = dict(zip(ss, svs, strict=True))
    for stamp, value in zip(ds, dvs, strict=True):
        s = speeds.get(stamp)
        vector = direction_vector(value, s, convention, .2 if speed == "wind_speed" else .02)
        if vector:
            ax.quiver([mdates.date2num(stamp)], [.58], [vector[0]], [vector[1]],
                      angles="uv", scale_units="inches", scale=3, width=.004, color=TEAL)
        caption = (wind_from_name(value) if convention == "from" else f"{value:.0f}°")
        if s is None or not math.isfinite(s) or not math.isfinite(value):
            caption = "缺测"
        elif vector is None:
            caption = "微风" if convention == "from" else "弱流"
        ax.text(stamp, .10, caption, ha="center", va="center", fontsize=8, color=MUTED)
    if not ds:
        ax.text(.5, .45, "方向数据未取得", transform=ax.transAxes, ha="center", color=RED)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_ylabel("风吹去\n北↑东→" if convention == "from" else "流向 toward\n北↑东→", fontsize=10)


def render_day_chart(manifest: dict, site: dict, coverage: dict, start: datetime,
                     end: datetime, destination: Path, partial: bool, *, run_dir=None) -> Path:
    """Nine aligned rows; every available clock hour gets an explicit tick."""
    mapping = series_map(manifest, site["id"])
    fig, axes = plt.subplots(9, 1, figsize=(15, 14), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1.55, .95, 1.45, 1.1, 1.4, 1.25, 1.65, .95]})
    fig.subplots_adjust(left=.095, right=.93, top=.89, bottom=.10, hspace=.50)
    label = status_label(manifest, partial)
    fig.suptitle(f"{site['name']} · {start.date().isoformat()} 逐时证据图", x=.07, ha="left", y=.98, fontsize=22)
    fig.text(.07, .94, label, fontsize=14, color=RED, weight="bold")
    sunset_record = sunset_for_date(site, start.date())
    sunset_time = datetime.fromisoformat(sunset_record["time"])
    fig.text(.07, .925, f"北京时间 Asia/Shanghai (+08:00) | 本页 {start:%H:%M} 至 {end:%H:%M} | "
             f"当地日落约 {sunset_time:%H:%M} (Astral 3.2)", fontsize=11)
    ticks = hourly_ticks(start, end)
    tick_labels = [f"{t:%m-%d}\n00" if t.hour == 0 else f"{t:%H}" for t in ticks]
    sunset_in_window = start <= sunset_time <= end
    for ax in axes:
        ax.set_facecolor("white")
        ax.grid(axis="both", color="#dce7ec", linewidth=.65)
        ax.set_xlim(start - timedelta(minutes=20), end + timedelta(minutes=20))
        ax.set_xticks(ticks)
        ax.set_xticklabels(tick_labels, fontsize=9)
        ax.tick_params(axis="x", labelbottom=True, length=3, pad=3)
        if sunset_in_window:
            ax.axvline(sunset_time, color=AMBER, linewidth=1, linestyle="--", alpha=.8)
        for segment in coverage.get(site["id"], {}).get("segments", []):
            low = datetime.fromisoformat(segment.get("start", segment["low"]["time"]))
            high = datetime.fromisoformat(segment.get("end", segment["high"]["time"]))
            if low <= end and high >= start:
                ax.axvspan(max(low, start), min(high, end), color=PALE, alpha=.9, zorder=0)
    tide = axes[0]
    receipt = tide_receipt(manifest, site["id"], start.date().isoformat(), run_dir=run_dir)
    normalized = mapping.get("tide_height") or {}
    normalized_times, normalized_values = _samples(normalized, start, end)
    # A fully validated report may end partway through its final date: that
    # legitimate slice must retain its verified curve even though it is not 24h.
    base_receipt = tide_receipt(manifest, site["id"], run_dir=run_dir)
    plot_verified = receipt["status"] == "verified" or (not partial and base_receipt["status"] == "verified" and normalized_times
                     and not normalized.get("render_suppressed") and site["reference_station"].get("timezone_verified") is True
                     and all(math.isfinite(value) for value in normalized_values))
    if plot_verified:
        _plot_line(tide, mapping, "tide_height", start, end, BLUE, "原始逐时潮位")
        tide.set_ylabel("潮位 (m)")
    else:
        if receipt["status"] == "received_unverified":
            message = (f"原始逐时潮位已取得（{receipt['count']} 个值）\n"
                       "时区定义未核实，暂不按北京时间绘制")
        else:
            message = receipt["label"]
        tide.text(.5, .45, message, transform=tide.transAxes, ha="center", va="center", fontsize=12,
                  color=RED, linespacing=1.6)
        tide.set_yticks([])
        tide.set_ylabel("潮位暂不绘制")
    station = site["reference_station"]
    tide_source_note = (_source_note(mapping, ("tide_height",)) if not receipt.get("source_id") else
                        f"原始潮表源: {receipt['source_id']}"
                        + (f" | 源内 TimeArea={receipt.get('raw_time_area')}；未转换原始 cm 值见 PDF 附录"
                           if receipt["status"] == "received_unverified" else ""))
    tide.set_title(f"参考站 {station['name']} ({station['id']}) | 基准面: {station.get('datum') or '未核实'}\n"
                   + tide_source_note, loc="left", fontsize=10, color=MUTED)
    for item in manifest.get("extrema", []):
        t = datetime.fromisoformat(item["time"])
        if plot_verified and station.get("timezone_verified") is True and item["station_id"] == station["id"] and start <= t <= end:
            height = item["height"]
            tide.scatter(t, height, color=AMBER, zorder=4, s=30)
            high = item["type"] == "high"
            tide.annotate(f"{'高' if high else '低'} {t:%H:%M}\n{height:.2f} m", (t, height),
                          xytext=(0, -34 if high else 10), textcoords="offset points",
                          ha="center", fontsize=9, color=INK)
    tide.margins(y=.3)
    wind = axes[1]
    ws, wv = _samples(mapping.get("wind_speed"), start, end)
    gs, gv = _samples(mapping.get("wind_gust"), start, end)
    gusts = dict(zip(gs, gv, strict=True))
    if ws:
        wind.bar(ws, wv, width=.024, color=BLUE, label="持续风柱高")
        for t, speed in zip(ws, wv, strict=True):
            gust = gusts.get(t, np.nan)
            if math.isfinite(speed) and math.isfinite(gust):
                wind.vlines(t, speed, gust, color=AMBER, linewidth=2)
                wind.plot(t, gust, "_", color=AMBER, markersize=9)
        wind.plot([], [], color=AMBER, label="阵风独立柱顶延伸")
    else:
        wind.text(.5, .5, "风速数据未取得", transform=wind.transAxes, ha="center", color=RED)
    wind.set_ylim(bottom=0)
    wind.set_ylabel("风速 (m/s)")
    wind.legend(loc="upper right", fontsize=8, ncol=2)
    wind.set_title(_source_note(mapping, ("wind_speed", "wind_gust")), loc="left", fontsize=9, color=MUTED)
    _direction_row(axes[2], mapping, "wind_direction", "wind_speed", "from", ticks, start, end)
    axes[2].set_title("箭头为风吹去方向；中文标注气象来向 (from)，微风/缺测不画箭头", loc="left", fontsize=9, color=MUTED)
    for var, color, name in (("wave_height", BLUE, "有效波高"), ("swell_height", TEAL, "涌浪高")):
        _plot_line(axes[3], mapping, var, start, end, color, name)
    axes[3].set_ylabel("波高 (m)")
    axes[3].legend(loc="upper right", ncol=2, fontsize=8)
    axes[3].set_title(_source_note(mapping, ("wave_height", "swell_height")), loc="left", fontsize=9, color=MUTED)
    _plot_line(axes[4], mapping, "swell_period", start, end, TEAL, "涌浪周期")
    axes[4].set_ylabel("周期 (s)")
    axes[4].set_title(_source_note(mapping, ("swell_period",)), loc="left", fontsize=9, color=MUTED)
    ps, pv = _samples(mapping.get("precipitation"), start, end)
    if ps:
        axes[5].bar(ps, pv, width=.027, color=BLUE, label="逐小时 mm")
    else:
        axes[5].text(.5, .4, "降雨数据未取得", transform=axes[5].transAxes, ha="center", color=RED)
    axes[5].set_ylabel("降雨 (mm)")
    axes[5].set_ylim(bottom=0)
    prob = axes[5].twinx()
    _plot_line(prob, mapping, "precipitation_probability", start, end, AMBER, "降雨概率")
    prob.set_ylim(0, 105)
    prob.set_ylabel("概率 (%)", fontsize=9)
    axes[5].set_title(_source_note(mapping, ("precipitation", "precipitation_probability")), loc="left", fontsize=9, color=MUTED)
    for var, color, name in (("temperature", AMBER, "气温"), ("sea_temperature", TEAL, "海温")):
        _plot_line(axes[6], mapping, var, start, end, color, name)
    axes[6].set_ylabel("温度 (°C)")
    axes[6].legend(loc="upper right", ncol=2, fontsize=8)
    axes[6].set_title(_source_note(mapping, ("temperature", "sea_temperature")), loc="left", fontsize=9, color=MUTED)
    cs, cv = _samples(mapping.get("current_speed"), start, end)
    if cs:
        axes[7].plot(cs, cv, color=TEAL, marker=".", linewidth=2)
    else:
        axes[7].text(.5, .5, "表层流速未取得", transform=axes[7].transAxes, ha="center", color=RED)
    axes[7].set_ylabel("流速 (m/s)")
    axes[7].set_ylim(bottom=0)
    cds, cdv = _samples(mapping.get("current_direction"), start, end)
    speeds = dict(zip(cs, cv, strict=True))
    ytop = max([v for v in cv if math.isfinite(v)] or [1]) * 1.35
    axes[7].set_ylim(0, ytop * 1.25)
    for stamp, direction in zip(cds, cdv, strict=True):
        vector = direction_vector(direction, speeds.get(stamp), "toward", .02)
        if vector:
            axes[7].quiver([mdates.date2num(stamp)], [ytop], [vector[0]], [vector[1]],
                           angles="uv", scale_units="inches", scale=4, width=.003, color=TEAL)
    axes[7].set_title(_source_note(mapping, ("current_speed", "current_direction"))
                     + " | 箭头为流去向 toward；北↑东→；弱流/缺测不画", loc="left", fontsize=9, color=MUTED)
    wt, wvalues = _samples(mapping.get("weather_code"), start, end)
    for stamp, value in zip(wt, wvalues, strict=True):
        if math.isfinite(value):
            code = int(value)
            is_thunder = code in (95, 96, 99)
            axes[8].text(stamp, .5, f"{code}\n{WEATHER_LABELS.get(code, '未定义')}", ha="center", va="center",
                         fontsize=9, color=RED if is_thunder else MUTED, weight="bold" if is_thunder else "normal",
                         bbox={"facecolor": "#ffe4df", "edgecolor": "none", "pad": 4} if is_thunder else None)
        else:
            axes[8].text(stamp, .5, "缺测", ha="center", va="center", fontsize=9, color=RED)
    if not wt:
        axes[8].text(.5, .5, "天气代码未取得", transform=axes[8].transAxes, ha="center", color=RED)
    axes[8].set_ylim(0, 1)
    axes[8].set_yticks([])
    axes[8].set_ylabel("天气代码")
    axes[8].set_title(_source_note(mapping, ("weather_code",)) + " | 雷暴代码是模型信号，不等于官方预警", loc="left", fontsize=9, color=MUTED)
    fig.text(.07, .055, "浅色区间: 参考站低潮至紧邻高潮；折线仅连接冻结的原始逐时值，未用极值拟合。橙色虚线: 估算日落。", fontsize=10)
    fig.text(.07, .034, "潮位不是水深，高潮不是憩流；海洋格点不代表湾内实测。地形、地平线和天气未校正日落。此图不能证明现场可下水。", fontsize=10, color=RED)
    fig.text(.07, .015, label + " | analysis_provider=" + str(manifest.get("analysis_provider", "none")), fontsize=10, color=RED)
    # Preserve a safe inter-panel split for the PDF. PNGs keep the full chart;
    # two wide landscape PDF pages keep each hourly label near native size.
    split_fraction = (axes[3].get_position().y0 + axes[4].get_position().y1) / 2
    split_y = round((1 - split_fraction) * fig.get_figheight() * 180)
    fig.savefig(destination, dpi=180, metadata={"marine_pdf_split_y": str(split_y)})
    plt.close(fig)
    return destination
