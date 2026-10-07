"""Guard tests: the write tool must refuse everything except the vetted limits patch."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nightshift import tools as T  # noqa: E402
from nightshift.tools import GuardError  # noqa: E402

# ---------- memory quantity validation (no cluster needed) ----------

def test_memory_quantity_regex():
    with pytest.raises(GuardError, match="invalid memory"):
        T._mem_to_mb("banana")
    with pytest.raises(GuardError, match="invalid memory"):
        T._mem_to_mb("")
    with pytest.raises(GuardError, match="invalid memory"):
        T._mem_to_mb("256MB")  # only Mi/Gi


def test_memory_range():
    with pytest.raises(GuardError, match="out of the allowed range"):
        T._mem_to_mb("4Mi")       # below 8Mi
    with pytest.raises(GuardError, match="out of the allowed range"):
        T._mem_to_mb("8192Mi")    # above 4096Mi
    with pytest.raises(GuardError, match="out of the allowed range"):
        T._mem_to_mb("16Gi")
    assert T._mem_to_mb("256Mi") == 256
    assert T._mem_to_mb("1Gi") == 1024


def test_name_slots_reject_injection():
    for bad in ("a; rm -rf /", "x y", "$(whoami)", "a`b`", "a|b", "a\nb", "", None):
        with pytest.raises(GuardError, match="invalid"):
            T._slot(bad, "resource name", T.KUBE_NAME_RE)


def test_get_rejects_unknown_kinds():
    with pytest.raises(GuardError, match="not allowed"):
        T.kubectl_get("secrets")
    with pytest.raises(GuardError, match="not allowed"):
        T.kubectl_get("delete")


def test_get_rejects_bad_output():
    with pytest.raises(GuardError, match="output"):
        T.kubectl_get("pods", output="yaml")  # never yaml: it can encode arbitrary data


# ---------- argv construction (monkeypatched execution) ----------

def test_patch_refuses_missing_container(monkeypatch):
    def fake_run(argv, **kw):
        return json.dumps({"spec": {"template": {"spec": {"containers": [
            {"name": "sidecar"}]}}}})
    monkeypatch.setattr(T, "run_kubectl", fake_run)
    with pytest.raises(GuardError, match="not found on deployment"):
        T.kubectl_patch_limits("payments-api", "payments-api", "city", "256Mi")


def test_patch_builds_single_path_json(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        if "get" in argv:
            return json.dumps({"spec": {"template": {"spec": {"containers": [
                {"name": "other"}, {"name": "payments-api"}]}}}})
        seen["argv"] = argv
        return "deployment.apps/payments-api patched"

    monkeypatch.setattr(T, "run_kubectl", fake_run)
    out = T.kubectl_patch_limits("payments-api", "payments-api", "city", "256Mi")
    assert "patched" in out
    argv = seen["argv"]
    # the patch must be type=json and touch EXACTLY the memory limit path of container 1
    p_idx = argv.index("-p")
    patch = json.loads(argv[p_idx + 1])
    assert "--type=json" in argv
    assert len(patch) == 1
    assert patch[0]["op"] == "replace"
    assert patch[0]["path"] == "/spec/template/spec/containers/1/resources/limits/memory"
    assert patch[0]["value"] == "256Mi"
    # no other flag may smuggle in: assert every arg is clean
    assert all(";" not in a and "|" not in a for a in argv)


def test_patch_refuses_out_of_range_value_before_any_call(monkeypatch):
    def boom(*a, **kw):  # pragma: no cover
        raise AssertionError("must not reach kubectl")
    monkeypatch.setattr(T, "run_kubectl", boom)
    with pytest.raises(GuardError, match="out of the allowed range"):
        T.kubectl_patch_limits("payments-api", "payments-api", "city", "4097Mi")


def test_read_tools_carry_context_and_no_shell(monkeypatch):
    seen = {}
    monkeypatch.setattr(T, "run_kubectl",
                        lambda argv, **kw: seen.setdefault("argv", argv) and "x" or "x")
    T.kubectl_get("pods", namespace="city")
    argv = seen["argv"]
    assert argv[0] == "kubectl"
    assert any(a.startswith("--context=") for a in argv)
    assert "sh" not in argv and "bash" not in argv


def test_logs_tail_bounds():
    with pytest.raises(GuardError, match="tail"):
        T.kubectl_logs("web-abc", tail=500)
    with pytest.raises(GuardError, match="tail"):
        T.kubectl_logs("web-abc", tail=0)


def test_cluster_down_is_environmental_not_denial():
    # no such context on CI -> structured environmental error, not a denial
    try:
        T.kubectl_get("pods", namespace="city")
    except GuardError as exc:
        assert exc.deny is False  # cluster absence must NOT read as a guard denial
