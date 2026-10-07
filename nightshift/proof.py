"""Render the proof page: raw JSONL trace side-by-side with the city events."""

from __future__ import annotations

import html
import json

from nightshift.trace import Run

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Night Shift run {run_id} — proof panel</title>
<style>
  body {{ font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
         margin: 0; background: #0d1117; color: #c9d1d9; }}
  header {{ padding: 1.2rem 1.5rem; border-bottom: 1px solid #21262d; background: #010409; }}
  h1 {{ font-size: 1.05rem; margin: 0; color: #e6edf3; }}
  .meta {{ color: #8b949e; font-size: .8rem; margin-top: .3rem; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: #21262d;
          min-height: 80vh; }}
  @media (max-width: 900px) {{ .cols {{ grid-template-columns: 1fr; }} }}
  .col {{ background: #0d1117; padding: 1rem 1.2rem; }}
  h2 {{ font-size: .85rem; color: #58a6ff; text-transform: uppercase; letter-spacing: .08em; }}
  .ev {{ border-left: 2px solid #30363d; padding: .25rem 0 .25rem .8rem; margin: .45rem 0;
        font-size: .78rem; }}
  .ev.agent {{ border-color: #d29922; }}
  .ev.city {{ border-color: #3fb950; }}
  .ev.denied {{ border-color: #f85149; background: #17111226; }}
  .ev.linked {{ background: #111826; }}
  .seq {{ color: #8b949e; }}
  pre {{ margin: .2rem 0 0; white-space: pre-wrap; word-break: break-word; color: #adbac7;
       font-size: .74rem; }}
  .arrow {{ color: #3fb950; }}
  footer {{ padding: 1rem 1.5rem; color: #8b949e; font-size: .75rem; }}
</style>
</head>
<body>
<header>
  <h1>Night Shift — proof panel for run <code>{run_id}</code></h1>
  <div class="meta">mode: {mode} · left: the raw agent trace (JSONL, unmodified) ·
  right: the city events it produced · linked by <code>agent_seq</code></div>
</header>
<div class="cols">
  <div class="col"><h2>Agent trace (raw)</h2>{agent_col}</div>
  <div class="col"><h2>City events</h2>{city_col}</div>
</div>
<footer>Every city event carries <code>agent_seq</code> / its derivation —
nothing moved in the city without a trace line behind it.</footer>
</body>
</html>
"""


def _block(rec: dict, cls: str) -> str:
    denied = " denied" if rec.get("event") == "guard_denied" else ""
    return (f'<div class="ev {cls}{denied}"><span class="seq">#{rec.get("seq", "?")} '
            f'@{rec.get("elapsed_ms", 0)}ms</span> {html.escape(rec.get("event", ""))}'
            f'<pre>{html.escape(json.dumps(rec.get("payload", {}), ensure_ascii=False))}</pre></div>')


def _city_block(rec: dict) -> str:
    if rec.get("type") == "agent":
        deriv = f" ← agent#{rec.get('agent_seq')} ({rec.get('verdict')})"
        payload = {"tool": rec.get("tool"), "args": rec.get("args"),
                   "verdict": rec.get("verdict"), "duration_ms": rec.get("duration_ms")}
        return (f'<div class="ev city">'
                f'<span class="seq">#{rec.get("seq", "?")} @{rec.get("elapsed_ms", 0)}ms</span> '
                f'<span style="color:#ffd75e">tool: {html.escape(str(rec.get("tool")))}</span> '
                f'<span class="arrow">{html.escape(deriv)}</span>'
                f'<pre>{html.escape(json.dumps(payload, ensure_ascii=False))}</pre></div>')
    linked = f' data-agent-seq="{rec.get("agent_seq")}"' if rec.get("agent_seq") else ""
    why = rec.get("why", "")
    aseq = rec.get("agent_seq")
    deriv = f" ← agent#{aseq} ({why})" if aseq else f" ← {why}"
    return (f'<div class="ev city"{linked}>'
            f'<span class="seq">#{rec.get("seq", "?")} @{rec.get("elapsed_ms", 0)}ms</span> '
            f'{html.escape(rec.get("city_event", ""))} '
            f'<span class="arrow">{html.escape(deriv)}</span>'
            f'<pre>{html.escape(json.dumps(rec.get("data", {}), ensure_ascii=False))}</pre></div>')


def render_proof_page(run: Run) -> str:
    agent_events = run.read_events()
    city_events = run.read_city_events()
    meta = run.read_meta()
    mode = meta.get("mode", run.mode)
    agent_col = "\n".join(_block(r, "agent") for r in agent_events)
    city_col = "\n".join(_city_block(r) for r in city_events)
    return TEMPLATE.format(run_id=html.escape(run.run_id), mode=html.escape(str(mode)),
                           agent_col=agent_col, city_col=city_col)
