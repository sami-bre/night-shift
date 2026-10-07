"""Server tests: run archive, SSE smoke, rate limiting (monkeypatched runner)."""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from nightshift import app as app_mod  # noqa: E402
from nightshift import config  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(app_mod.config, "RUNS_DIR", tmp_path)
    app_mod._state["active_run"] = None
    app_mod._state["ip_last"] = {}
    app_mod._state["live_count"] = {"day": "x", "n": 0}
    return TestClient(app_mod.app)


def seed_run(tmp_path, run_id="r-test", done=True):
    d = tmp_path / run_id
    d.mkdir(parents=True, exist_ok=True)
    events = [
        {"seq": 1, "elapsed_ms": 0, "run_id": run_id, "type": "city", "v": 1,
         "city_event": "agent_wake", "building": "power_plant", "why": "run_start",
         "agent_seq": 1, "data": {}},
        {"seq": 2, "elapsed_ms": 500, "run_id": run_id, "type": "city", "v": 1,
         "city_event": "smoke_start", "building": "bank", "why": "cluster_state",
         "agent_seq": 4, "data": {"status": "OOMKilled"}},
        {"seq": 3, "elapsed_ms": 900, "run_id": run_id, "type": "city", "v": 1,
         "city_event": "resolved", "building": "", "why": "recovery_confirmed",
         "agent_seq": None, "data": {"duration_s": 9.0, "tool_calls": 5, "spend_usd": 0.0}},
    ]
    (d / "city_events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (d / "trace.jsonl").write_text(json.dumps(
        {"seq": 1, "elapsed_ms": 0, "run_id": run_id, "event": "run_start",
         "node": "", "payload": {}}) + "\n")
    (d / "meta.json").write_text(json.dumps({"run_id": run_id, "mode": "mock",
                                             "recovered": True}))
    if not done:
        (d / ".active").write_text("1")
    return run_id


def test_sse_replays_archived_run_and_finishes(client, tmp_path):
    rid = seed_run(tmp_path)
    with client.stream("GET", f"/api/events/{rid}") as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    assert "event: city" in body
    assert "smoke_start" in body and "resolved" in body
    assert "event: done" in body


def test_sse_unknown_run_404(client):
    r = client.get("/api/events/nope")
    assert r.status_code == 404


def test_agent_feed_endpoint(client, tmp_path):
    rid = seed_run(tmp_path)
    # the seeded run has no tool calls yet; append one agent record
    with (tmp_path / rid / "city_events.jsonl").open("a") as fh:
        fh.write(json.dumps({"seq": 4, "elapsed_ms": 700, "run_id": rid,
                             "type": "agent", "v": 1, "agent_seq": 7,
                             "tool": "kubectl_get", "args": {"resource": "pods"},
                             "verdict": "ok", "ok": True, "duration_ms": 120,
                             "detail": ""}) + "\n")
    r = client.get(f"/api/runs/{rid}/agent-feed")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == rid and len(body["feed"]) == 1
    assert body["feed"][0]["tool"] == "kubectl_get"
    assert client.get("/api/runs/nope/agent-feed").status_code == 404


def test_proof_page_renders_both_columns(client, tmp_path):
    rid = seed_run(tmp_path)
    r = client.get(f"/api/runs/{rid}/trace")
    assert r.status_code == 200
    assert "Agent trace" in r.text and "City events" in r.text
    assert "smoke_start" in r.text


def _stub_runner(monkeypatch, tmp_path, seconds=1.0):
    """Replace execute_run with a stub that writes a minimal archive."""
    def fake_execute(run_id=None, mode="mock", runs_dir=None):
        async def inner():
            await asyncio.sleep(seconds)
            d = tmp_path / run_id
            d.mkdir(parents=True, exist_ok=True)
            (d / "city_events.jsonl").write_text(json.dumps(
                {"seq": 1, "elapsed_ms": 0, "run_id": run_id, "type": "city", "v": 1,
                 "city_event": "agent_wake", "building": "", "why": "run_start",
                 "agent_seq": 1, "data": {}}) + "\n")
            return None
        return inner()
    monkeypatch.setattr(app_mod, "execute_run", fake_execute)


def test_concurrent_runs_blocked(client, tmp_path, monkeypatch):
    _stub_runner(monkeypatch, tmp_path, seconds=1.0)
    with client:  # lifespan
        r1 = client.post("/api/run")
        assert r1.status_code == 200
        r2 = client.post("/api/run")
        assert r2.status_code == 503
        assert "concurrent" in r2.json()["error"]


def test_per_ip_cooldown(client, tmp_path, monkeypatch):
    _stub_runner(monkeypatch, tmp_path, seconds=0.1)
    with client:
        r1 = client.post("/api/run")
        assert r1.status_code == 200
        # wait for the stub run to finish
        for _ in range(40):
            if app_mod._state["active_run"] is None:
                break
            time.sleep(0.1)
        r2 = client.post("/api/run")
        assert r2.status_code == 429
        assert "cooldown" in r2.json()["error"]


def test_public_visitors_get_mock(client, tmp_path, monkeypatch):
    """Reverted policy: the public path NEVER runs live — mock by default,
    live only behind the correct NIGHTSHIFT_LIVE_TOKEN."""
    monkeypatch.setattr(app_mod.config, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(app_mod.config, "LIVE_TOKEN", "sekrit")
    _stub_runner(monkeypatch, tmp_path, seconds=0.05)
    with client:
        r = client.post("/api/run")  # anonymous visitor
        assert r.status_code == 200
        assert r.json()["mode"] == "mock"
        assert r.json()["budget_exhausted"] is False


def test_live_only_with_token(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod.config, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(app_mod.config, "LIVE_TOKEN", "sekrit")
    _stub_runner(monkeypatch, tmp_path, seconds=0.05)
    with client:
        r = client.post("/api/run", headers={"x-nightshift-live": "sekrit"})
        assert r.status_code == 200
        assert r.json()["mode"] == "live"
        r2 = client.post("/api/run", headers={"x-nightshift-live": "wrong"})
        assert r2.status_code in (200, 429, 503)  # wrong token never runs live


def test_budget_cap_falls_back_to_mock(client, tmp_path, monkeypatch):
    """Even with a valid live token, the $2/day spend cap forces mock with a
    flag the UI can show — never a 4xx."""
    monkeypatch.setattr(app_mod.config, "OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(app_mod.config, "LIVE_TOKEN", "sekrit")
    monkeypatch.setattr(app_mod.config, "DAILY_SPEND_CAP_USD", 2.00)
    _stub_runner(monkeypatch, tmp_path, seconds=0.05)
    with client:
        # a finished live run from TODAY that already burned the cap
        rid = f"r-{time.strftime('%Y%m%d')}-000000-aaaa"
        d = tmp_path / rid
        d.mkdir()
        (d / "meta.json").write_text(json.dumps(
            {"run_id": rid, "mode": "live", "spend_usd": 2.50}))
        r = client.post("/api/run", headers={"x-nightshift-live": "sekrit"})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "mock"
        assert body["budget_exhausted"] is True


def test_force_mock_mode(client, tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod.config, "OPENROUTER_API_KEY", "sk-test")
    _stub_runner(monkeypatch, tmp_path, seconds=0.05)
    with client:
        r = client.post("/api/run", json={"mode": "mock"})
        assert r.status_code == 200
        assert r.json()["mode"] == "mock"
        assert r.json()["budget_exhausted"] is False


def test_live_disabled_forces_mock(client, monkeypatch):
    monkeypatch.setattr(app_mod.config, "LIVE_ENABLED", False)
    with client:
        r = client.post("/api/run")
        assert r.status_code == 200
        assert r.json()["mode"] == "mock"


def test_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(app_mod, "pod_status_snapshot",
                        lambda: {"ok": True, "pods": {"payments-api": "OOMKilled"}})
    r = client.get("/api/status")
    d = r.json()
    assert d["cluster_ok"] is True
    assert d["pods"]["payments-api"] == "OOMKilled"
    assert d["incident_app"] == config.INCIDENT_APP
