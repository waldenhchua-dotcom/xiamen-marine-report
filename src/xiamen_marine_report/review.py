"""Detached independent review tied to the exact frozen candidate bytes."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MAP_REVIEW_CHECKLIST, REVIEW_CHECKLIST, parse_time
from .provenance import (
    canonical_digest,
    load_manifest,
    read_json,
    safe_relative_path,
    sha256_file,
    write_json,
)
from .validate import validate_run


def candidate_digest(candidate: dict[str, Any]) -> str:
    return canonical_digest({"manifest_sha256": candidate["manifest_sha256"],
                             "artifacts": sorted(candidate["artifacts"], key=lambda item: item["file"])})


def candidate_requires_maps(manifest: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Frozen map evidence makes map outputs mandatory even if their kinds are edited."""
    return (candidate.get("maps_required") is True
            or any(source.get("kind") in {"map", "cartographic"} for source in manifest.get("sources", [])
                   if isinstance(source, dict))
            or any(artifact.get("kind") in {"map_png", "map_metadata"} or artifact.get("role") == "map"
                   or artifact.get("file") == "map_sources.json"
                   for artifact in candidate.get("artifacts", []) if isinstance(artifact, dict)))


def _verify_map_artifacts(path: Path, manifest: dict[str, Any], candidate: dict[str, Any]) -> None:
    if not candidate_requires_maps(manifest, candidate):
        return
    from PIL import Image

    artifacts = candidate["artifacts"]
    selected_sites = {site["id"] for site in manifest["sites"]}
    maps = [artifact for artifact in artifacts if artifact.get("kind") == "map_png"]
    if len(maps) != len(selected_sites) or {artifact.get("site_id") for artifact in maps} != selected_sites:
        raise ValueError("Map candidate must retain exactly one independent map PNG per selected site")
    comprehensive = [artifact for artifact in artifacts if artifact.get("kind") == "png"]
    if len(comprehensive) != len(selected_sites) or sum(artifact.get("kind") == "pdf" for artifact in artifacts) != 1:
        raise ValueError("Map candidate must retain one combined PDF and every comprehensive PNG")
    metadata = [artifact for artifact in artifacts if artifact.get("kind") == "map_metadata"]
    if len(metadata) != 1:
        raise ValueError("Map candidate requires one hash-bound map metadata artifact")
    if not isinstance(read_json(safe_relative_path(path.parent, metadata[0]["file"])), (dict, list)):
        raise ValueError("Map metadata must be structured JSON")
    sources = {source["id"]: source for source in manifest["sources"]}
    copied = {artifact["file"]: artifact for artifact in artifacts}
    for artifact in maps:
        if artifact.get("role") != "map":
            raise ValueError("Independent map PNG must have the map role")
        source_id = artifact.get("map_source_id")
        if source_id not in sources or sources[source_id].get("kind") not in {"map", "cartographic"}:
            raise ValueError("Independent map PNG requires a frozen map source binding")
        source = sources[source_id]
        source_metadata = source.get("metadata", {})
        if source_metadata.get("crs") != "WGS84" or not source_metadata.get("time_convention") or not source.get("license"):
            raise ValueError("Map source must document WGS84, static timing and license/attribution")
        parse_time(source.get("retrieved_at", ""))
        original_id = source_metadata.get("original_source_id")
        chain = [source]
        if original_id is not None:
            if original_id not in sources or sources[original_id].get("kind") not in {"map", "cartographic"}:
                raise ValueError("Derived map source requires its frozen original map response")
            chain.append(sources[original_id])
        for evidence in chain:
            artifact_copy = copied.get(evidence.get("file"))
            if artifact_copy is None or artifact_copy.get("sha256") != evidence.get("sha256"):
                raise ValueError("Map candidate must include hash-bound original and derived map evidence")
        try:
            with Image.open(safe_relative_path(path.parent, artifact["file"])) as image:
                if image.format != "PNG" or image.width < 1200 or image.height < 600:
                    raise ValueError("Map PNG must be a readable high-resolution PNG")
                image.verify()
        except OSError as exc:
            raise ValueError("Map PNG structure is invalid") from exc


def _candidate_path(run_dir: Path, candidate_path: Path | str | None) -> Path:
    if candidate_path is None:
        pointer = read_json(run_dir / "latest_candidate.json")
        value = pointer.get("file") or pointer.get("path") or pointer.get("candidate")
        return safe_relative_path(run_dir, value)
    supplied = Path(candidate_path)
    if not supplied.is_absolute():
        return safe_relative_path(run_dir, str(candidate_path))
    resolved = supplied.resolve()
    if not resolved.is_relative_to(run_dir.resolve()):
        raise ValueError("Candidate must be contained in its run directory")
    return resolved


def verify_candidate(run_dir: Path | str, candidate_path: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    run_dir = Path(run_dir)
    path = _candidate_path(run_dir, candidate_path)
    candidate = read_json(path)
    if not isinstance(candidate, dict):
        raise ValueError("Candidate must be a JSON object")
    if candidate.get("manifest_sha256") != sha256_file(run_dir / "manifest.json"):
        raise ValueError("Candidate manifest binding changed; old review is invalid")
    artifacts = candidate.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Candidate has no artifacts")
    names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("file") in names:
            raise ValueError("Duplicate or malformed artifact")
        artifact_path = safe_relative_path(path.parent, artifact.get("file", ""))
        names.add(artifact["file"])
        if not artifact_path.is_file() or sha256_file(artifact_path) != artifact.get("sha256"):
            raise ValueError("Candidate artifact changed; old review is invalid")
    if candidate.get("candidate_digest") != candidate_digest(candidate):
        raise ValueError("Candidate digest changed; old review is invalid")
    manifest = load_manifest(run_dir)
    if candidate.get("request_date") != manifest.get("request_date"):
        raise ValueError("Candidate request date does not match frozen evidence")
    _verify_map_artifacts(path, manifest, candidate)
    return path, candidate


def _validate_review_input(review: dict[str, Any], manifest: dict[str, Any], candidate: dict[str, Any],
                           decision: str) -> None:
    reviewer = review.get("reviewer")
    identity = reviewer.get("id") if isinstance(reviewer, dict) else reviewer
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("Review needs independent reviewer identity")
    if identity.strip().casefold() == str(manifest.get("analysis_provider", "none")).casefold():
        raise ValueError("The analysis provider cannot approve its own report")
    checked = parse_time(review.get("checked_at", ""))
    if checked > datetime.now(timezone.utc):
        raise ValueError("Review check time cannot be in the future")
    if candidate.get("created_at") is not None and checked < parse_time(candidate["created_at"]):
        raise ValueError("Review cannot precede creation of its final candidate")
    if review.get("candidate_digest") != candidate_digest(candidate):
        raise ValueError("Review checklist is not bound to this candidate digest")
    checklist = review.get("checklist", {})
    if not isinstance(checklist, dict):
        raise ValueError("Review checklist must be a JSON object")
    if decision == "approve" and any(checklist.get(key) is not True for key in REVIEW_CHECKLIST):
        raise ValueError("Approval requires every independent checklist item checked true")
    if decision == "approve" and candidate_requires_maps(manifest, candidate):
        if any(checklist.get(key) is not True for key in MAP_REVIEW_CHECKLIST):
            raise ValueError("Approval requires independent map images and source attribution review")


def review_candidate(run_dir: Path | str, decision: str, review_file: Path | str,
                     candidate_path: Path | str | None = None) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("Decision must be approve or reject")
    run_dir = Path(run_dir)
    path, candidate = verify_candidate(run_dir, candidate_path)
    review = read_json(review_file)
    if not isinstance(review, dict):
        raise ValueError("Review must be a JSON object")
    manifest = load_manifest(run_dir)
    _validate_review_input(review, manifest, candidate, decision)
    if decision == "approve":
        if candidate.get("validation_state") != "VALIDATED" or validate_run(run_dir)["state"] != "VALIDATED":
            raise ValueError("Incomplete/blocked evidence cannot be approved")
    record = dict(review)
    record.update(decision=decision, state="APPROVED_REPORT" if decision == "approve" else "REJECTED",
                  candidate_id=candidate.get("candidate_id"), candidate_digest=candidate_digest(candidate),
                  manifest_sha256=candidate["manifest_sha256"],
                  synthetic=manifest.get("synthetic", False),
                  recorded_at=datetime.now(timezone.utc).isoformat(),
                  delivery_label="UNAPPROVED_REFERENCE",
                  scope="Independent document review only; never permission to launch. Existing artifacts retain their reference label.")
    # Historical decisions stay attached to their candidate, with a latest detached record.
    write_json(path.parent / "review.json", record)
    write_json(run_dir / "review.json", record)
    return record


def review_status(run_dir: Path | str) -> dict[str, Any]:
    """Recheck bindings every time; edits and a newer candidate invalidate approval."""
    run_dir = Path(run_dir)
    try:
        _, candidate = verify_candidate(run_dir)
        record = read_json(run_dir / "review.json")
        if record.get("decision") not in {"approve", "reject"}:
            raise ValueError("Detached review decision missing")
        expected_state = "APPROVED_REPORT" if record["decision"] == "approve" else "REJECTED"
        if record.get("state") != expected_state:
            raise ValueError("Detached review state is inconsistent")
        _validate_review_input(record, load_manifest(run_dir), candidate, record["decision"])
        if record.get("candidate_id") != candidate.get("candidate_id") or record.get("candidate_digest") != candidate_digest(candidate):
            raise ValueError("Latest candidate differs from reviewed candidate")
        if record.get("state") == "APPROVED_REPORT" and validate_run(run_dir)["state"] != "VALIDATED":
            raise ValueError("Reviewed evidence no longer validates")
        return {**record, "valid": True}
    except (OSError, ValueError, KeyError, TypeError):
        return {"state": "AWAITING_REVIEW", "valid": False,
                "reason": "No valid detached review exists for the current frozen candidate"}
