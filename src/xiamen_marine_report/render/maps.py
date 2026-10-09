"""Honest coordinate schematics: documented points, never an invented flow field."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from .charts import VARIABLE_LABELS, direction_vector, series_map
from .style import AMBER, BLUE, MUTED, RED, TEAL


def _valid_point(item: dict) -> bool:
    return isinstance(item.get("latitude"), (float, int)) and isinstance(item.get("longitude"), (float, int))


def grid_inventory(manifest: dict, site: dict) -> list[dict]:
    """Return each variable's actual grid; grouping never loses variable identity."""
    result = []
    for item in manifest.get("series", []):
        if item["site_id"] != site["id"] or item["variable"] == "tide_height":
            continue
        result.append({"variable": item["variable"], "source_id": item["source_id"], **(item.get("grid") or {})})
    return result


def render_map(manifest: dict, site: dict, destination: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9.5, 5.3))
    fig.subplots_adjust(left=.12, right=.94, top=.85, bottom=.17)
    target = site["target_point"]
    station = site["reference_station"]
    ax.scatter(target["longitude"], target["latitude"], marker="*", s=180, color=RED, label="目标规划点 (约)", zorder=6)
    ax.annotate(site["name"], (target["longitude"], target["latitude"]), xytext=(6, 7), textcoords="offset points", color=RED)
    coordinates = [(target["longitude"], target["latitude"])]
    if _valid_point(station):
        ax.scatter(station["longitude"], station["latitude"], marker="s", s=65, color=AMBER, label="参考潮位站")
        ax.annotate(station["id"], (station["longitude"], station["latitude"]), xytext=(6, -14), textcoords="offset points", color=MUTED)
        coordinates.append((station["longitude"], station["latitude"]))
    else:
        fig.text(.12, .075, f"参考站 {station['id']}: 坐标未核实，未在图中虚构定位。", fontsize=10, color=RED)
    seen = {}
    for row in grid_inventory(manifest, site):
        if not _valid_point(row):
            continue
        key = (float(row["longitude"]), float(row["latitude"]))
        seen.setdefault(key, []).append(row["variable"])
    for index, (key, variables) in enumerate(seen.items(), 1):
        marine = any(v in {"wave_height", "swell_height", "swell_period", "current_speed", "current_direction", "sea_temperature"} for v in variables)
        ax.scatter(*key, marker="o" if marine else "^", color=TEAL if marine else BLUE, s=70, label=f"G{index}: {'海洋' if marine else '气象'}格点")
        ax.annotate(f"G{index}", key, xytext=(7, 5), textcoords="offset points", fontsize=10)
        coordinates.append(key)
    # An optional arrow is attached to exactly the returned current grid at one frozen timestamp.
    mapping = series_map(manifest, site["id"])
    direction = mapping.get("current_direction", {})
    speed = mapping.get("current_speed", {})
    grid = direction.get("grid") or {}
    if _valid_point(grid) and direction.get("timestamps") == speed.get("timestamps"):
        for stamp, bearing, velocity in zip(direction.get("timestamps", []), direction.get("values", []), speed.get("values", []), strict=True):
            vector = direction_vector(bearing, velocity, "toward", .02)
            if vector:
                ax.quiver([grid["longitude"]], [grid["latitude"]], [vector[0]], [vector[1]], angles="uv",
                          scale_units="inches", scale=2, color=TEAL, width=.009)
                fig.text(.12, .045, f"仅格点流向箭头: {stamp}；toward 北↑东→；不是全湾潮流场。", fontsize=9, color=MUTED)
                break
    xy = np.array(coordinates)
    xspan, yspan = max(float(np.ptp(xy[:, 0])), .025), max(float(np.ptp(xy[:, 1])), .025)
    ax.set_xlim(float(xy[:, 0].min()) - .3 * xspan, float(xy[:, 0].max()) + .3 * xspan)
    ax.set_ylim(float(xy[:, 1].min()) - .3 * yspan, float(xy[:, 1].max()) + .3 * yspan)
    ax.set_xlabel("经度 (WGS84，未转 GCJ-02)")
    ax.set_ylabel("纬度 (WGS84)")
    ax.grid(color="#dce7ec", linewidth=.7)
    ax.ticklabel_format(useOffset=False)
    ax.legend(loc="best", fontsize=9)
    ax.set_title(f"{site['name']} · 来源点位示意 (无底图，无下水口授权)", loc="left", fontsize=15)
    fig.savefig(destination, dpi=180)
    plt.close(fig)
    return destination


def grid_description(row: dict) -> str:
    name = VARIABLE_LABELS.get(row["variable"], row["variable"])
    coords = f"{row['latitude']:.5f}, {row['longitude']:.5f}" if _valid_point(row) else "未取得"
    distance = row.get("distance_km")
    distance_text = f"{distance:.2f} km" if isinstance(distance, (int, float)) else "未核实"
    return (f"{name}: {coords}; 距目标 {distance_text}; 分辨率 {row.get('resolution') or '未核实'}; "
            f"格点核实 {'是' if row.get('verified') else '否'}; 源 {row['source_id']}")
