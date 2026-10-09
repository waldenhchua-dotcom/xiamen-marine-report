"""Frozen source storage, canonical hashes and credential-safe provenance."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

SECRET_KEY = re.compile(r"token|secret|password|passwd|api.?key|credential|cookie|authorization|signature", re.I)
WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$", re.I)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path | str, obj: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(target)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON number {value}")))


def safe_relative_path(root: Path | str, value: str) -> Path:
    """Resolve only a contained relative file, rejecting cross-platform traversal."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Evidence/artifact path must be a nonempty relative path")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute()
            or PureWindowsPath(value).drive or ":" in normalized
            or any(part in {"", ".", ".."} or part != part.strip() or part.endswith(".")
                   or WINDOWS_DEVICE.fullmatch(part) for part in parts)):
        raise ValueError("Unsafe evidence/artifact path")
    base = Path(root).resolve()
    candidate = (base / normalized).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("Evidence/artifact path escapes its directory")
    return candidate


def sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"}:
        return "[REDACTED_NON_HTTP_URL]"
    host = parsed.hostname or ""
    private = host.lower() in {"localhost", "localhost.localdomain"} or "." not in host
    try:
        private = private or not ipaddress.ip_address(host).is_global
    except ValueError:
        private = private or host.endswith((".local", ".internal", ".lan"))
    if private:
        return "[REDACTED_PRIVATE_URL]"
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    query = [(key, "[REDACTED]" if SECRET_KEY.search(key) else value)
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), ""))


def sanitize_request(request: dict[str, Any]) -> dict[str, Any]:
    def clean(obj: Any, key: str = "") -> Any:
        if SECRET_KEY.search(key):
            return "[REDACTED]"
        if isinstance(obj, dict):
            return {k: clean(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        if isinstance(obj, str) and key.lower() in {"url", "uri"}:
            return sanitize_url(obj)
        return obj
    return clean(request)


def load_manifest(run_dir: Path | str) -> dict[str, Any]:
    result = read_json(Path(run_dir) / "manifest.json")
    if not isinstance(result, dict):
        raise ValueError("Manifest must be a JSON object")
    return result


def save_run(output_root: Path | str, manifest: dict[str, Any], raw_objects: dict[str, bytes]) -> Path:
    """Always create a new run; never rewrite an earlier evidence package."""
    copied = deepcopy(manifest)
    run_id = f"{copied['request_date']}_{uuid4().hex[:12]}"
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / run_id
    run_dir.mkdir(exist_ok=False)
    (run_dir / "evidence").mkdir()
    copied.update(run_id=run_id, schema_version=1, state="FETCHED")
    copied.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    seen: set[str] = set()
    for index, source in enumerate(copied.get("sources", [])):
        source_id = source.get("id")
        if not isinstance(source_id, str) or source_id in seen:
            raise ValueError("Sources require unique string IDs")
        seen.add(source_id)
        raw = raw_objects.get(source_id)
        if not isinstance(raw, bytes):
            raise ValueError(f"Missing raw bytes for source {source_id}")
        name = f"evidence/{index:04d}.json"
        evidence_path = safe_relative_path(run_dir, name)
        evidence_path.write_bytes(raw)
        source["file"], source["sha256"] = name, sha256_file(evidence_path)
        source["request"] = sanitize_request(source.get("request", {}))
    if set(raw_objects) != seen:
        raise ValueError("Raw objects must exactly match manifest sources")
    write_json(run_dir / "manifest.json", copied)
    return run_dir

