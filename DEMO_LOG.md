# DEMO_LOG.md — The Night Shift, live-run spend

Key policy (user-set): the shared OpenRouter key is for demo-runtime agent
calls only (cheap flash class); build work runs on a separate provider.
All development/verification iterations used MockLLM (zero spend); live runs
below are the real thing. Model: `google/gemini-3-flash-preview`
($0.50/M input, $3.00/M output at time of build).

## Live verification runs (2026-10-07, through https://samibre.dev/night-shift/api/run)

| run | model | result | tool calls | spend |
| --- | --- | --- | --- | --- |
| r-20261007-172242-a809 | google/gemini-3-flash-preview | recovered=True | 4 (get → describe pod → patch **128Mi** → verify) → logs | $0.0061 |

Notes: the live agent chose 128Mi (vs the canned script's 256Mi) and read the
replacement pod's logs — a genuinely different, correct diagnosis path from the
mock script. Spend read from the run's usage counters (meta.json spend_usd).
| r-20261007-173256-df49 | google/gemini-3-flash-preview | recovered=True | 4 (get ×3 → patch) — did NOT call kubectl_logs at all | $0.0061 |

Run-to-run variance confirmed: run 1 described the pod first and chose 128Mi; run 2
never read logs and still fixed it. Total live spend: $0.0123. Key balance after:
$27.38 (well above the $3 floor).

## 2026-10-07 (evening) — live-by-default + $2/day budget + mission-control sidebar

- Policy change (Samuel): live OpenRouter runs are now the DEFAULT for public
  visitors; mock is the fallback. New hard cap: $2.00/day live spend, enforced
  server-side from archived per-run spend_usd counters; on exhaustion visitors
  get deterministic mock runs and the UI says "live budget exhausted".
- New mission-control sidebar: every tool call streams live (tool, args,
  verdict ok/error/denied/cluster_unavailable, duration_ms) via type=agent
  records on the same SSE stream. LLM internals stay in the trace report.
- Live verification through the public URL (live-by-default, no header):
  r-20261007-180138-46c2 — recovered=True, spend $0.0061, agent feed streamed
  5 tool calls (get → describe pod → patch 128Mi → get → get) with real args
  and durations. Sidebar visually verified in a browser.
- Live spend today total: $0.0123 (2 runs) + $0.0061 = $0.0184. Well under cap.

## 2026-10-07 (late evening) — mock-revert + sidebar polish + favicon

- REVERTED live-by-default per Samuel: public visitors get deterministic mock
  runs, never live; live only behind NIGHTSHIFT_LIVE_TOKEN. The $2/day cap and
  spend logging remain as guards on the token-gated live path. Tests updated
  (39 passing).
- Mission-control sidebar: never empty on load — pre-renders the last
  archived run's tool calls (new GET /api/runs/{id}/agent-feed) marked
  "previous shift"; live entries append below; stacking breakpoint lowered
  to 760px with sidebar min-height on stacked layouts.
- Included web/favicon.svg + <link rel=icon> (Bud's change).
- No new live spend today beyond the $0.0184 logged above.
