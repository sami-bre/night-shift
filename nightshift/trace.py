"""Trace events + run archive (JSONL, streamed where useful)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from nightshift import config


def new_run_id() -> str:
    return time.strftime("r-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


class Run:
    """One archived run: trace.jsonl + city_events.jsonl + meta.json.
    fan-out: async subscribers get city events; run() may block writing."""

    def __init__(self, run_id: str, mode: str, runs_dir: Path | None = None):
        self.run_id = run_id
        self.mode = mode
        self.namespace = config.CITY_NAMESPACE
        self.app = config.INCIDENT_APP
        self.dir = Path(runs_dir) if runs_dir else config.RUNS_DIR / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.dir / "trace.jsonl"
        self.city_path = self.dir / "city_events.jsonl"
        self._seq = 0
        self._city_seq = 0
        self._t0 = time.monotonic()
        self.subscribers: list = []  # list[asyncio.Queue]

    # -- agent events ------------------------------------------------------

    def emit(self, event: str, node: str = "", **payload) -> dict:
        self._seq += 1
        rec = {
            "seq": self._seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                  + f".{int(time.time() * 1000) % 1000:03d}Z",
            "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
            "run_id": self.run_id,
            "event": event,
            "node": node,
            "payload": payload,
        }
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read_events(self) -> list[dict]:
        if not self.trace_path.exists():
            return []
        return [json.loads(ln) for ln in self.trace_path.read_text().splitlines() if ln.strip()]

    # -- city events -------------------------------------------------------

    def emit_city(self, city_event: str, building: str = "", why: str = "",
                  agent_seq: int | None = None, **data) -> dict:
        self._city_seq += 1
        rec = {
            "seq": self._city_seq,
            "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
            "run_id": self.run_id,
            "type": "city",
            "v": 1,
            "city_event": city_event,
            "building": building,
            "why": why,
            "agent_seq": agent_seq,
            "data": data,
        }
        with self.city_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for q in list(self.subscribers):
            try:
                q.put_nowait(rec)
            except Exception:
                pass
        return rec

    def read_city_events(self) -> list[dict]:
        if not self.city_path.exists():
            return []
        return [json.loads(ln) for ln in self.city_path.read_text().splitlines()
                if ln.strip()]

    # -- agent feed (tool calls, surfaced live in the sidebar) -------------

    def emit_agent(self, tool: str, args: dict, verdict: str, ok: bool,
                   duration_ms: int | None = None, agent_seq: int | None = None,
                   detail: str = "") -> dict:
        """Append a tool-feed record to the shared event stream (type=agent so
        the proof page can render it separately) and fan out to SSE."""
        self._city_seq += 1
        rec = {
            "seq": self._city_seq,
            "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
            "run_id": self.run_id,
            "type": "agent",
            "v": 1,
            "agent_seq": agent_seq,
            "tool": tool,
            "args": args,
            "verdict": verdict,
            "ok": ok,
            "duration_ms": duration_ms,
            "detail": detail[:200],
        }
        with self.city_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for q in list(self.subscribers):
            try:
                q.put_nowait(rec)
            except Exception:
                pass
        return rec

    def write_meta(self, **meta) -> None:
        meta.update({"run_id": self.run_id, "mode": self.mode})
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=1))

    def read_meta(self) -> dict:
        p = self.dir / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    @staticmethod
    def load(run_id: str, runs_dir: Path | None = None) -> Run:
        d = (Path(runs_dir) if runs_dir else config.RUNS_DIR) / run_id
        mode = "unknown"
        meta = d / "meta.json"
        if meta.exists():
            mode = json.loads(meta.read_text()).get("mode", "unknown")
        return Run(run_id, mode, runs_dir=d)
