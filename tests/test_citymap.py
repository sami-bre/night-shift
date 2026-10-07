"""City event mapping: each agent event type produces exactly the mapped city events."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift.citymap import CityMapper  # noqa: E402
from nightshift.trace import Run  # noqa: E402


def make_run(tmp_path):
    return Run("test-map", "mock", runs_dir=tmp_path)


def ev(seq, event, payload):
    return {"seq": seq, "elapsed_ms": seq * 100, "run_id": "test-map",
            "event": event, "node": "x", "payload": payload}


def city_names(run):
    return [c["city_event"] for c in run.read_city_events()
            if c.get("type") == "city"]


def feed_records(run):
    return [c for c in run.read_city_events() if c.get("type") == "agent"]


def test_smoke_on_real_failure_only_once(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "run_start", {"mode": "mock"}))
    m.handle(ev(2, "cluster_state", {"ok": True, "pods": {"payments-api": "OOMKilled"}}))
    m.handle(ev(3, "cluster_state", {"ok": True, "pods": {"payments-api": "OOMKilled"}}))
    assert city_names(run).count("smoke_start") == 1
    assert city_names(run)[0] == "agent_wake"


def test_tool_call_mapping(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_get", "args": {}}))
    m.handle(ev(2, "tool_call", {"tool": "kubectl_describe", "args": {}}))
    m.handle(ev(3, "tool_call", {"tool": "kubectl_patch_limits", "args": {}}))
    names = city_names(run)
    assert names == ["townhall_light", "magnifier_ping", "repair_walk"]


def test_logs_tool_result_produces_bubbles_with_real_lines(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_logs", "args": {}}))
    m.handle(ev(2, "tool_result", {"tool": "kubectl_logs", "ok": True,
                                   "result": "[boot] payments-api starting\n"
                                             "[boot] allocating payments cache (64 MB)\n"}))
    bubbles = [c for c in run.read_city_events()
               if c.get("city_event") == "log_bubble"]
    assert [b["data"]["line"] for b in bubbles] == [
        "[boot] payments-api starting", "[boot] allocating payments cache (64 MB)"]


def test_relight_then_dawn_and_resolved(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "cluster_state", {"ok": True, "pods": {"payments-api": "OOMKilled"}}))
    m.handle(ev(2, "cluster_state", {"ok": True, "pods": {"payments-api": "Running"}}))
    m.handle(ev(3, "run_finish", {"answer": "fixed it"}))
    m.finalize(recovered=True, spend_usd=0.0003, duration_s=12.0, tool_calls=5)
    names = city_names(run)
    assert "window_relight" in names
    assert "sky_dawn" in names and "resolved" in names
    resolved = [c for c in run.read_city_events() if c["city_event"] == "resolved"][0]
    assert resolved["data"]["spend_usd"] == 0.0003
    assert resolved["data"]["duration_s"] == 12.0


def test_no_recovery_is_honest(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "cluster_state", {"ok": True, "pods": {"payments-api": "OOMKilled"}}))
    m.handle(ev(2, "run_finish", {"error": "LLM call failed"}))
    m.finalize(recovered=False, spend_usd=0.0, duration_s=5.0, tool_calls=1)
    names = city_names(run)
    assert "resolved_failed" in names
    assert "sky_dawn" not in names and "resolved" not in names


def test_cluster_down_event(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "cluster_state", {"ok": False, "pods": {}, "error": "cluster_unavailable"}))
    assert "cluster_down" in city_names(run)


def test_seq_dedup_no_double_city_events(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    rec = ev(1, "tool_call", {"tool": "kubectl_get", "args": {}})
    m.handle(rec)
    m.handle(rec)  # re-feeding the same trace line must not re-light the town
    assert city_names(run).count("townhall_light") == 1


def test_guard_denied_maps_to_city(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_patch_limits", "args": {"memory_limit": "9999Mi"}}))
    m.handle(ev(2, "guard_denied", {"tool": "kubectl_patch_limits",
                                    "reason": "GUARD DENIED: out of range"}))
    denied = [c for c in run.read_city_events() if c.get("city_event") == "guard_denied"]
    assert denied and "out of range" in denied[0]["data"]["reason"]


def test_tool_feed_entries_track_call_to_result(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_logs",
                                 "args": {"pod": "payments-api-x", "tail": 10}}))
    m.handle(ev(2, "tool_result", {"tool": "kubectl_logs", "ok": True,
                                   "result": "[boot] payments-api starting\n"}))
    feed = feed_records(run)
    assert len(feed) == 2
    assert feed[0]["verdict"] == "running" and feed[0]["duration_ms"] is None
    assert feed[1]["verdict"] == "ok"
    assert feed[1]["duration_ms"] == 100  # elapsed 200 - 100
    assert feed[1]["args"] == {"pod": "payments-api-x", "tail": 10}
    assert feed[1]["agent_seq"] == 1  # linked to the tool_call line


def test_tool_feed_verdicts(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_get", "args": {}}))
    m.handle(ev(2, "tool_result", {"tool": "kubectl_get", "ok": False,
                                   "error": "cluster_unavailable: connection refused"}))
    feed = feed_records(run)
    assert feed[-1]["verdict"] == "cluster_unavailable"

    run2 = make_run(tmp_path)
    m2 = CityMapper(run2)
    m2.handle(ev(1, "tool_call", {"tool": "kubectl_patch_limits", "args": {}}))
    m2.handle(ev(2, "tool_result", {"tool": "kubectl_patch_limits", "ok": False,
                                    "error": "kubectl failed: pod not found"}))
    assert feed_records(run2)[-1]["verdict"] == "error"


def test_denied_tool_closes_feed_as_denied(tmp_path):
    run = make_run(tmp_path)
    m = CityMapper(run)
    m.handle(ev(1, "tool_call", {"tool": "kubectl_delete", "args": {}}))
    m.handle(ev(2, "guard_denied", {"tool": "kubectl_delete", "reason": "unknown tool"}))
    feed = feed_records(run)
    assert feed[-1]["verdict"] == "denied"
    assert feed[-1]["duration_ms"] == 100


def test_snapshot_shape():
    """pod_status_snapshot returns app-label keys; works or fails cleanly."""
    from nightshift.citymap import pod_status_snapshot
    snap = pod_status_snapshot()
    assert isinstance(snap, dict) and "ok" in snap
    if snap["ok"]:
        assert all(isinstance(v, str) for v in snap["pods"].values())
