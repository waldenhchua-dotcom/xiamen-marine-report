"""Frozen OSM maps: local bay detail and regional evidence-point context.

Land tint follows OSM's documented directed coastline rule. Closing lines occur
only at the clipped map frame, never across missing coastline geometry.
"""

import json
import math
from pathlib import Path

import matplotlib.patheffects as effects
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as PlotPath
from shapely import contains_xy, make_valid
from shapely.geometry import Point, box, shape
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

from ..provenance import safe_relative_path
from .charts import VARIABLE_LABELS, direction_vector, series_map
from .style import AMBER, BLUE, INK, MUTED, RED, TEAL, status_label

LOCAL_BOUNDS = {
    "haicang": (118.005, 24.435, 118.10, 24.535),
    "wuyuan": (118.125, 24.500, 118.210, 24.565),
    "huandong": (118.105, 24.550, 118.235, 24.695),
}
SEA, LAND, COAST = "#d6edf4", "#f4f0df", "#689b9b"
HALO = [effects.withStroke(linewidth=3, foreground="white")]


def _valid_point(item: dict, *, station: bool = False) -> bool:
    lat, lon = item.get("latitude"), item.get("longitude")
    if (isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (float, int))
            or not isinstance(lon, (float, int)) or not math.isfinite(lat) or not math.isfinite(lon)
            or not -90 <= lat <= 90 or not -180 <= lon <= 180):
        return False
    crs = item.get("crs") or item.get("coordinate_crs")
    return not station or crs == "WGS84" or (item.get("synthetic") is True and crs in (None, "WGS84"))


def grid_inventory(manifest: dict, site: dict) -> list[dict]:
    return [{"variable": item["variable"], "source_id": item["source_id"], **(item.get("grid") or {})}
            for item in manifest.get("series", []) if item["site_id"] == site["id"] and item["variable"] != "tide_height"]


def load_map_bundle(run_dir: Path, manifest: dict) -> dict:
    """Read the hashed frozen source, without network or mutable app map files."""
    source = next((s for s in manifest.get("sources", []) if s["id"] == "map_osm_geojson"), None)
    if source is None:
        raise ValueError("Run has no frozen OSM map evidence; run a new fetch or demo to include the static basemap")
    geojson = json.loads(safe_relative_path(run_dir, source["file"]).read_text(encoding="utf-8"))
    if geojson.get("type") != "FeatureCollection" or not geojson.get("features"):
        raise ValueError("Frozen OSM map must be a nonempty GeoJSON FeatureCollection")
    metadata = dict(source.get("metadata") or {})
    extra = next((s for s in manifest["sources"] if s["id"] == "map_osm_metadata"), None)
    if extra:
        metadata.update(json.loads(safe_relative_path(run_dir, extra["file"]).read_text(encoding="utf-8")))
    metadata.update(source_id=source["id"], sha256=source["sha256"], retrieved_at=source["retrieved_at"],
                    request=source.get("request"), license=source.get("license"))
    processed = []
    repaired = 0
    for feature in geojson["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = make_valid(geometry)
            repaired += 1
        processed.append((feature.get("properties") or {}, geometry))
    metadata["invalid_polygon_repairs"] = repaired
    return {"geojson": geojson, "metadata": metadata, "processed": processed}


def map_sources_metadata(manifest: dict, bundle: dict) -> dict:
    sources = [s for s in manifest["sources"] if s["id"].startswith("map_osm_")]
    return {
        "geometry_source_id": "map_osm_geojson", "original_source_id": "map_osm_raw",
        "source_ids": [s["id"] for s in sources],
        "sources": [{"id": s["id"], "file": s["file"], "sha256": s["sha256"],
                     "retrieved_at": s["retrieved_at"], "request": s.get("request"), "license": s.get("license")} for s in sources],
        "metadata": bundle["metadata"], "crs": "WGS84", "static_basemap": True,
        "attribution": "© OpenStreetMap contributors | ODbL 1.0 | https://www.openstreetmap.org/copyright",
        "land_fill_method": "Original directed OSM coastline clipped to map frame; land is on its left. Coastline gaps are never connected.",
        "coastline_documentation": "https://wiki.openstreetmap.org/wiki/Tag:natural=coastline",
        "limitations": "静态地理参考，不是航海图、目标日随潮位变化的岸界、实时流场或获许可下水口。未转换 GCJ-02。",
        "overlays_synthetic": bool(manifest.get("synthetic")),
        "site_grids": {site["id"]: [{"grid_id": f"G{index}", "longitude": point[0], "latitude": point[1],
                                      "variables": [r["variable"] for r in rows],
                                      "source_ids": sorted({r["source_id"] for r in rows}),
                                      "distance_km": rows[0].get("distance_km")}
                                     for index, (point, rows) in enumerate(_grid_groups(manifest, site), 1)]
                       for site in manifest["sites"]},
    }


def coastline_land_cells(features: list[dict], bounds: tuple[float, float, float, float], *, processed=None):
    """Infer only consistently bounded left-side land, never bridge source gaps."""
    frame = box(*bounds)
    records = processed if processed is not None else [(f.get("properties") or {}, shape(f["geometry"])) for f in features]
    lines = []
    for props, geometry in records:
        if props.get("layer") != "coastline" or not geometry.intersects(frame):
            continue
        if geometry.geom_type == "LineString":
            lines.append(geometry)
        elif geometry.geom_type == "MultiLineString":
            lines.extend(geometry.geoms)
    clipped = [line.intersection(frame) for line in lines]
    cells = list(polygonize(unary_union([frame.boundary, *clipped])))
    left, right = [], []
    eps = min(bounds[2] - bounds[0], bounds[3] - bounds[1]) * .00003
    for line in lines:
        coords = list(line.coords)
        for index in range(0, len(coords) - 1, max(1, len(coords) // 150)):
            a, b = np.array(coords[index]), np.array(coords[index + 1])
            delta = b - a
            length = float(np.linalg.norm(delta))
            middle = (a + b) / 2
            if not length or not frame.contains(Point(*middle)):
                continue
            normal = np.array([-delta[1], delta[0]]) / length * eps
            left.append(middle + normal)
            right.append(middle - normal)
    if not left:
        return []
    left, right = np.array(left), np.array(right)
    return [cell for cell in cells if contains_xy(cell, left[:, 0], left[:, 1]).any()
            and not contains_xy(cell, right[:, 0], right[:, 1]).any()]


def _polygon_patch(ax, polygon, color, zorder=0):
    polygon = orient(polygon, sign=1)
    vertices, codes = [], []
    for ring in [polygon.exterior, *polygon.interiors]:
        coords = list(ring.coords)
        vertices.extend(coords)
        codes.extend([PlotPath.MOVETO] + [PlotPath.LINETO] * (len(coords) - 2) + [PlotPath.CLOSEPOLY])
    ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor="none", zorder=zorder))


def _line_parts(geometry):
    if geometry.geom_type == "LineString":
        return [list(geometry.coords)]
    if geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        return [list(g.coords) for g in geometry.geoms if g.geom_type == "LineString"]
    return []


def _draw_background(ax, bundle, bounds, *, regional=False):
    frame = box(*bounds)
    ax.set_facecolor(SEA)
    query_bbox = bundle["metadata"].get("query_bbox")
    if isinstance(query_bbox, list) and len(query_bbox) == 4:
        covered = frame.intersection(box(*query_bbox))
        unknown = frame.difference(covered)
        if not unknown.is_empty:
            polygons = [unknown] if unknown.geom_type == "Polygon" else list(unknown.geoms)
            for polygon in polygons:
                _polygon_patch(ax, polygon, "#f1f1f1")
            location = unknown.representative_point()
            ax.text(location.x, location.y, "底图范围外", color=RED, fontsize=8, ha="center", zorder=9)
        frame = covered
    if frame.is_empty:
        return
    drawing_bounds = frame.bounds
    records = bundle.get("processed") or [(f.get("properties") or {}, shape(f["geometry"])) for f in bundle["geojson"]["features"]]
    for polygon in coastline_land_cells([], drawing_bounds, processed=records):
        _polygon_patch(ax, polygon, LAND)
    roads, major, coast, waterways, labels, road_labels, water_polygons = [], [], [], [], [], [], []
    for props, geometry in records:
        if not geometry.intersects(frame):
            continue
        clipped = geometry.intersection(frame)
        layer = props.get("layer")
        if layer == "coastline":
            coast.extend(_line_parts(clipped))
        elif layer == "road":
            is_major = props.get("highway") in {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link"}
            (major if is_major else roads).extend(_line_parts(clipped))
            if not regional and is_major and props.get("name") and geometry.length > (bounds[2] - bounds[0]) * .25:
                road_labels.append((geometry.length, props["name"], clipped.representative_point()))
        elif layer == "waterway":
            waterways.extend(_line_parts(clipped))
        elif layer in ("water", "land"):
            polygons = [clipped] if clipped.geom_type == "Polygon" else list(clipped.geoms) if clipped.geom_type == "MultiPolygon" else []
            for polygon in polygons:
                if layer == "water":
                    water_polygons.append(polygon)
                else:
                    _polygon_patch(ax, polygon, LAND, 1)
            if layer == "water" and props.get("name") and polygons:
                labels.append((2, props["name"], clipped.representative_point()))
        elif layer == "place" and props.get("name"):
            priority = {"city": 0, "town": 1, "suburb": 2, "island": 1, "village": 3}.get(props.get("place"), 3)
            labels.append((priority, props["name"], clipped.representative_point()))
    for polygon in water_polygons:
        _polygon_patch(ax, polygon, SEA, 1.5)
    for segments, color, width, zorder in [(roads, "#d5d6cd", .55 if regional else .8, 2),
                                           (major, "white", 1.5 if regional else 3.2, 3),
                                           (major, "#e3b780", .7 if regional else 1.35, 4),
                                           (waterways, "#99c9dc", .7 if regional else 1., 3),
                                           (coast, COAST, .75 if regional else 1.1, 5)]:
        ax.add_collection(LineCollection(segments, colors=color, linewidths=width, zorder=zorder))
    placed = []
    for _priority, name, point in sorted(labels, key=lambda row: row[0]):
        if len(placed) >= (7 if regional else 14):
            break
        minimum = (bounds[2] - bounds[0]) * (.13 if regional else .085)
        inset_x, inset_y = (bounds[2] - bounds[0]) * .04, (bounds[3] - bounds[1]) * .04
        if not box(bounds[0] + inset_x, bounds[1] + inset_y, bounds[2] - inset_x, bounds[3] - inset_y).contains(point):
            continue
        if any(point.distance(previous) < minimum for previous in placed):
            continue
        ax.text(point.x, point.y, name, fontsize=9 if regional else 11, color="#657768", ha="center",
                va="center", zorder=6, path_effects=HALO, clip_on=True)
        placed.append(point)
    for _length, name, point in sorted(road_labels, reverse=True, key=lambda row: row[0])[:3]:
        inset_x, inset_y = (bounds[2] - bounds[0]) * .06, (bounds[3] - bounds[1]) * .06
        if not box(bounds[0] + inset_x, bounds[1] + inset_y, bounds[2] - inset_x, bounds[3] - inset_y).contains(point):
            continue
        ax.text(point.x, point.y, name, fontsize=8, color="#927246", ha="center", zorder=6, path_effects=HALO, clip_on=True)
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect(1 / math.cos(math.radians((bounds[1] + bounds[3]) / 2)), adjustable="box")
    ax.ticklabel_format(useOffset=False)
    ax.tick_params(labelsize=9)
    ax.grid(color="white", linewidth=.5, alpha=.65)


def _grid_groups(manifest, site):
    groups = {}
    for row in grid_inventory(manifest, site):
        if _valid_point(row):
            key = (float(row["longitude"]), float(row["latitude"]))
            groups.setdefault(key, []).append(row)
    return list(groups.items())


def current_arrow_sample(mapping):
    """A current arrow requires speed and direction at the same verified point."""
    direction, speed = mapping.get("current_direction", {}), mapping.get("current_speed", {})
    grid, speed_grid = direction.get("grid") or {}, speed.get("grid") or {}
    if not _valid_point(grid) or not _valid_point(speed_grid):
        return None, "流速/流向位置未取得，不画方向"
    if (grid.get("verified") is not True or speed_grid.get("verified") is not True
            or grid.get("crs", "WGS84") != "WGS84" or speed_grid.get("crs", "WGS84") != "WGS84"):
        return None, "流速/流向格点或 CRS 未核实，不画方向"
    if (grid["latitude"] != speed_grid["latitude"] or grid["longitude"] != speed_grid["longitude"]):
        return None, "流速与流向来自不同格点，不合成单点方向"
    if direction.get("timestamps") != speed.get("timestamps"):
        return None, "流速与流向时刻不同，不合成单点方向"
    for stamp, bearing, velocity in zip(direction.get("timestamps", []), direction.get("values", []), speed.get("values", []), strict=True):
        vector = direction_vector(bearing, velocity, "toward", .02)
        if vector:
            return {"stamp": stamp, "grid": grid, "vector": vector}, stamp
    return None, "未取得/弱流，不画方向"


def _overlay(ax, manifest, site, groups, *, regional=False):
    target, station = site["target_point"], site["reference_station"]
    synthetic = bool(manifest.get("synthetic"))
    def visible(lon, lat):
        return ax.get_xlim()[0] <= lon <= ax.get_xlim()[1] and ax.get_ylim()[0] <= lat <= ax.get_ylim()[1]
    def label_side(lon, offset=8):
        left = lon > sum(ax.get_xlim()) / 2
        return (-offset if left else offset), ("right" if left else "left")
    if visible(target["longitude"], target["latitude"]):
        ax.scatter(target["longitude"], target["latitude"], marker="*", s=200 if regional else 290,
                   color=RED, edgecolor="white", linewidth=1, zorder=12)
        target_offset, target_align = label_side(target["longitude"], 9)
        ax.annotate(site["name"] + ("测试规划点" if synthetic else "约规划点"),
                    (target["longitude"], target["latitude"]), xytext=(target_offset, -17), textcoords="offset points",
                    color=RED, fontsize=10 if regional else 13, zorder=13, path_effects=HALO,
                    clip_on=True, annotation_clip=True, ha=target_align)
    if _valid_point(station, station=True) and visible(station["longitude"], station["latitude"]):
        ax.scatter(station["longitude"], station["latitude"], marker="s", s=65 if regional else 100,
                   color=AMBER, edgecolor="white", linewidth=1, zorder=10)
        station_offset, station_align = label_side(station["longitude"])
        station_caption = ("合成测试站" if synthetic else "参考潮位站") if regional else ("测试站 " if synthetic else "参考站 ") + station["id"]
        ax.annotate(station_caption,
                    (station["longitude"], station["latitude"]), xytext=(station_offset, 8), textcoords="offset points",
                    color="#9c6929", fontsize=8 if regional else 10, zorder=11, path_effects=HALO,
                    clip_on=True, annotation_clip=True, ha=station_align)
    for index, (point, rows) in enumerate(groups, 1):
        if not visible(*point):
            continue
        marine = any(r["variable"] in {"wave_height", "swell_height", "swell_period", "current_speed", "current_direction", "sea_temperature"} for r in rows)
        ax.scatter(*point, marker="o" if marine else "^", color=TEAL if marine else BLUE,
                   s=55 if regional else 105, edgecolor="white", linewidth=1, zorder=10)
        grid_offset, grid_align = label_side(point[0], 6)
        ax.annotate(f"G{index}", point, xytext=(grid_offset, 5), textcoords="offset points", fontsize=10, zorder=11, color=INK,
                    path_effects=HALO, clip_on=True, annotation_clip=True, ha=grid_align)
    sample, note = current_arrow_sample(series_map(manifest, site["id"]))
    if sample and visible(sample["grid"]["longitude"], sample["grid"]["latitude"]):
        grid, vector = sample["grid"], sample["vector"]
        ax.quiver([grid["longitude"]], [grid["latitude"]], [vector[0]], [vector[1]], angles="uv",
                  scale_units="inches", scale=3, color=TEAL, width=.008, zorder=12)
    return note


def render_map(manifest: dict, site: dict, destination: Path, bundle: dict, *, partial=False) -> Path:
    if not _valid_point(site["target_point"]):
        raise ValueError("Cannot map an invalid target point; finite WGS84 coordinates are required")
    fig = plt.figure(figsize=(16, 12))
    local_bounds = LOCAL_BOUNDS[site["id"]]
    groups = _grid_groups(manifest, site)
    points = [(site["target_point"]["longitude"], site["target_point"]["latitude"]), *(key for key, _ in groups)]
    station = site["reference_station"]
    if _valid_point(station, station=True):
        points.append((station["longitude"], station["latitude"]))
    xy = np.array(points)
    regional_bounds = (min(117.97, float(xy[:, 0].min()) - .025), min(24.41, float(xy[:, 1].min()) - .025),
                       max(118.32, float(xy[:, 0].max()) + .025), max(24.73, float(xy[:, 1].max()) + .025))
    local = fig.add_axes((.065, .255, .60, .54))
    regional = fig.add_axes((.735, .605, .235, .205))
    _draw_background(local, bundle, local_bounds)
    _draw_background(regional, bundle, regional_bounds, regional=True)
    _overlay(local, manifest, site, groups)
    stamp = _overlay(regional, manifest, site, groups, regional=True)
    regional.add_patch(Rectangle((local_bounds[0], local_bounds[1]), local_bounds[2] - local_bounds[0],
                                local_bounds[3] - local_bounds[1], fill=False, edgecolor=RED, linewidth=1.2, linestyle="--", zorder=8))
    local.set_title("湾区局部 · 静态海岸与道路", loc="left", fontsize=15, pad=12)
    regional.set_title("区域图 · 全部已定位证据点", loc="left", fontsize=12, pad=10)
    local.set_xlabel("经度 WGS84")
    local.set_ylabel("纬度 WGS84")
    fig.text(.055, .977, f"{site['name']} · {manifest['request_date']} 地理位置与证据点", fontsize=23, color=INK, va="top")
    fig.text(.055, .925, status_label(manifest, partial), fontsize=14, weight="bold", color=RED, va="top")
    metadata = bundle["metadata"]
    snapshot = metadata.get("timestamp_osm_base") or metadata.get("snapshot_date") or metadata.get("snapshot_time") or metadata.get("retrieved_at")
    fig.text(.055, .885, f"底图: 真实静态 OpenStreetMap | 数据快照 {snapshot} | WGS84，经纬度未转 GCJ-02", fontsize=11, color=MUTED, va="top")
    description = "站点/格点为合成演示位置，与真实底图分开；不是实际站位或预报格点。" if manifest.get("synthetic") else "红星为约规划点；标记实际来源格点；未经核实的站点 CRS 不叠加。"
    fig.text(.055, .18, description, fontsize=11, color=RED if manifest.get("synthetic") else MUTED, va="top")
    if not _valid_point(station, station=True):
        fig.text(.055, .145, f"{station['id']}: 坐标或来源 CRS 未核实，未在 WGS84 底图虚构定位。", fontsize=10, color=RED, va="top")
    fig.text(.055, .108, f"单点流向: {stamp or '未取得/弱流，不画方向'}；toward 北↑东→；仅该格点，非全湾潮流场。", fontsize=10, color=MUTED, va="top")
    fig.text(.735, .565, "图例与格点偏移", fontsize=12, color=INK, weight="bold", va="top")
    station_caption = ("测试站 " if manifest.get("synthetic") else "参考站 ") + station["id"]
    fig.text(.735, .531, f"★ 约规划点  ■ {station_caption}\n▲ 气象格点   ● 海洋格点\n红框: 左侧湾区；完整变量映射见 map_sources.json", fontsize=9, color=MUTED, linespacing=1.3, va="top")
    y = .465
    row_step = min(.047, .345 / max(len(groups), 1))
    row_font = 9 if len(groups) <= 10 else 8
    for index, (_point, rows) in enumerate(groups, 1):
        names = "、".join(VARIABLE_LABELS.get(r["variable"], r["variable"]) for r in rows)
        if len(names) > 22:
            names = names[:22] + "等"
        distance = rows[0].get("distance_km")
        distance_text = f"{distance:.2f} km" if isinstance(distance, (int, float)) else "未核实"
        fig.text(.735, y, f"G{index}: {names}\n{rows[0]['latitude']:.4f}, {rows[0]['longitude']:.4f} | 距目标 {distance_text}",
                 fontsize=row_font, color=MUTED, linespacing=1.2, va="top")
        y -= row_step
    fig.text(.055, .077, "© OpenStreetMap contributors | ODbL 1.0 | https://www.openstreetmap.org/copyright | 冻结源 map_osm_geojson / map_osm_raw", fontsize=10, va="top")
    fig.text(.055, .047, "海岸为静态地理岸线，不是目标日随潮位变化的岸界。底图不是航海图、实时海况或获许可下水口。", fontsize=10, color=RED, va="top")
    fig.text(.055, .02, "地图不能证明现场可下水。完整坐标、变量映射、单位、源哈希见 manifest.json / map_sources.json。", fontsize=9, color=MUTED, va="top")
    fig.savefig(destination, dpi=200)
    plt.close(fig)
    return destination


def grid_description(row: dict) -> str:
    name = VARIABLE_LABELS.get(row["variable"], row["variable"])
    coords = f"{row['latitude']:.5f}, {row['longitude']:.5f}" if _valid_point(row) else "未取得"
    distance = row.get("distance_km")
    distance_text = f"{distance:.2f} km" if isinstance(distance, (int, float)) else "未核实"
    return (f"{name}: {coords}; 距目标 {distance_text}; 分辨率 {row.get('resolution') or '未核实'}; "
            f"格点核实 {'是' if row.get('verified') else '否'}; 源 {row['source_id']}")
