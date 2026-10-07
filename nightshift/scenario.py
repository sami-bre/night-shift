"""The canned mock scenario (deterministic, zero API spend).

The script is the same diagnosis a competent on-call engineer performs; the
pod name is a placeholder resolved from the REAL cluster before the run so the
canned path executes real tool calls against real pod state.
"""

from __future__ import annotations

import json

from nightshift import config

PAYMENTS_POD_PLACEHOLDER = "PAYMENTS_API_POD"


def resolve_pod_placeholder(script: list[dict]) -> list[dict]:
    """Replace PAYMENTS_API_POD with the REAL payments-api pod name from the
    cluster, so the mock path executes against real pod names."""
    pod = config.INCIDENT_APP
    try:
        from nightshift import tools as T
        raw = T.kubectl_get("pods", namespace=config.CITY_NAMESPACE, output="json")
        doc = json.loads(raw)
        for item in doc.get("items", []):
            labels = item.get("metadata", {}).get("labels", {})
            if labels.get("app") == config.INCIDENT_APP:
                pod = item["metadata"]["name"]
                break
    except Exception:
        pass  # cluster down: keep the plain app name; the run will show cluster_down
    return json.loads(json.dumps(script).replace(PAYMENTS_POD_PLACEHOLDER, pod))


def mock_script() -> list[dict]:
    pod = PAYMENTS_POD_PLACEHOLDER
    ns = config.CITY_NAMESPACE
    return [
        {"thought": "P1 page: payments-api is failing. First, look at the pods "
                    "to see the actual state.",
         "action": {"tool": "kubectl_get", "args": {"resource": "pods",
                                                    "namespace": ns}}},
        {"thought": "payments-api is crash-looping (OOMKilled). I need its recent "
                    "logs to see where it dies.",
         "action": {"tool": "kubectl_logs", "args": {"pod": pod, "namespace": ns,
                                                     "tail": 10}}},
        {"thought": "It dies right after allocating the payments cache. The "
                    "deployment's memory settings are the prime suspect.",
         "action": {"tool": "kubectl_describe", "args": {"resource": "deployments",
                                                         "name": "payments-api",
                                                         "namespace": ns}}},
        {"thought": "Confirmed: memory limit is 32Mi but the app allocates a 64MB "
                    "cache at boot — it gets OOMKilled every restart. Raising the "
                    "limit to 256Mi is the smallest correct fix.",
         "action": {"tool": "kubectl_patch_limits", "args": {"deployment": "payments-api",
                                                             "container": "payments-api",
                                                             "namespace": ns,
                                                             "memory_limit": "256Mi"}}},
        {"thought": "Patch applied — a new pod is rolling out. Verify the new pod "
                    "is healthy.",
         "action": {"tool": "kubectl_get", "args": {"resource": "pods",
                                                    "namespace": ns}}},
        {"thought": "The replacement pod is Running with the raised limit. "
                    "Incident resolved.",
         "finish": "Root cause: payments-api allocates a 64 MB payments cache at boot "
                   "under a 32Mi memory limit, so the container was OOMKilled on every "
                   "restart (CrashLoopBackOff, exit 137). Fix: raised the container's "
                   "memory limit to 256Mi via kubectl_patch_limits (the only write tool "
                   "I have). The replacement pod came up Running and stayed healthy. "
                   "Verified with kubectl get pods."},


    ]
