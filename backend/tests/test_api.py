"""HTTP API tests. CAD runs inline (real geometry); inference is off unless
a test mocks it."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cadquery", reason="CadQuery (CAD kernel) not installed; API tests build real geometry")

from app.main import app  # noqa: E402
from app.schemas.design import DEFAULT_PARAMS  # noqa: E402
from app.services.store import store  # noqa: E402

SID = "test-session-0001"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWCAD_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("FLOWCAD_CAD_ISOLATION", "inline")
    monkeypatch.setenv("DO_INFERENCE_API_KEY", "")
    store.clear()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def generate(client, sid=SID, **overrides):
    return client.post("/api/design/generate", json={"session_id": sid, "params": {**DEFAULT_PARAMS.model_dump(), **overrides}})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cad_engine"]["available"] is True
    assert body["ai"]["configured"] is False


def test_default_design(client):
    body = client.get("/api/design/default").json()
    assert body["params"]["base_length_mm"] == 100
    assert body["limits"]["thickness_mm"] == {"min": 1.0, "max": 25.0}


def test_generate_commits_revision_and_serves_files(client, tmp_path):
    r = generate(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "committed"
    rev = body["revision"]
    assert rev["number"] == 1
    assert {f["format"] for f in rev["files"]} == {"stl", "step"}
    stl = next(f for f in rev["files"] if f["format"] == "stl")
    f = client.get(stl["url"])
    assert f.status_code == 200 and len(f.content) == stl["size_bytes"]
    assert f.headers["content-type"] == "model/stl"
    d = client.get(stl["url"] + "?download=1")
    assert "attachment" in d.headers["content-disposition"]


def test_invalid_generate_rejected_with_engineering_message(client):
    r = generate(client, hole_diameter_mm=16.0)
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "rejected"
    assert "does not leave enough material at the plate edge" in body["message"]
    assert body["revision"] is None


def test_zero_dimension_rejected(client):
    r = generate(client, thickness_mm=0)
    assert r.status_code == 422
    assert "greater than 0 mm" in r.json()["message"]


def test_edit_from_prompt_keeps_unspecified_values(client):
    assert generate(client, base_width_mm=60.0).status_code == 200
    r = client.post("/api/design/edit", json={"session_id": SID, "text": "Change the thickness to 6 mm."})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "committed"
    assert body["revision"]["number"] == 2
    assert body["revision"]["params"]["thickness_mm"] == 6
    assert body["revision"]["params"]["base_width_mm"] == 60  # preserved from revision 1
    assert body["interpretation"]["source"] == "rules"
    assert body["interpretation"]["changed_fields"] == ["thickness_mm"]


def test_failed_edit_preserves_last_valid_design(client):
    first = generate(client).json()["revision"]
    r = client.post("/api/design/edit", json={"session_id": SID, "text": "Make the holes 30 mm wide."})
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "rejected"
    assert body["revision"]["number"] == 1
    assert body["revision"]["design_id"] == first["design_id"]
    assert "revision 1" in body["message"]
    # The previous model is still downloadable.
    assert client.get(first["files"][0]["url"]).status_code == 200
    # History records the rejected attempt.
    assert [h["status"] for h in body["history"]] == ["committed", "rejected"]
    # The next valid edit builds on revision 1, not on the rejected attempt.
    nxt = client.post("/api/design/edit", json={"session_id": SID, "text": "Change the thickness to 6 mm."}).json()
    assert nxt["revision"]["number"] == 2 and nxt["revision"]["params"]["hole_diameter_mm"] == 8


def test_ambiguous_edit_asks_for_clarification(client):
    generate(client)
    r = client.post("/api/design/edit", json={"session_id": SID, "text": "make it bigger"})
    assert r.status_code == 200
    assert r.json()["status"] == "clarify"
    assert r.json()["revision"]["number"] == 1


def test_first_prompt_without_revision_uses_defaults(client):
    r = client.post("/api/design/edit", json={"session_id": SID, "text": "120 mm long bracket, 6 mm thick"})
    assert r.status_code == 200
    p = r.json()["revision"]["params"]
    assert p["base_length_mm"] == 120 and p["base_width_mm"] == 50


def test_edit_uses_gemma_when_configured(client, monkeypatch):
    monkeypatch.setenv("DO_INFERENCE_API_KEY", "k")
    reply = {"status": "ok", "changes": {"base_length_mm": 130}, "message": ""}

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    real = httpx.Client
    monkeypatch.setattr("app.services.gemma.httpx.Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    body = client.post("/api/design/edit", json={"session_id": SID, "text": "make the base 130 mm"}).json()
    assert body["interpretation"]["source"] == "gemma"
    assert body["revision"]["params"]["base_length_mm"] == 130


def test_unavailable_inference_falls_back(client, monkeypatch):
    monkeypatch.setenv("DO_INFERENCE_API_KEY", "k")

    def handler(request):
        raise httpx.ConnectError("no route")

    real = httpx.Client
    monkeypatch.setattr("app.services.gemma.httpx.Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    body = client.post("/api/design/edit", json={"session_id": SID, "text": "Change the thickness to 6 mm."}).json()
    assert body["status"] == "committed"
    assert body["interpretation"]["source"] == "rules"
    assert body["interpretation"]["ai_error"].startswith("unavailable")


def test_cad_export_failure_reported_and_previous_kept(client, monkeypatch):
    generate(client)
    from app.services import cad_engine

    def broken(shape, fmt, path):
        raise OSError("disk full")

    monkeypatch.setattr(cad_engine, "_export_one", broken)
    r = generate(client, base_length_mm=110.0)
    assert r.status_code == 422
    assert "STL export failed" in r.json()["message"]
    assert r.json()["revision"]["number"] == 1


@pytest.mark.parametrize("name", [
    "../README.md", "..%2F..%2Fetc%2Fpasswd", "abc.stl", "0" * 32 + ".py",
    "0" * 32 + ".stl",  # well-formed but does not exist
])
def test_file_endpoint_blocks_traversal_and_unknown(client, name):
    assert client.get(f"/api/files/{name}").status_code == 404


def test_request_validation(client):
    assert client.post("/api/design/edit", json={"session_id": SID, "text": ""}).status_code == 422
    assert client.post("/api/design/edit", json={"session_id": "../x", "text": "hi"}).status_code == 422
    assert client.post("/api/design/generate", json={"session_id": SID, "params": {}, "extra": 1}).status_code == 422


def test_unknown_parameter_rejected(client):
    r = generate(client, rib_count=2)
    assert r.status_code == 422
    assert "Unsupported parameter" in r.json()["message"]


def test_process_isolated_worker_generates(client, monkeypatch):
    """The production path: CAD runs in a separate worker process."""
    from app.services import cad_runner

    monkeypatch.setenv("FLOWCAD_CAD_ISOLATION", "process")
    try:
        r = generate(client)
        assert r.status_code == 200, r.json()
        assert r.json()["revision"]["metadata"]["hole_count"] == 4
    finally:
        cad_runner.shutdown()
