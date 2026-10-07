"""Agent loop tests against MockLLM (no API spend, cluster optional)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import config  # noqa: E402
from nightshift.graph import run_agent  # noqa: E402
from nightshift.llm import make_provider  # noqa: E402
from nightshift.scenario import mock_script, resolve_pod_placeholder  # noqa: E402
from nightshift.trace import Run  # noqa: E402


def run_agent_once(tmp_path, script):
    run = Run("test-agent", "mock", runs_dir=tmp_path)
    run.emit("run_start", task="P1", provider="mock", model="canned-script", mode="mock")
    provider = make_provider("mock", script)
    task = f"P1: {config.INCIDENT_APP} is failing"
    final = asyncio.run(run_agent(task, provider, run, max_steps=config.MAX_STEPS))
    return run, final


def test_happy_path_emits_full_trace(tmp_path):
    script = resolve_pod_placeholder(mock_script())
    run, final = run_agent_once(tmp_path, script)
    events = run.read_events()
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_finish"
    assert "llm_request" in kinds and "llm_response" in kinds
    assert final["done"]
    # the mock performs the canonical diagnosis: get -> logs -> describe -> patch -> get
    tool_calls = [e["payload"]["tool"] for e in events if e["event"] == "tool_call"]
    assert tool_calls == ["kubectl_get", "kubectl_logs", "kubectl_describe",
                          "kubectl_patch_limits", "kubectl_get"]
    # exactly one write tool call, and it carries a valid memory quantity
    patches = [e for e in events if e["event"] == "tool_call"
               and e["payload"]["tool"] == "kubectl_patch_limits"]
    assert len(patches) == 1
    assert patches[0]["payload"]["args"]["memory_limit"] == "256Mi"
    # every tool call has a paired result/denial event
    results = [e for e in events if e["event"] in ("tool_result", "guard_denied")]
    assert len(results) >= len(tool_calls) - 1


def test_max_steps_guard(tmp_path):
    endless = [{"thought": f"step {i}", "action": {"tool": "kubectl_get",
               "args": {"resource": "pods", "namespace": "city"}}} for i in range(50)]
    run, final = run_agent_once(tmp_path, endless)
    events = run.read_events()
    calls = [e for e in events if e["event"] == "tool_call"]
    assert len(calls) <= config.MAX_STEPS
    assert final["done"]
    finishes = [e for e in events if e["event"] == "run_finish"]
    assert finishes and finishes[0]["payload"].get("reason") == "max_steps_reached"


def test_guard_denial_recorded_not_lethal(tmp_path):
    script = [
        {"thought": "try to delete everything", "action": {"tool": "kubectl_patch_limits",
         "args": {"deployment": "payments-api", "container": "payments-api",
                  "namespace": "city", "memory_limit": "9999Mi"}}},
        {"thought": "refused; finish", "finish": "The guard layer refused the patch."},
    ]
    run, final = run_agent_once(tmp_path, script)
    denials = [e for e in run.read_events() if e["event"] == "guard_denied"]
    assert len(denials) == 1
    assert final["done"]


def test_unknown_tool_is_denied(tmp_path):
    script = [
        {"thought": "use a tool I don't have", "action": {"tool": "kubectl_delete",
         "args": {"name": "payments-api"}}},
        {"thought": "no such tool; finish", "finish": "No write access."},
    ]
    run, final = run_agent_once(tmp_path, script)
    denials = [e for e in run.read_events() if e["event"] == "guard_denied"]
    assert len(denials) == 1
    assert "unknown tool" in denials[0]["payload"]["reason"]


def test_mock_script_is_self_consistent():
    """The canned script's tools exist and its arg shapes validate."""
    from nightshift.tools import KUBE_NAME_RE, MEM_RE, TOOLS
    for step in mock_script():
        action = step.get("action")
        if not action:
            continue
        assert action["tool"] in TOOLS
        for k, v in action["args"].items():
            if k == "memory_limit":
                assert MEM_RE.match(v)
            elif isinstance(v, str) and k in ("deployment", "container", "namespace", "pod", "name"):
                assert KUBE_NAME_RE.match(v), (k, v)
