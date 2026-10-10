"""Small same-origin HTML app. Local loopback only; no arbitrary filesystem API."""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .models import parse_date
from .provenance import load_manifest, read_json, write_json
from .tide_status import tide_receipt
from .validate import validate_run

ASSETS = Path(__file__).parent / "web_assets"
SITE_IDS = {"haicang", "wuyuan", "huandong"}


def check_request(value: Any) -> dict:
    if not isinstance(value, dict) or value.get("mode") not in {"live", "demo"}:
        raise ValueError("Choose live or demo")
    requested_date = value.get("date")
    if not isinstance(requested_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested_date):
        raise ValueError("Date must be YYYY-MM-DD")
    parse_date(requested_date)
    sites = value.get("sites")
    if not isinstance(sites, list) or not sites or len(sites) > 3:
        raise ValueError("Select one to three sites")
    if any(not isinstance(site, str) or site not in SITE_IDS for site in sites):
        raise ValueError("Unknown site")
    if len(set(sites)) != len(sites):
        raise ValueError("Duplicate site")
    if type(value.get("allow_partial", False)) is not bool:
        raise ValueError("Partial view choice must be boolean")
    return {"date": requested_date, "sites": sites, "mode": value["mode"],
            "allow_partial": value.get("allow_partial", False)}


class JobStore:
    def __init__(self, output_root: Path):
        self.root = output_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        # Matplotlib is not thread-safe. One bounded worker serializes all report work.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marine-report")

    def create(self, request: dict) -> str:
        with self.lock:
            if any(job["status"] in {"queued", "running"} for job in self.jobs.values()):
                raise RuntimeError("A report is already running")
            if len(self.jobs) >= 100:
                raise RuntimeError("Session limit reached; restart the app to begin a new session")
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {"id": job_id, "status": "queued", "message": "等待取证",
                                 "request": request, "files": [], "_paths": {}}
        self.executor.submit(self._run, job_id, request)
        return job_id

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            self.jobs[job_id].update(values)

    def snapshot(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return {key: value for key, value in job.items() if not key.startswith("_")} if job else None

    def file(self, job_id: str, name: str) -> Path | None:
        with self.lock:
            path = self.jobs.get(job_id, {}).get("_paths", {}).get(name)
        if path and path.is_file() and path.resolve().is_relative_to(self.root):
            return path
        return None

    def _run(self, job_id: str, request: dict) -> None:
        try:
            self._update(job_id, status="running", message="自动获取并冻结 API 响应" if request["mode"] == "live"
                         else "生成显著标记的合成数据")
            if request["mode"] == "demo":
                from .demo import generate_fixture
                from .provenance import save_run
                fixture = generate_fixture(request["date"])
                manifest = fixture["manifest"]
                manifest["sites"] = [site for site in manifest["sites"] if site["id"] in request["sites"]]
                manifest["series"] = [series for series in manifest["series"]
                                       if series["site_id"] in request["sites"]]
                manifest["official_documents"] = [document for document in manifest["official_documents"]
                                                   if document["site_id"] in request["sites"]]
                run = save_run(self.root / "runs", manifest, fixture["raw_objects"])
            else:
                from .fetch import fetch_run
                run = fetch_run(request["date"], request["sites"], self.root / "runs")
            self._update(job_id, message="核验日期、来源、单位和三段跨日覆盖")
            validation = validate_run(run)
            write_json(run / "validation.json", validation)
            manifest = load_manifest(run)
            files: list[dict] = []
            paths: dict[str, Path] = {}
            candidate = None
            if validation["state"] == "VALIDATED" or request["allow_partial"]:
                self._update(job_id, message="绘制 PDF、各水域综合图与地图图片")
                from .render import render_run
                candidate_path = render_run(run, self.root / "reports", allow_partial=request["allow_partial"])
                candidate = read_json(candidate_path)
                for artifact in candidate["artifacts"]:
                    if artifact.get("kind") == "evidence":
                        continue  # Raw objects are downloaded together in the evidence ZIP.
                    path = candidate_path.parent / artifact["file"]
                    name = path.name
                    paths[name] = path
                    files.append({"name": name, "kind": artifact.get("kind"), "size": path.stat().st_size})
                paths["candidate.json"] = candidate_path
                files.append({"name": "candidate.json", "kind": "json", "size": candidate_path.stat().st_size})
            bundle = run / f"{request['date']}_frozen_evidence.zip"
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in [run / "manifest.json", run / "validation.json", *sorted((run / "evidence").rglob("*"))]:
                    if file.is_file() and not file.is_symlink():
                        archive.write(file, file.relative_to(run).as_posix())
            paths[bundle.name] = bundle
            files.append({"name": bundle.name, "kind": "evidence", "size": bundle.stat().st_size})
            status = "complete" if validation["state"] == "VALIDATED" else "blocked"
            self._update(job_id, status=status, message="候选已生成，等待独立审核" if status == "complete"
                         else "关键证据未通过；请查看缺口", validation=validation,
                         run_id=manifest["run_id"], synthetic=manifest["synthetic"],
                         source_count=len(manifest["sources"]), series_count=len(manifest["series"]),
                         tide_receipts=[tide_receipt(manifest, site["id"], run_dir=run) for site in manifest["sites"]],
                         files=files, _paths=paths, candidate_digest=candidate.get("candidate_digest") if candidate else None,
                         sources=[{"id": source["id"], "kind": source.get("kind"),
                                   "http_status": source.get("http_status"), "sha256": source.get("sha256"),
                                   "retrieved_at": source.get("retrieved_at")} for source in manifest["sources"]])
        except Exception as error:
            # Third-party errors can contain credentialed URLs. Only expose their type.
            self._update(job_id, status="failed", message="取证或生成失败；未签发批准报告",
                         error_type=type(error).__name__)


def make_server(port: int, output_root: Path) -> ThreadingHTTPServer:
    store = JobStore(output_root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def _reply(self, code: int, body: bytes, mime: str, *, filename: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                             "img-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; "
                             "base-uri 'none'; frame-ancestors 'self'")
            if filename:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
                self.send_header("Content-Disposition", f'inline; filename="{safe_name}"')
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value: dict) -> None:
            self._reply(code, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _same_host(self) -> bool:
            host = self.headers.get("Host", "")
            try:
                parsed = urlsplit("http://" + host)
                return parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port == self.server.server_port
            except ValueError:
                return False

        def do_GET(self) -> None:
            if not self._same_host():
                self._json(403, {"message": "Local app host required"})
                return
            path = unquote(urlsplit(self.path).path)
            static = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if path in static:
                file = ASSETS / static[path]
                self._reply(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")
                return
            if path == "/api/health":
                self._json(200, {"status": "ready", "timezone": "Asia/Shanghai", "analysis_provider": "none"})
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})(?:/files/([^/]+))?", path)
            if match:
                job_id, filename = match.groups()
                if filename:
                    file = store.file(job_id, filename)
                    if file:
                        self._reply(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                                    filename=file.name)
                        return
                else:
                    snapshot = store.snapshot(job_id)
                    if snapshot:
                        self._json(200, snapshot)
                        return
            self._json(404, {"message": "Not found"})

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if not self._same_host() or (origin and origin != "http://" + self.headers.get("Host")):
                self._json(403, {"message": "Same-origin request required"})
                return
            if self.path != "/api/jobs" or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(400, {"message": "Expected JSON report request"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Request too large")
                request = check_request(json.loads(self.rfile.read(length)))
                job_id = store.create(request)
                self._json(202, {"id": job_id})
            except (ValueError, TypeError) as error:
                self._json(400, {"message": str(error)[:200]})
            except RuntimeError as error:
                self._json(409, {"message": str(error)[:200]})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.job_store = store  # type: ignore[attr-defined]
    return server


def serve(*, port: int = 8765, output_root: Path = Path("runs/app")) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be 1..65535")
    server = make_server(port, output_root)
    print(f"厦门海况 HTML App: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.job_store.executor.shutdown(wait=True)  # type: ignore[attr-defined]

