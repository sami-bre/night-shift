# SPEC.md — The Night Shift

> Pitch (fixed): *"Watch an AI agent get paged at 3 AM, diagnose a crashing service on a
> real Kubernetes cluster, and heal it — rendered as a pixel-art city where every light
> is a real tool call."*

A pixel-art city runs in the browser. Underneath it is a **real k3d cluster** on the
demo VPS with a **really broken** service (`payments-api` OOMKilled, crash-looping for
real). An LLM agent (OpenRouter flash-class, or a deterministic mock) gets paged,
investigates through **guarded tools**, applies the one allowed fix
(`kubectl_patch_limits`), and verifies recovery. Every agent action and every real
cluster-state change is emitted as an event; the city maps events to visuals —
smoke over the bank only while the pod is *actually* failing, log bubbles containing
the *actual* log tails the agent read, a repair sprite that walks only when the patch
tool *actually* executed, and a dawn sky only after the pod is *actually* Running.
If any pixel moves without a trace event behind it, the project has failed.

## 1. The incident (real, not simulated)

Cluster `night-shift` (k3d, idempotent `k3d/bootstrap.sh`), namespace `city`:

| workload | state | role in the story |
| --- | --- | --- |
| `payments-api` | **OOMKilled / CrashLoopBackOff by design** (memory limit 32Mi, app allocates 64Mi at boot after printing real log lines) | the city bank — smoke/flicker while really failing |
| `web` (nginx) | Running | city lights / houses |
| `redis` | Running | houses |
| `postgres` | Running | hospital |

The broken state is real pod state; `CrashLoopBackOff` + `OOMKilled` + a `kubectl logs`
tail showing `MemoryError`-adjacent lines are what the agent actually sees. The fix
(raise the limit) makes a *new pod* actually go `Running`. Nothing is faked: if the
cluster is down, the status line says `cluster down` honestly and the page still renders.

## 2. Tool surface (guarded, MCP-style; guards in the tool layer)

In-process tool registry (single backend process serves tools + SSE — a stdio MCP hop
adds a subprocess and an event-relay with no benefit here; the guard philosophy is
identical to mcp-ops-agent, and every tool answers the same JSON envelope
`{"ok": bool, "tool": str, "denied": bool, "error"/"result": ...}`).

| tool | args | guard |
| --- | --- | --- |
| `kubectl_get` | `resource`, `name?`, `namespace?`, `output?` (`wide\|json`) | read-only; fixed argv; resource kind from allowlist; name/namespace regex-validated |
| `kubectl_describe` | `resource`, `name?`, `namespace?` | read-only; fixed argv |
| `kubectl_logs` | `pod`, `namespace?`, `tail?` (1..200) | read-only; fixed argv |
| `kubectl_patch_limits` | `deployment`, `container`, `namespace`, `memory_limit` | **the only write tool**; refuses everything except a vetted resource-limits patch (see §3) |

Deviation note (engineering, not scope): the brief says "patch a named pod"; Kubernetes
forbids mutating a running pod's resources in place, so the patch tool patches the
**Deployment that owns the pod** (raising `resources.limits.memory` for the named
container), which triggers the real rollout that heals the service. This is documented
in the tool description and SPEC rather than hidden.

## 3. Guard rules

1. **Read-only kubectl:** argv is fixed by the server; user values only fill validated
   slots (kinds allowlist; `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` names). No flags accepted
   from the model → `delete/apply/exec/patch` unreachable via these tools.
2. **The write tool refuses by construction.** `kubectl_patch_limits` accepts only:
   - `deployment`, `container`, `namespace` matching validated regexes
   - `memory_limit` matching `^[0-9]{1,4}(Mi|Gi)$` (range-checked: total 8Mi–4Gi)
   - it internally looks up the container index, then issues a **JSON-strategic patch
     on exactly one path** `/spec/template/spec/containers/<idx>/resources/limits/memory`.
   Any other field, other resource kind, other operation, or invalid value → guard denial.
   The underlying argv never contains model text beyond the validated tokens.
3. **No shell tool at all** this time (smaller surface than mcp-ops-agent; the story
   doesn't need one).
4. **Secrets/filesystem:** tools have no file access; nothing outside the cluster.
5. **Server-side caps:** 1 concurrent run; per-IP cooldown 10 min; hard daily cap on
   live-LLM runs (mock default for anonymous visitors); trace payloads truncated.

## 4. Agent graph (LangGraph, ReAct-style)

Same shape as mcp-ops-agent (`reason → act → loop → finalize`, `MAX_STEPS = 10`), with
the tool roster and the strict-JSON reply contract
(`{"thought": ..., "action": {"tool","args"}}` / `{"thought": ..., "finish": ...}`).
Providers: `MockLLM` (canned script replayed in order — the default everywhere: tests,
`make demo`, anonymous page runs) and `OpenRouterLLM` (httpx → chat completions,
temperature 0, `google/gemini-3-flash-preview` default). **Live mode is open-ended**:
the system prompt describes the alert, not the fix; the LLM must choose get/describe/
logs and decide the patch itself. Run-to-run traces genuinely differ.

The incident briefing the agent receives: "P1: payments-api in namespace `city` is
crash-looping" — deliberately no mention of memory limits.

## 5. Event schema & city mapping

### Agent events (JSONL trace, `runs/<run_id>/trace.jsonl`)

`run_start`, `cluster_state` (real pod snapshot: `{"payments-api": "OOMKilled", ...}`),
`llm_request`, `llm_response`, `tool_call`, `tool_result`, `guard_denied`,
`run_finish`. Each line: `{"seq", "ts", "elapsed_ms", "run_id", "event", "node",
"payload"}`.

### City events (SSE stream, `GET /api/events/{run_id}`)

The mapper consumes the agent event stream and real cluster-state snapshots:

```json
{"type": "city", "v": 1, "city_event": "smoke_start", "building": "bank",
 "why": "tool_call", "agent_seq": 4, "data": {...}}
```

Mapping table (frontend implements exactly these):

| agent/cluster source | city event | visual |
| --- | --- | --- |
| `cluster_state` payments-api != Running | `smoke_start` | bank flickers, smoke pixels rise, alert banner `P1: payments-api failing` |
| `run_start` | `agent_wake` | power-plant windows pulse, status line "agent on shift" |
| `tool_call` `kubectl_get` | `townhall_light` | town-hall window lights up (2.5s) |
| `tool_call` `kubectl_logs` + its `tool_result` | `log_bubbles` (one per real log line, capped 5) | bank speech bubbles with the actual log tail |
| `tool_call` `kubectl_describe` | `magnifier_ping` | magnifying-glass ping over the bank |
| `tool_call` `kubectl_patch_limits` | `repair_walk` | repair sprite walks power-plant → bank (real elapsed time) |
| `tool_result` ok from `kubectl_patch_limits` | `hammer` | hammer animation ×3 at the bank |
| `cluster_state` payments-api == Running (post-patch) | `window_relight` (one per window, 300ms apart) | bank windows relight one by one |
| `cluster_state` healthy | `sky_dawn` | night gradient lightens over 10s, stars fade |
| `run_finish` (after recovery confirmed) | `resolved` | banner "RESOLVED in Ns · X tool calls · $0.0003" + link to `/trace` page |
| `run_finish` (no recovery / error) | `resolved_failed` | honest banner: what ended the run |
| `cluster_state` unreachable | `cluster_down` | status line "cluster down — demo degraded honestly" |

## 6. Server (FastAPI, port 8808 on localhost)

- `GET /` → `web/` static (city page).
- `POST /api/run` `{mode: "mock"|"live"}` → starts a run, returns `{run_id}`.
  Rate limits: 1 concurrent run (503 otherwise), per-IP cooldown 600s, live daily cap
  (`NIGHTSHIFT_LIVE_DAILY_CAP`, default 20), anonymous visitors default to mock;
  live requires the `X-NightShift-Live` token from the env file (Samuel's control).
- `GET /api/events/{run_id}` → SSE stream of city events (replays the run's archived
  events at their recorded pace if the run is over).
- `GET /api/status` → real cluster snapshot for the status line.
- `GET /api/runs/{run_id}/trace` → rendered proof page: raw JSONL trace side-by-side
  with the city events it produced (the trust layer, one click from the city).
- Runs archived under `runs/<run_id>/` (`trace.jsonl`, `city_events.jsonl`, `meta.json`).

## 7. Frontend (canvas + sprite sheet)

- `tools/gen_sprites.py` generates `web/assets/sprites.png` (Pillow) — buildings
  (bank/town hall/hospital/power plant/houses), repair-sprite walk/hammer frames,
  smoke puffs — so the art is reproducible from code with provenance. Committed.
- `web/city.js`: canvas state machine `idle → alert → respond → dawn`; star field,
  twinkling windows, day/night sky gradient interpolation; SSE consumer applying city
  events; status line fed by `/api/status` (real cluster state).
- On load with no active run, the page auto-replays the latest archived run (instant
  wow for a visitor; every replay is a replay of real events, labeled as such).

## 8. Tests (mock-first) & repo

- `test_guards.py`: patch tool refuses non-limit patches, bad quantities, other kinds;
  kubectl argv validation; read-only tools reject injected flags/names.
- `test_agent.py`: MockLLM happy path (page → get → logs → describe → patch → verify →
  finish) and max-steps guard; every tool call produces a trace event.
- `test_citymap.py`: each agent event type produces exactly the mapped city events.
- `test_server.py`: SSE smoke (city events flow), rate limiting (concurrent 503,
  cooldown 429), run archive written.
- Makefile: `cluster-up`, `demo` (mock run through the server), `serve`, `test`,
  `screenshots`, `clean`. CI: ruff + pytest (GitHub Actions, green badge).

## 9. Honesty rules

- Live mode leaves diagnosis open-ended; no scripted tool paths in live traces.
- Smoke/dawn/relight derive from real cluster state, not the agent's claims.
- Mock runs are labeled MOCK in the banner; live runs show real spend.
- If the cluster is down, the page says so and degrades gracefully.
- `DEMO_LOG.md` records every live run's spend; key never committed or printed.