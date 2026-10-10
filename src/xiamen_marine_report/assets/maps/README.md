# Static Xiamen OpenStreetMap map data

© OpenStreetMap contributors. The original database extract and the derived
`xiamen_basemap.geojson` are made available under **ODbL-1.0**, separately from
the Apache-2.0 program code. The license text is in `ODbL-1.0.txt`.

- Copyright and source attribution: https://www.openstreetmap.org/copyright
- Database license: https://opendatacommons.org/licenses/odbl/1-0/
- Public source API: https://overpass-api.de/api/interpreter
- Actual OSM database snapshot: **2026-10-10T00:27:51Z**
- Retrieval: **2026-10-10T00:29:10.787071+00:00**
- Query extent: west 117.80, south 24.30, east 118.70, north 24.85.
- Coordinates: WGS84 longitude, latitude; no GCJ-02 conversion.

`metadata.json` records the exact query, versions, URLs, original SHA-256,
compressed-storage SHA-256, GeoJSON SHA-256, feature counts, and processing.
The original response is losslessly gzip-packed to reduce package size.
Runtime checks both the compressed and uncompressed hashes, then freezes the
original uncompressed response into each run. GeoJSON is uncompressed and is
frozen independently, with a link to its original source.

The GeoJSON retains original selected coordinates, names, real coastlines,
water bodies/waterways, and a main-road/bridge skeleton. Closed island land
polygons use exact directed OSM node endpoints; an open coast is never given
an invented closing edge. Full way coordinates can extend outside the query
box and are clipped by the map display only. OSM coastlines approximate mean
high-water springs rather than current tide position or depth.

These are real static geographic data. Synthetic demo forecasts/points are
separately marked; that label does not make this geography synthetic. This map
does not identify permitted launch sites, provide navigation charts, establish
local safety, or depict a measured current field.

Publicly used derivative databases remain available under ODbL, retaining
source notices and attribution. Recipients can obtain the machine-readable
derivative GeoJSON and original extract from this directory. Map PNG/PDF
outputs must show readable `© OpenStreetMap contributors`, the static snapshot
date, and `https://www.openstreetmap.org/copyright` near the map; web previews
should link that attribution. The map data license does not relicense the
independent program code or independent marine forecasts.
