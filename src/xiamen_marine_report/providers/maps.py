"""Offline, hash-checked OpenStreetMap snapshot and auditable GeoJSON conversion.

Map coordinates describe real static geography. A demo's science is synthetic;
that distinction must not be applied to the underlying OSM geography.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from importlib.resources import files

MAP_GEOJSON_SOURCE_ID = "map_osm_geojson"
MAP_RAW_SOURCE_ID = "map_osm_raw"


def osm_to_geojson(obj: dict) -> dict:
    """Convert genuine selected OSM objects; never add a made-up shoreline.

    Closed water ways remain polygons. Open coast ways remain lines. Island land
    polygons are formed only by exact directed OSM endpoint/node-id assembly
    that returns to its starting node, with no new closing segment.
    """
    if obj.get("remark") or not isinstance(obj.get("elements"), list):
        raise ValueError("Incomplete or invalid Overpass snapshot")
    features = []
    coasts = {}
    for element in obj["elements"]:
        tags = element.get("tags", {})
        if element.get("type") == "node":
            if "lat" not in element or "lon" not in element:
                continue
            layer = "place"
            geometry = {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
        elif element.get("type") == "way":
            if ("highway" in tags and tags["highway"] not in {"motorway", "trunk", "primary"}
                    and tags.get("bridge") not in {"yes", "viaduct"}):
                # Static background uses an arterial skeleton, with observed
                # bridge-tagged ways retained to show true water crossings.
                continue
            original_geometry = element.get("geometry", [])
            coords = [[point["lon"], point["lat"]] for point in original_geometry
                      if isinstance(point, dict) and "lon" in point and "lat" in point]
            if len(coords) != len(original_geometry):
                raise ValueError("Incomplete OSM geometry; refusing to connect across missing points")
            if len(coords) < 2:
                continue
            if tags.get("natural") == "coastline":
                layer = "coastline"
                nodes = element.get("nodes", [])
                if len(nodes) == len(coords):
                    coasts[element["id"]] = {"nodes": nodes, "coordinates": coords}
            elif tags.get("natural") == "water":
                layer = "water"
            elif "waterway" in tags:
                layer = "waterway"
            elif "highway" in tags:
                layer = "road"
            else:
                continue
            if layer == "water" and len(coords) >= 4 and coords[0] == coords[-1]:
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}
        else:
            continue
        properties = {"layer": layer, "osm_type": element["type"], "osm_id": element["id"],
                      "name": tags.get("name:zh") or tags.get("name") or tags.get("name:en") or ""}
        for key in ("highway", "bridge", "waterway", "place", "natural", "water", "ref"):
            if key in tags:
                properties[key] = tags[key]
        features.append({"type": "Feature", "id": f"{element['type']}/{element['id']}",
                         "properties": properties, "geometry": geometry})
    starts = defaultdict(list)
    for way_id, segment in coasts.items():
        starts[segment["nodes"][0]].append(way_id)
    visited = set()
    for start_id in sorted(coasts):
        if start_id in visited:
            continue
        first = coasts[start_id]
        chain = [start_id]
        used = {start_id}
        coords = first["coordinates"][:]
        start_node, end_node = first["nodes"][0], first["nodes"][-1]
        while end_node != start_node:
            choices = [way_id for way_id in starts.get(end_node, []) if way_id not in used]
            if len(choices) != 1:
                break
            way_id = choices[0]
            next_segment = coasts[way_id]
            if coords[-1] != next_segment["coordinates"][0]:
                break
            coords.extend(next_segment["coordinates"][1:])
            chain.append(way_id)
            used.add(way_id)
            end_node = next_segment["nodes"][-1]
        # Only complete chains are marked visited: an open chain may have begun
        # in its middle; another start can still identify a complete ring.
        if end_node == start_node and coords[0] == coords[-1] and len(coords) >= 4:
            visited.update(used)
            signed_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(coords, coords[1:], strict=False)) / 2
            # OSM directed coastline has land to the left. Counterclockwise
            # closed rings bound islands; clockwise rings are not island fills.
            if signed_area > 0:
                features.append({"type": "Feature", "id": f"land/{min(chain)}",
                                 "properties": {"layer": "land", "name": "", "osm_type": "assembled_coastline",
                                                "osm_way_ids": sorted(chain),
                                                "construction": "Exact node-id endpoint assembly; no invented closing edge"},
                                 "geometry": {"type": "Polygon", "coordinates": [coords]}})
    if not features or not any(feature["properties"]["layer"] == "coastline" for feature in features):
        raise ValueError("Snapshot has no usable real coastline")
    return {"type": "FeatureCollection", "name": "Xiamen static OpenStreetMap basemap",
            "features": features,
            "metadata": {"crs": "WGS84 / EPSG:4326, longitude-latitude",
                         "source": "OpenStreetMap via Overpass API",
                         "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
                         "copyright_url": "https://www.openstreetmap.org/copyright",
                         "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
                         "timestamp_osm_base": obj.get("osm3s", {}).get("timestamp_osm_base"),
                         "geometry_processing": "Original OSM coordinates retained; motorway/trunk/primary and bridge-tagged road skeleton, selected public names/tags, exact closed island assembly; no simplification or invented coastline",
                         "layer_counts": dict(Counter(feature["properties"]["layer"] for feature in features))}}


def load_basemap_sources() -> tuple[list[dict], dict[str, bytes]]:
    """Load bundled static data offline and verify every bundled source hash."""
    directory = files("xiamen_marine_report").joinpath("assets", "maps")
    metadata = json.loads(directory.joinpath("metadata.json").read_text(encoding="utf-8"))
    sources, raw_objects = [], {}
    for asset, source_id in (("raw", MAP_RAW_SOURCE_ID), ("geojson", MAP_GEOJSON_SOURCE_ID)):
        item = metadata["assets"][asset]
        raw = directory.joinpath(item["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item.get("storage_sha256", item["sha256"]):
            raise ValueError("Bundled OSM basemap hash mismatch")
        if item.get("storage") == "gzip":
            raw = gzip.decompress(raw)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Original OSM basemap bytes hash mismatch")
        source_metadata = {
            "provider": "OpenStreetMap contributors; Overpass API",
            "data_classification": "real static geography, independent of synthetic demo science",
            "time_convention": "Static OSM database snapshot at timestamp_osm_base; not forecast or real-time currents",
            "timestamp_osm_base": metadata["timestamp_osm_base"],
            "snapshot_date": metadata["snapshot_date"], "crs": metadata["crs"],
            "provider_version": metadata["version"],
            "query_bbox": metadata["query_bbox"], "bbox_order": "west,south,east,north",
            "geometry_processing": metadata["geometry_processing"] if asset == "geojson" else "Unmodified original public Overpass response bytes",
            "unit_conventions": "Geography coordinate degrees; not physical forecast series",
            "license_url": metadata["license_url"], "attribution": metadata["attribution"],
            "copyright_url": metadata["copyright_url"],
            "layer_counts": metadata["layer_counts"], "static": True,
            "limitations": metadata["limitations"], "derived": asset == "geojson",
        }
        if asset == "geojson":
            source_metadata["original_source_id"] = MAP_RAW_SOURCE_ID
        sources.append({"id": source_id, "file": "", "sha256": "",
                        "retrieved_at": metadata["retrieved_at"],
                        "request": {"url": metadata["source_url"], "method": "POST",
                                    "params": {"data": metadata["query"]}},
                        "http_status": metadata["http_status"], "kind": "map",
                        "license": "ODbL-1.0. © OpenStreetMap contributors. Map database and derived GeoJSON are separately licensed from Apache-2.0 program code.",
                        "metadata": source_metadata})
        raw_objects[source_id] = raw
    return sources, raw_objects
