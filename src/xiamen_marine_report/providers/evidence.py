"""Optional bounded, hash-checked import of independently reviewed local evidence."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

from ..provenance import read_json, safe_relative_path, sha256_file
from .common import MAX_BYTES, is_public_evidence_url, redact_request


def load_evidence(evidence_dir: Path, request_date: str, site_ids: list[str]) -> tuple[dict, dict]:
    """Import a bundle, never arbitrary paths or a caller's unchecked `verified`.

    The independent import review records who checked the original source against
    each extracted value. This is a provenance attestation, not report approval.
    The run validator and final independent report review remain mandatory.
    """
    bundle_path = evidence_dir / "bundle.json"
    if bundle_path.stat().st_size > MAX_BYTES:
        raise ValueError("Evidence bundle too large")
    bundle = read_json(bundle_path)
    if not isinstance(bundle, dict) or bundle.get("request_date") != request_date:
        raise ValueError("Evidence bundle request date mismatch")
    if bundle.get("synthetic") is not False:
        raise ValueError("Real fetch cannot mix synthetic or unclassified local evidence")
    review = bundle.get("import_review", {})
    if (not isinstance(review, dict) or not isinstance(review.get("reviewer"), str)
            or not review["reviewer"].strip() or review["reviewer"].lower() in {"none", "self", "automatic"}
            or review.get("original_source_values_checked") is not True):
        raise ValueError("Local extracted evidence requires named independent source-value review")
    checked = datetime.fromisoformat(review.get("checked_at", ""))
    if checked.tzinfo is None:
        raise ValueError("Import review needs an explicit timestamp offset")
    sources = bundle.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise ValueError("Evidence bundle requires source records")
    raw, source_ids = {}, set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Imported source records must be objects")
        source_id = source.get("id")
        if (not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", source_id)
                or source_id in source_ids):
            raise ValueError("Invalid or duplicate imported source ID")
        if source_id.startswith(("weather_", "marine_", "tide_", "xiamen_")):
            raise ValueError("Imported source ID conflicts with an automatic provider namespace; use import_ prefix")
        source_ids.add(source_id)
        target = safe_relative_path(evidence_dir, source.get("file", ""))
        if not target.is_file() or target.stat().st_size > MAX_BYTES:
            raise ValueError("Missing or oversized imported source")
        if source.get("sha256") != sha256_file(target):
            raise ValueError("Imported source hash mismatch")
        request = source.get("request", {})
        if not isinstance(request, dict) or not isinstance(request.get("url"), str):
            raise ValueError("Imported source needs a public request URL")
        if not is_public_evidence_url(request.get("url", "")):
            raise ValueError("Imported evidence requires a public credential-free source URL")
        if redact_request(request["url"], request.get("params", {}))["params"] != request.get("params", {}):
            raise ValueError("Imported evidence request contains credentials")
        if source.get("http_status") != 200 or source.get("kind") not in {"official", "forecast", "astronomical"}:
            raise ValueError("Imported evidence is not a successful supported source type")
        if not source.get("license"):
            raise ValueError("Imported evidence needs licensing/access restrictions")
        retrieved = datetime.fromisoformat(source.get("retrieved_at", ""))
        if retrieved.tzinfo is None:
            raise ValueError("Imported retrieval timestamp lacks offset")
        if not isinstance(source.get("metadata", {}), dict):
            raise ValueError("Imported source metadata must be an object")
        source.setdefault("metadata", {})["import_review"] = copy.deepcopy(review)
        raw[source_id] = target.read_bytes()
    for collection in ("series", "extrema", "official_documents"):
        if not isinstance(bundle.get(collection, []), list):
            raise ValueError("Imported collections must be lists")
        for record in bundle.get(collection, []):
            if not isinstance(record, dict):
                raise ValueError("Imported extracted records must be objects")
            if record.get("source_id") not in source_ids:
                raise ValueError("Imported values reference a missing source")
            if record.get("site_id") is not None and record["site_id"] not in site_ids:
                raise ValueError("Imported evidence belongs to an unselected site")
            if collection == "official_documents" and record.get("request_date") != request_date:
                raise ValueError("Imported official document request date mismatch")
            source = next(row for row in sources if row["id"] == record["source_id"])
            original_id = source.get("metadata", {}).get("original_source_id")
            if original_id not in source_ids or original_id == record["source_id"]:
                raise ValueError("Reviewed normalized extraction requires a separately frozen original source")
            try:
                envelope = json.loads(raw[record["source_id"]])
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise ValueError("Imported normalized extraction must be JSON") from exc
            if not isinstance(envelope, dict) or record not in envelope.get(collection, []):
                raise ValueError("Imported normalized value is not bound to its frozen extraction bytes")
    if not isinstance(bundle.get("stations", []), list):
        raise ValueError("Imported stations must be a list")
    for update in bundle.get("stations", []):
        if not isinstance(update, dict):
            raise ValueError("Imported station records must be objects")
        if update.get("site_id") not in site_ids:
            raise ValueError("Imported station belongs to an unselected site")
        station_source = next((row for row in sources if row["id"] == update.get("source_id")), None)
        if station_source is None or station_source.get("kind") != "official":
            raise ValueError("Imported complete station record needs an official reviewed extraction source_id")
        required = {"id", "name", "latitude", "longitude", "datum", "timezone_verified",
                    "station_relation_verified", "provenance", "limitations", "coordinate_crs"}
        if not required.issubset(update):
            raise ValueError("Imported station must provide a complete provenance record")
        original_id = station_source.get("metadata", {}).get("original_source_id")
        if original_id not in source_ids or original_id == station_source["id"]:
            raise ValueError("Imported station extraction needs a separately frozen original source")
        try:
            envelope = json.loads(raw[station_source["id"]])
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("Imported normalized station extraction must be JSON") from exc
        if not isinstance(envelope, dict) or update not in envelope.get("stations", []):
            raise ValueError("Imported station is not bound to frozen extraction bytes")
        for check, field in (("timezone_verified", "timezone_evidence_source_id"),
                             ("station_relation_verified", "relation_evidence_source_id")):
            if update.get(check) is True:
                source_id = update.get(field)
                source = next((row for row in sources if row["id"] == source_id), None)
                if source is None or source.get("kind") != "official":
                    raise ValueError("Station verification requires reviewed original official evidence")
        if update.get("timezone_verified") is True and update.get("timezone") != "Asia/Shanghai":
            raise ValueError("Only independently verified Asia/Shanghai station times can be imported")
    return bundle, raw
