"""Offline checks of real map evidence, topology, licensing and coverage."""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from importlib.resources import files

import pytest

from xiamen_marine_report.providers.maps import (
    MAP_GEOJSON_SOURCE_ID,
    MAP_RAW_SOURCE_ID,
    load_basemap_sources,
    osm_to_geojson,
)


@pytest.fixture(scope="module")
def map_data():
    sources, raw = load_basemap_sources()
    return sources, raw, json.loads(raw[MAP_GEOJSON_SOURCE_ID])


def test_map_metadata_original_and_derived_hashes_are_complete(map_data):
    sources, raw, geo = map_data
    folder = files("xiamen_marine_report").joinpath("assets", "maps")
    metadata = json.loads(folder.joinpath("metadata.json").read_text(encoding="utf-8"))
    assert metadata["http_status"] == 200
    assert metadata["crs"] == "WGS84" and metadata["coordinate_order"] == "longitude,latitude"
    assert metadata["query_bbox"] == [117.8, 24.3, 118.7, 24.85]
    assert metadata["snapshot_date"] == metadata["timestamp_osm_base"][:10]
    assert metadata["license"] == "ODbL-1.0" and "OpenStreetMap" in metadata["attribution"]
    assert metadata["source_url"] == "https://overpass-api.de/api/interpreter"
    assert metadata["retrieved_at"].endswith("+00:00")
    assert "Share alike" in folder.joinpath("ODbL-1.0.txt").read_text(encoding="utf-8")
    compressed = folder.joinpath(metadata["assets"]["raw"]["file"]).read_bytes()
    assert hashlib.sha256(compressed).hexdigest() == metadata["assets"]["raw"]["storage_sha256"]
    assert gzip.decompress(compressed) == raw[MAP_RAW_SOURCE_ID]
    for source, asset in zip(sources, ("raw", "geojson"), strict=True):
        assert source["kind"] == "map" and source["metadata"]["static"] is True
        assert source["metadata"]["time_convention"] and source["metadata"]["crs"] == "WGS84"
        assert source["metadata"]["timestamp_osm_base"] == metadata["timestamp_osm_base"]
        assert source["license"] and source["request"]["params"]["data"] == metadata["query"]
        assert hashlib.sha256(raw[source["id"]]).hexdigest() == metadata["assets"][asset]["sha256"]
    assert sources[1]["metadata"]["original_source_id"] == MAP_RAW_SOURCE_ID
    assert geo["type"] == "FeatureCollection" and geo["metadata"]["license"] == "ODbL-1.0"


def _bounds(feature):
    geometry = feature["geometry"]
    coords = geometry["coordinates"]
    if geometry["type"] == "Point":
        coords = [coords]
    elif geometry["type"] == "Polygon":
        coords = [point for ring in coords for point in ring]
    return min(p[0] for p in coords), min(p[1] for p in coords), max(p[0] for p in coords), max(p[1] for p in coords)


@pytest.mark.parametrize("bbox", [
    (118.015, 24.437, 118.10, 24.53),
    (118.13, 24.505, 118.205, 24.565),
    (118.12, 24.55, 118.23, 24.69),
])
def test_real_coastline_and_roads_cover_each_bay(map_data, bbox):
    _, _, geo = map_data
    present = []
    for feature in geo["features"]:
        west, south, east, north = _bounds(feature)
        if east >= bbox[0] and west <= bbox[2] and north >= bbox[1] and south <= bbox[3]:
            present.append(feature)
    assert sum(feature["properties"]["layer"] == "coastline" for feature in present) >= 10
    assert sum(feature["properties"]["layer"] == "road" for feature in present) >= 20


def test_geojson_is_reproducibly_derived_from_actual_original(map_data):
    _, raw, stored = map_data
    reconstructed = osm_to_geojson(json.loads(raw[MAP_RAW_SOURCE_ID]))
    assert reconstructed == stored
    original = json.loads(raw[MAP_RAW_SOURCE_ID])
    ways = {element["id"]: element for element in original["elements"] if element["type"] == "way"}
    for feature in stored["features"]:
        properties = feature["properties"]
        if properties["layer"] == "coastline":
            way = ways[properties["osm_id"]]
            expected = [[point["lon"], point["lat"]] for point in way["geometry"]]
            assert feature["geometry"]["type"] == "LineString"
            assert feature["geometry"]["coordinates"] == expected
        elif properties["layer"] == "land":
            ring = feature["geometry"]["coordinates"][0]
            assert ring[0] == ring[-1] and len(ring) >= 4
            assert set(properties["osm_way_ids"]).issubset(ways)
            assert "no invented closing edge" in properties["construction"].lower()


def test_public_snapshot_contains_no_credentials_or_private_workspace_paths(map_data):
    sources, raw, _ = map_data
    text = raw[MAP_RAW_SOURCE_ID].decode("utf-8") + json.dumps(sources)
    assert not re.search(r"(?i)(apikey|access_token|authorization|cookie)\s*[=:]", text)
    assert not re.search(r"(?i)[A-Z]:\\+(?:Users|Documents)\\+", text)
    assert not re.search(r"/Users/[^/]+/", text)
    assert not re.search(r"(?i)https?://(?:localhost|[^/]+\.internal)", text)


def test_open_coast_is_not_artificially_closed():
    obj = {"elements": [{"type": "way", "id": 1, "nodes": [1, 2, 3],
                         "geometry": [{"lon": 118.0, "lat": 24.5}, {"lon": 118.1, "lat": 24.5},
                                      {"lon": 118.2, "lat": 24.6}],
                         "tags": {"natural": "coastline"}}]}
    geo = osm_to_geojson(obj)
    assert len(geo["features"]) == 1
    assert geo["features"][0]["geometry"]["type"] == "LineString"
    assert geo["features"][0]["geometry"]["coordinates"][0] != geo["features"][0]["geometry"]["coordinates"][-1]


def test_missing_geometry_is_not_connected_across_a_gap():
    obj = {"elements": [{"type": "way", "id": 1, "nodes": [1, 2, 3],
                         "geometry": [{"lon": 118.0, "lat": 24.5}, None, {"lon": 118.2, "lat": 24.6}],
                         "tags": {"natural": "coastline"}}]}
    with pytest.raises(ValueError, match="missing points"):
        osm_to_geojson(obj)
