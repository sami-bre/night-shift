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
