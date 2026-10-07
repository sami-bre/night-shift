"""City event mapper: translates the agent event stream + real cluster state
into city events for the browser.

Principle: every city event carries `agent_seq` (the trace line that caused it)
or `state_seq` (the cluster_state snapshot it derived from) — nothing moves in
the city without provenance.
"""

from __future__ import annotations

import json
import time

from nightshift import config
from nightshift.trace import Run


def _app_status(payload: dict) -> str | None:
    pods = payload.get("pods") or {}
    st = pods.get(config.INCIDENT_APP)
    if st is None:
        # pods dict maps app label -> status; also tolerate pod-name keys
        for k, v in pods.items():
            if k.startswith(config.INCIDENT_APP):
                return v
    return st


class CityMapper:
    """Feed agent events (in order) via handle(); city events land in the run
    archive and fan out to SSE subscribers."""

    def __init__(self, run: Run):
        self.run = run
        self.t0 = time.monotonic()
        self.seen: set[int] = set()
        self.smoke_started = False
        self.relighted = False
        self.recovery_confirmed = False
        self.tool_calls = 0
        self.patched = False
        self.finish_info: dict | None = None

    def handle(self, rec: dict) -> None:
        """Feed one trace event; safe to re-feed (deduped by seq)."""
        seq = rec.get("seq")
        if seq is not None:
            if seq in self.seen:
                return
            self.seen.add(seq)
        getattr(self, "on_" + rec["event"], lambda *a: None)(rec)

    # -- agent event handlers ---------------------------------------------

    def on_run_start(self, rec: dict) -> None:
        self.run.emit_city("agent_wake", building="power_plant",
                           why="run_start", agent_seq=rec["seq"],
                           mode=rec["payload"].get("mode", self.run.mode))

    def on_cluster_state(self, rec: dict) -> None:
        status = _app_status(rec["payload"])
        if status is None:
            if not rec["payload"].get("ok", True):
                self.run.emit_city("cluster_down", why="cluster_state",
                                   agent_seq=rec["seq"],
                                   error=rec["payload"].get("error", ""))
            return
        healthy = status == "Running"
        if not healthy and not self.smoke_started:
            self.smoke_started = True
            self.run.emit_city("smoke_start", building="bank", why="cluster_state",
                               agent_seq=rec["seq"], status=status)
        elif healthy and self.smoke_started and not self.relighted:
            self.relighted = True
            self.recovery_confirmed = True
            self.run.emit_city("window_relight", building="bank", why="cluster_state",
                               agent_seq=rec["seq"], status=status)

    def on_tool_call(self, rec: dict) -> None:
        self.last_tool = rec
        self.last_result = None
        tool = rec["payload"]["tool"]
        if tool == "kubectl_get":
            self.run.emit_city("townhall_light", building="town_hall",
                               why="tool_call", agent_seq=rec["seq"], tool=tool)
        elif tool == "kubectl_describe":
            self.run.emit_city("magnifier_ping", building="bank",
                               why="tool_call", agent_seq=rec["seq"], tool=tool)
        elif tool == "kubectl_patch_limits":
            self.tool_calls += 1
            self.patched = True
            self.run.emit_city("repair_walk", building="bank", why="tool_call",
                               agent_seq=rec["seq"], tool=tool)

    def on_tool_result(self, rec: dict) -> None:
        tool = rec["payload"]["tool"]
        ok = rec["payload"].get("ok")
        self.tool_calls += 1
        if tool == "kubectl_logs" and ok:
            lines = self._log_lines(rec["payload"])
            for i, line in enumerate(lines):
                self.run.emit_city("log_bubble", building="bank", why="tool_result",
                                   agent_seq=rec["seq"], line=line, index=i)
        if tool == "kubectl_patch_limits" and ok:
            self.run.emit_city("hammer", building="bank", why="tool_result",
                               agent_seq=rec["seq"])
        self.last_result = rec

    def on_guard_denied(self, rec: dict) -> None:
        self.run.emit_city("guard_denied", building="bank", why="guard_denied",
                           agent_seq=rec["seq"], tool=rec["payload"].get("tool"),
                           reason=rec["payload"].get("reason", ""))

    def on_run_finish(self, rec: dict) -> None:
        # the agent's own end is remembered; the CITY resolves only after the
        # runner confirms real recovery (mapper.finalize)
        self.finish_info = rec["payload"]

    def finalize(self, recovered: bool, spend_usd: float = 0.0,
                 duration_s: float = 0.0, tool_calls: int = 0) -> None:
        """Called by the runner once the real cluster state is known post-run."""
        finish = self.finish_info or {}
        error = finish.get("error", "")
        if recovered:
            self.run.emit_city("sky_dawn", why="recovery_confirmed")
            self.run.emit_city("resolved", why="recovery_confirmed",
                               answer=finish.get("answer", ""), error=error,
                               duration_s=duration_s, tool_calls=tool_calls,
                               spend_usd=spend_usd)
        else:
            self.run.emit_city("resolved_failed", why="no_recovery",
                               answer=finish.get("answer", ""), error=error,
                               duration_s=duration_s, tool_calls=tool_calls,
                               spend_usd=spend_usd)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _log_lines(payload: dict) -> list[str]:
        """Real log lines the agent actually read (from the tool result)."""
        text = payload.get("result") or payload.get("result_preview") or ""
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("$ ")]
        return lines[:5]


def pod_status_snapshot() -> dict:
    """Real cluster snapshot: {ok, pods: {app-label: status}, error?}."""
    from nightshift import tools as T
    try:
        raw = T.kubectl_get("pods", namespace=config.CITY_NAMESPACE, output="json")
        doc = json.loads(raw)
        pods: dict[str, str] = {}
        for item in doc.get("items", []):
            name = item.get("metadata", {}).get("name", "?")
            status = item.get("status", {}).get("phase", "Unknown")
            # surface the interesting truth: container state beats pod phase
            css = item.get("status", {}).get("containerStatuses", [])
            if css:
                waiting = css[0].get("state", {}).get("waiting", {})
                last = css[0].get("lastState", {}).get("terminated", {})
                if waiting.get("reason") in ("CrashLoopBackOff", "ImagePullBackOff"):
                    status = waiting["reason"]
                elif last.get("reason") == "OOMKilled" and status != "Running":
                    status = "OOMKilled"
            label = name.rsplit("-", 2)[0] if name.count("-") >= 2 else name
            pods[label] = status
        return {"ok": True, "pods": pods}
    except Exception as exc:
        return {"ok": False, "pods": {}, "error": str(exc)}
