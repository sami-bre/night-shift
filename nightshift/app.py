"""FastAPI server: city page, SSE event stream, run trigger with rate limits.

All routes are JSON; the static frontend is served from web/. Runs are
archived under runs/<run_id>/ (trace.jsonl, city_events.jsonl, meta.json).
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from nightshift import config
from nightshift import tools as T
from nightshift.citymap import pod_status_snapshot
from nightshift.proof import render_proof_page
from nightshift.runner import execute_run
from nightshift.trace import Run, new_run_id

app = FastAPI(title="The Night Shift", docs_url=None, redoc_url=None)

_state = {
    "active_run": None,        # run_id or None
    "ip_last": {},             # ip -> monotonic of last run START (cooldown)
    "live_count": {"day": "", "n": 0},
}


def _clear_stale_active_markers() -> None:
    """A server restart means no run can still be active: a leftover .active
    marker would make the SSE stream claim a dead run is live."""
    if not config.RUNS_DIR.exists():
        return
    for marker in config.RUNS_DIR.glob("*/.active"):
        marker.unlink(missing_ok=True)


_clear_stale_active_markers()


def _rate_check(ip: str, live: bool) -> tuple[bool, str, int]:
    if _state["active_run"] is not None:
        return False, "a run is already in progress (1 concurrent max) — watch it live", 503
    last = _state["ip_last"].get(ip, 0.0)
    if last and time.monotonic() - last < config.IP_COOLDOWN_S:
        wait = int(config.IP_COOLDOWN_S - (time.monotonic() - last))
        return False, (f"cooldown: each visitor can start a run every "
                       f"{config.IP_COOLDOWN_S}s (next in {wait}s)"), 429
    return True, "", 200


def _today_live_spend() -> float:
    """Completed live-run spend for today, from the archived run metas."""
    total = 0.0
    today = time.strftime("%Y%m%d")
    if config.RUNS_DIR.exists():
        for meta_path in config.RUNS_DIR.glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text())
                rid = str(meta.get("run_id", ""))
                if meta.get("mode") == "live" and rid[2:10] == today:
                    total += float(meta.get("spend_usd", 0) or 0)
            except Exception:
                continue
    return total


def _decide_mode(live_authorized: bool) -> tuple[str, bool]:
    """Public visitors get deterministic mock runs, never live. Live runs only
    on explicit demand behind NIGHTSHIFT_LIVE_TOKEN. The $2/day spend cap
    remains as a guard on the live path; mock is the fallback when limits hit.
    Returns (mode, budget_exhausted)."""
    if not live_authorized:
        return "mock", False
    if (not config.LIVE_ENABLED or not config.OPENROUTER_API_KEY
            or _today_live_spend() >= config.DAILY_SPEND_CAP_USD
            or _state["live_count"]["n"] >= config.LIVE_DAILY_CAP):
        return "mock", True
    return "live", False


async def _run_wrapper(run_id: str, mode: str) -> None:
    run_dir = config.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ".active").write_text("1")
    try:
        await execute_run(run_id=run_id, mode=mode)
    except Exception as exc:  # never leave the SSE stream hanging silently
        run = Run(run_id, mode)
        run.emit("run_finish", node="runner", error=f"{type(exc).__name__}: {exc}")
        run.emit_city("resolved_failed", why="runner_error", error=str(exc)[:300])
        run.write_meta(recovered=False, error=str(exc)[:300])
    finally:
        (run_dir / ".active").unlink(missing_ok=True)
        _state["active_run"] = None


@app.post("/api/run")
async def api_run(request: Request):
    ip = request.client.host if request.client else "?"
    requested_mock = False
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("mode") == "mock":
            requested_mock = True
    except Exception:
        pass
    if request.headers.get("x-nightshift-mode") == "mock":
        requested_mock = True

    live_authorized = (bool(config.LIVE_TOKEN)
                       and request.headers.get("x-nightshift-live", "")
                       == config.LIVE_TOKEN
                       and not requested_mock)
    mode, budget_exhausted = _decide_mode(live_authorized)
    if mode == "live":
        today = time.strftime("%Y%m%d")
        if _state["live_count"]["day"] != today:
            _state["live_count"] = {"day": today, "n": 0}
        _state["live_count"]["n"] += 1
    ok, reason, code = _rate_check(ip, live=(mode == "live"))
    if not ok:
        return JSONResponse({"error": reason}, status_code=code)
    _state["ip_last"][ip] = time.monotonic()

    run_id = new_run_id()
    _state["active_run"] = run_id
    asyncio.create_task(_run_wrapper(run_id, mode))
    return JSONResponse({"run_id": run_id, "mode": mode,
                         "budget_exhausted": budget_exhausted})


@app.get("/api/status")
async def api_status():
    snap = await asyncio.to_thread(pod_status_snapshot)
    return JSONResponse({
        "cluster_ok": snap.get("ok", False),
        "pods": snap.get("pods", {}),
        "incident_app": config.INCIDENT_APP,
        "active_run": _state["active_run"],
        "latest_run": _latest_run_id(),
    })


def _latest_run_id() -> str | None:
    if not config.RUNS_DIR.exists():
        return None
    runs = sorted((d for d in config.RUNS_DIR.iterdir()
                   if d.is_dir() and (d / "city_events.jsonl").exists()),
                  key=lambda d: d.name)
    return runs[-1].name if runs else None


@app.get("/api/events/{run_id}")
async def api_events(run_id: str, request: Request):
    run_dir = config.RUNS_DIR / run_id
    if not run_dir.is_dir():
        return JSONResponse({"error": "no such run"}, status_code=404)

    async def gen():
        pos = 0
        # LIVE if the run is currently active (has .active marker); else REPLAY.
        # The mode is declared up front so the client never guesses.
        live = (run_dir / ".active").exists()
        yield "event: meta\ndata: " + json.dumps({"replay": not live, "run_id": run_id}) + "\n\n"
        idle_after_done = 0.0
        while True:
            if await request.is_disconnected():
                return
            path = run_dir / "city_events.jsonl"
            if path.exists():
                data = path.read_bytes()
                new = data[pos:]
                pos = len(data)
                for raw in new.splitlines():
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        yield f"event: city\ndata: {line}\n\n"
                if new:
                    idle_after_done = 0.0
            done = not (run_dir / ".active").exists()
            if done:
                idle_after_done += 0.2
                if idle_after_done >= 1.5:
                    yield "event: done\ndata: {}\n\n"
                    return
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{run_id}/agent-feed")
async def api_agent_feed(run_id: str):
    """Tool-call records for a run — the sidebar pre-renders the last run's
    feed from this so it is never empty on page load."""
    run_dir = config.RUNS_DIR / run_id
    if not run_dir.is_dir():
        return JSONResponse({"error": "no such run"}, status_code=404)
    feed = [r for r in Run.load(run_id).read_city_events()
            if r.get("type") == "agent"]
    return JSONResponse({"run_id": run_id, "feed": feed})


@app.get("/api/runs/{run_id}/trace")
async def api_trace(run_id: str):
    run_dir = config.RUNS_DIR / run_id
    if not run_dir.is_dir():
        return JSONResponse({"error": "no such run"}, status_code=404)
    return HTMLResponse(render_proof_page(Run.load(run_id)))


def _should_restage(snap: dict) -> bool:
    """True when the incident app has recovered and no run is active — the
    timer then re-breaks it so the demo's resting state is a live incident."""
    if _state["active_run"] is not None:
        return False
    if not snap.get("cluster_ok"):
        return False
    return snap.get("pods", {}).get(config.INCIDENT_APP) == "Running"


async def _incident_timer() -> None:
    while config.INCIDENT_RESET_S > 0:
        await asyncio.sleep(config.INCIDENT_RESET_S)
        try:
            snap = await asyncio.to_thread(pod_status_snapshot)
            if _should_restage(snap):
                await asyncio.to_thread(T.ensure_incident)
        except Exception:
            continue  # degraded cluster / missing kubectl: retry next tick


@app.on_event("startup")
async def _start_incident_timer() -> None:
    asyncio.create_task(_incident_timer())


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
