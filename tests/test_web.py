"""Local UI boundary tests; live providers are never used in CI."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from xiamen_marine_report.web import check_request, make_server


@pytest.fixture
def web_server(tmp_path):
    server = make_server(0, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    server.job_store.executor.shutdown(wait=True)
    thread.join(timeout=5)


def request(server, path, *, headers=None, body=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    req = Request(url, headers=headers or {}, data=body)
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


def test_ui_and_health_are_self_contained(web_server):
    status, headers, body = request(web_server, "/")
    assert status == 200 and "厦门" in body.decode()
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert b"https://" not in body
    status, _, body = request(web_server, "/api/health")
    assert status == 200 and json.loads(body)["timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/%2e%2e/LICENSE", "/api/jobs/" + "a"*32 + "/files/../../LICENSE"])
def test_paths_cannot_read_workspace_files(web_server, path):
    assert request(web_server, path)[0] == 404


def test_dns_rebinding_and_cross_origin_job_creation_rejected(web_server):
    assert request(web_server, "/api/health", headers={"Host": "evil.example"})[0] == 403
    assert request(web_server, "/api/jobs", headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
                   body=b'{}')[0] == 403


@pytest.mark.parametrize("host", ["[", "localhost:invalid", "127.0.0.1:99999"])
def test_malformed_host_fails_closed(web_server, host):
    assert request(web_server, "/api/health", headers={"Host": host})[0] == 403


def test_invalid_job_does_not_start_network_request(web_server):
    assert request(web_server, "/api/jobs", headers={"Content-Type": "application/json"}, body=b'{}')[0] == 400
    assert not web_server.job_store.jobs


def test_single_running_job_is_bounded(web_server, monkeypatch):
    monkeypatch.setattr(web_server.job_store.executor, "submit", lambda *_args: None)
    payload = json.dumps({"mode": "demo", "date": "2026-10-10", "sites": ["haicang"]}).encode()
    status, _, body = request(web_server, "/api/jobs", headers={"Content-Type": "application/json"}, body=payload)
    assert status == 202
    job_id = json.loads(body)["id"]
    status, _, body = request(web_server, f"/api/jobs/{job_id}")
    assert status == 200 and "_paths" not in json.loads(body)
    assert request(web_server, "/api/jobs", headers={"Content-Type": "application/json"}, body=payload)[0] == 409


@pytest.mark.parametrize("changes", [
    {"date": "2026-02-30"}, {"date": "2026-1-1"}, {"mode": "shell"},
    {"sites": []}, {"sites": ["haicang", "haicang"]}, {"sites": ["../"]},
    {"allow_partial": "false"},
])
def test_request_validation(changes):
    value = {"mode": "live", "date": "2026-10-10", "sites": ["haicang"]}
    value.update(changes)
    with pytest.raises(ValueError):
        check_request(value)

