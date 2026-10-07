"""Run orchestration: agent + cluster watcher + city mapper, all archived."""

from __future__ import annotations

import asyncio
import time

from nightshift import config
from nightshift import tools as T
from nightshift.citymap import CityMapper, pod_status_snapshot
from nightshift.llm import make_provider
from nightshift.scenario import mock_script, resolve_pod_placeholder
from nightshift.trace import Run, new_run_id


async def _stage_incident(run: Run) -> dict:
    """Re-break payments-api for real if it's healthy, so every run starts
    from a genuine incident. Waits (bounded) until the pod is ACTUALLY
    failing — never starts the agent against a healthy pod."""
    staged = await asyncio.to_thread(T.ensure_incident)
    if staged.get("staged"):
        run.emit("incident_staged", node="runner", **staged)
        deadline = time.monotonic() + 75.0
        while time.monotonic() < deadline:
            snap = await asyncio.to_thread(pod_status_snapshot)
            status = snap.get("pods", {}).get(config.INCIDENT_APP)
            # genuinely failing = the container is being OOM-killed / restarting
            if snap.get("ok") and status not in (
                    None, "Running", "Pending", "ContainerCreating", "Terminating"):
                break
            await asyncio.sleep(1.5)
    return staged


async def _watch_cluster(run: Run, mapper: CityMapper, stop: asyncio.Event,
                         interval: float = 3.0, max_s: float = 150.0) -> None:
    """Poll REAL pod state; every snapshot becomes an archived cluster_state
    event AND a potential city event. This is what makes smoke/dawn real."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_s
    while not stop.is_set() and loop.time() < deadline:
        snap = await asyncio.to_thread(pod_status_snapshot)
        rec = run.emit("cluster_state", node="watcher", **snap)
        mapper.handle(rec)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def execute_run(run_id: str | None = None, mode: str = "mock",
                      runs_dir=None) -> Run:
    """One full night-shift run. Returns the archived Run."""
    run = Run(run_id or new_run_id(), mode, runs_dir=runs_dir)
    mapper = CityMapper(run)

    from nightshift.graph import run_agent

    await _stage_incident(run)  # REAL incident first: the pod is really failing now
    script = resolve_pod_placeholder(mock_script())
    provider = make_provider(mode, script)
    task = (f"P1: {config.INCIDENT_APP} is failing in namespace "
            f"{config.CITY_NAMESPACE}")

    start = time.monotonic()
    run.emit("run_start", task=task, provider=provider.name, model=provider.model,
             mode=mode)
    for rec in run.read_events():
        mapper.handle(rec)

    stop = asyncio.Event()
    watcher = asyncio.create_task(_watch_cluster(run, mapper, stop))
    try:
        await run_agent(task, provider, run, max_steps=config.MAX_STEPS)
    finally:
        stop.set()
        await asyncio.gather(watcher, return_exceptions=True)

    # consume everything the agent emitted, in order (mapper dedupes by seq)
    for rec in run.read_events():
        mapper.handle(rec)

    # post-run: watch the REAL cluster for the rollout to heal (or timeout honestly)
    recovered = False
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        snap = await asyncio.to_thread(pod_status_snapshot)
        rec = run.emit("cluster_state", node="watcher", **snap)
        mapper.handle(rec)
        if snap.get("ok") and snap.get("pods", {}).get(config.INCIDENT_APP) == "Running":
            recovered = True
            break
        await asyncio.sleep(3.0)

    spend = round(provider.spend_usd(), 6)
    mapper.finalize(recovered=recovered, spend_usd=spend,
                    duration_s=round(time.monotonic() - start, 1),
                    tool_calls=mapper.tool_calls)
    run.write_meta(recovered=recovered, spend_usd=spend,
                   task=task, provider=provider.name, model=provider.model)
    return run
