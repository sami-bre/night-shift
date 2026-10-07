"""Guarded tools for The Night Shift.

Guards live in the tool layer, never in the prompt (same philosophy as
mcp-ops-agent; this is a standalone implementation). Three read-only kubectl
tools and exactly one write tool, `kubectl_patch_limits`, which refuses
everything except a vetted resource-limits patch on a Deployment.

Every tool returns the JSON envelope:
  {"ok": true,  "tool": ..., "result": ...}
  {"ok": false, "tool": ..., "denied": true|false, "error": "..."}
`denied: true` marks a guardrail violation; `denied: false` is an
environmental error (cluster down, kubectl missing, not-found).
"""

from __future__ import annotations

import json
import re
import subprocess

from nightshift import config


class GuardError(Exception):
    """deny=True: guardrail violation. deny=False: environmental error."""

    def __init__(self, reason: str, deny: bool = True):
        self.reason = reason
        self.deny = deny
        super().__init__(reason)


KUBE_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
KUBE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,251}$")
MEM_RE = re.compile(r"^([0-9]{1,5})(Mi|Gi)$")
KUBE_GET_RESOURCES = {
    "pods", "deployments", "services", "nodes", "events", "namespaces",
    "replicasets", "endpoints", "statefulsets", "jobs", "cronjobs",
}
KUBE_DESCRIBE_RESOURCES = KUBE_GET_RESOURCES
KUBE_OUTPUTS = {"wide", "json"}
MIN_MEMORY_MB, MAX_MEMORY_MB = 8, 4096

KUBECTL = "kubectl"


def _slot(value, what: str, regex: re.Pattern) -> str:
    if not isinstance(value, str) or not regex.match(value):
        raise GuardError(f"invalid {what}: {value!r}")
    return value


def _argv(base: list[str]) -> list[str]:
    return [KUBECTL, f"--context={config.KUBE_CONTEXT}", *base]


def _mem_to_mb(limit: str) -> int:
    m = MEM_RE.match(limit or "")
    if not m:
        raise GuardError(f"invalid memory quantity {limit!r}: expected e.g. 256Mi or 1Gi")
    n, unit = int(m.group(1)), m.group(2)
    mb = n if unit == "Mi" else n * 1024
    if not (MIN_MEMORY_MB <= mb <= MAX_MEMORY_MB):
        raise GuardError(
            f"memory {limit} out of the allowed range {MIN_MEMORY_MB}Mi..{MAX_MEMORY_MB}Mi")
    return mb


def run_kubectl(argv: list[str], timeout: float = 20.0,
                max_out: int = 12000) -> str:
    """Run a pre-validated kubectl argv (read-only or the vetted patch)."""
    try:
        proc = subprocess.run(argv, timeout=timeout, capture_output=True,
                              text=True, shell=False)
    except FileNotFoundError as err:
        raise GuardError("kubectl binary not found on this machine", deny=False) from err
    except subprocess.TimeoutExpired as err:
        raise GuardError("kubectl timed out", deny=False) from err
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        low = err.lower()
        if ("connection refused" in low or "no configuration" in low
                or "unable to connect" in low or "context was not found" in low):
            raise GuardError(f"cluster_unavailable: {err[:400]}", deny=False) from None
        raise GuardError(f"kubectl failed: {err[:400]}", deny=False) from None
    out = proc.stdout
    if len(out) > max_out:
        out = out[:max_out] + "\n... [truncated]"
    return out


def _get(resource: str, name: str | None, namespace: str | None,
         output: str | None) -> list[str]:
    """Shared argv builder for kubectl get (keeps JSON untruncated & lean)."""
    extra: list[str] = []
    if output == "json":
        extra = ["--show-managed-fields=false"]
    return extra


# ---------------------------------------------------------------- read tools

def kubectl_get(resource: str, name: str | None = None,
                namespace: str | None = None, output: str | None = None) -> str:
    _slot(resource, "resource kind", KUBE_RESOURCE_RE)
    if resource not in KUBE_GET_RESOURCES:
        raise GuardError(
            f"resource kind '{resource}' not allowed; choose from {sorted(KUBE_GET_RESOURCES)}")
    argv = _argv(["get", resource])
    if name is not None:
        argv.append(_slot(name, "resource name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _slot(namespace, "namespace", KUBE_NAME_RE)]
    if output is not None:
        if output not in KUBE_OUTPUTS:
            raise GuardError(f"output must be one of {sorted(KUBE_OUTPUTS)}")
        argv += ["-o", output]
    argv += _get(resource, name, namespace, output)
    # machine-readable output is used by the server internals too: keep it intact
    return run_kubectl(argv, max_out=100000 if output == "json" else 12000)


def kubectl_describe(resource: str, name: str | None = None,
                     namespace: str | None = None) -> str:
    _slot(resource, "resource kind", KUBE_RESOURCE_RE)
    if resource not in KUBE_DESCRIBE_RESOURCES:
        raise GuardError(
            f"resource kind '{resource}' not allowed; choose from {sorted(KUBE_DESCRIBE_RESOURCES)}")
    argv = _argv(["describe", resource])
    if name is not None:
        argv.append(_slot(name, "resource name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _slot(namespace, "namespace", KUBE_NAME_RE)]
    return run_kubectl(argv)


def kubectl_logs(pod: str, namespace: str | None = None, tail: int | None = None) -> str:
    argv = _argv(["logs"])
    argv.append(_slot(pod, "pod name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _slot(namespace, "namespace", KUBE_NAME_RE)]
    if tail is not None:
        if not isinstance(tail, int) or not (1 <= tail <= 200):
            raise GuardError("tail must be an integer between 1 and 200")
        argv += ["--tail", str(tail)]
    return run_kubectl(argv, timeout=30.0)


# ------------------------------------------------------- the one write tool

def kubectl_patch_limits(deployment: str, container: str, namespace: str,
                         memory_limit: str) -> str:
    """The ONLY write tool: raise (or set) the memory limit of ONE container on
    ONE Deployment by a JSON-strategic patch on exactly one path. Refuses
    everything else by construction."""
    _slot(deployment, "deployment name", KUBE_NAME_RE)
    _slot(container, "container name", KUBE_NAME_RE)
    _slot(namespace, "namespace", KUBE_NAME_RE)
    _mem_to_mb(memory_limit)  # validation; the value string itself goes into argv

    idx = _container_index(deployment, container, namespace)
    patch = json.dumps([{"op": "replace",
                         "path": f"/spec/template/spec/containers/{idx}/resources/limits/memory",
                         "value": memory_limit}])
    argv = _argv(["patch", "deployment", deployment, "-n", namespace,
                  "--type=json", "-p", patch])
    return run_kubectl(argv)


# ------------------------------------------------------- incident lifecycle

def _patch_memory(deployment: str, container: str, namespace: str,
                  memory_limit: str) -> str:
    """Internal, fixed-argv memory-limit patch used by ensure_incident()."""
    idx = _container_index(deployment, container, namespace)
    patch = json.dumps([{"op": "replace",
                         "path": f"/spec/template/spec/containers/{idx}/resources/limits/memory",
                         "value": memory_limit}])
    argv = _argv(["patch", "deployment", deployment, "-n", namespace,
                  "--type=json", "-p", patch])
    return run_kubectl(argv)


def _container_index(deployment: str, container: str, namespace: str) -> int:
    raw = run_kubectl(_argv(["get", "deployment", deployment, "-n", namespace,
                             "-o", "json", "--show-managed-fields=false"]),
                      max_out=100000)
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as err:
        raise GuardError("could not read the deployment (bad kubectl output)",
                         deny=False) from err
    containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    idx = next((i for i, c in enumerate(containers) if c.get("name") == container), None)
    if idx is None:
        raise GuardError(
            f"container '{container}' not found on deployment '{deployment}' "
            f"(has: {[c.get('name') for c in containers]})", deny=False)
    return idx


def ensure_incident() -> dict:
    """Stage the incident for real before a run: if payments-api is currently
    Running, reset its memory limit to the broken 32Mi (a real kubectl patch,
    same fixed-argv machinery as the agent's write tool) so the pod really
    starts OOMKilling. Idempotent; returns what it did."""
    BROKEN_LIMIT = "32Mi"
    try:
        raw = run_kubectl(_argv(["get", "pods", "-n", config.CITY_NAMESPACE, "-o", "json",
                                 "--show-managed-fields=false"]), max_out=100000)
        doc = json.loads(raw)
        healthy = False
        for item in doc.get("items", []):
            meta = item.get("metadata", {})
            if meta.get("labels", {}).get("app") != config.INCIDENT_APP:
                continue
            if item.get("status", {}).get("phase") == "Running":
                healthy = True
        if not healthy:
            return {"staged": False, "reason": "already failing or absent"}
        _patch_memory(config.INCIDENT_APP, config.INCIDENT_APP,
                      config.CITY_NAMESPACE, BROKEN_LIMIT)
        return {"staged": True, "reason": f"{config.INCIDENT_APP} was healthy; "
                                          f"reset memory limit to {BROKEN_LIMIT} for this run"}
    except GuardError as exc:
        return {"staged": False, "reason": exc.reason}


# ----------------------------------------------------------------- registry

TOOLS = {
    "kubectl_get": {
        "fn": kubectl_get,
        "description": "Read-only kubectl get. Args: resource (kind from "
                       f"{sorted(KUBE_GET_RESOURCES)}), name?, namespace?, output? (wide|json).",
        "params": {"resource": "string", "name": "string?", "namespace": "string?",
                   "output": "string?"},
    },
    "kubectl_describe": {
        "fn": kubectl_describe,
        "description": "Read-only kubectl describe. Args: resource, name?, namespace?.",
        "params": {"resource": "string", "name": "string?", "namespace": "string?"},
    },
    "kubectl_logs": {
        "fn": kubectl_logs,
        "description": "Read-only kubectl logs for a pod. Args: pod, namespace?, tail? (1..200).",
        "params": {"pod": "string", "namespace": "string?", "tail": "int?"},
    },
    "kubectl_patch_limits": {
        "fn": kubectl_patch_limits,
        "description": "The ONLY write tool: set the memory limit of one container on one "
                       "Deployment (e.g. raise it to fix an OOMKilled pod). Args: deployment, "
                       "container, namespace, memory_limit (e.g. 256Mi; allowed 8Mi..4096Mi). "
                       "Refuses any other change.",
        "params": {"deployment": "string", "container": "string", "namespace": "string",
                   "memory_limit": "string"},
    },
}


def call_tool(tool: str, args: dict) -> str:
    """Execute a tool call inside the guard envelope. Blocking (subprocess)."""
    if tool not in TOOLS:
        return json.dumps({"ok": False, "tool": tool, "denied": True,
                           "error": f"GUARD DENIED: unknown tool '{tool}' — "
                                    f"available: {sorted(TOOLS)}"})
    try:
        result = TOOLS[tool]["fn"](**(args or {}))
        return json.dumps({"ok": True, "tool": tool, "result": result})
    except GuardError as exc:
        return json.dumps({"ok": False, "tool": tool, "denied": exc.deny,
                           "error": (f"GUARD DENIED: {exc.reason}" if exc.deny
                                     else exc.reason)})
    except TypeError as exc:
        return json.dumps({"ok": False, "tool": tool, "denied": False,
                           "error": f"bad arguments: {exc}"})
    except Exception as exc:
        return json.dumps({"ok": False, "tool": tool, "denied": False,
                           "error": f"{type(exc).__name__}: {exc}"})
