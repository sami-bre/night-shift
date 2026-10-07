"""LangGraph ReAct agent for The Night Shift.

Same loop shape as mcp-ops-agent (reason -> act -> finalize), emitting trace
events into the Run recorder. Tool execution is guarded (nightshift.tools) and
runs in a worker thread (kubectl is a blocking subprocess).
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from nightshift import tools as tools_mod
from nightshift.llm import Provider
from nightshift.trace import Run

SYSTEM_PROMPT = """You are an on-call operations agent for "The Night Shift": a tiny \
Kubernetes cluster (k3d) running a city of services in namespace "{ns}".

You have just been paged: P1 — the service "{app}" is failing (restarting or \
unhealthy) in namespace "{ns}".

Available tools (all guarded; guards live in the tool layer, not in this prompt):
{roster}

Respond with STRICT JSON only, no prose outside the JSON, in exactly one of:
  {{"thought": "...", "action": {{"tool": "<tool name>", "args": {{...}}}}}}
  {{"thought": "...", "finish": "<concise report for the humans: root cause + what you did>"}}

Rules:
- Investigate before acting: look at pods, logs, and the deployment description.
- Exactly one tool can change the world: kubectl_patch_limits raises a container's \
memory limit on a Deployment. Everything else is read-only.
- If a call is denied or errors, do not retry the same call; adapt or finish.
- When the evidence is sufficient, apply the smallest correct fix, then verify it \
worked, then finish.
"""


def build_roster() -> str:
    lines = []
    for name, t in tools_mod.TOOLS.items():
        params = ", ".join(f"{k}: {v}" for k, v in t["params"].items())
        lines.append(f"- {name}({params}): {t['description']}")
    return "\n".join(lines)


def _truncate(text: str, limit: int = 1200) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [truncated, {len(text)} chars]"


class AgentState(TypedDict):
    task: str
    history: Annotated[list[dict], lambda a, b: b]
    steps: int
    done: bool
    answer: str
    pending_action: dict | None
    error: str


def build_graph(provider: Provider, run: Run, max_steps: int):
    """Compile the agent graph. run.emit records every transition."""

    async def reason(state: AgentState) -> dict:
        messages = [{"role": "system",
                     "content": SYSTEM_PROMPT.format(ns=run.namespace, app=run.app,
                                                     roster=build_roster())}]
        messages += state["history"]
        run.emit("llm_request", node="reason", provider=provider.name,
                 model=provider.model,
                 messages_preview=_truncate("\n".join(m["content"] for m in messages)))
        try:
            reply = await asyncio.to_thread(provider.complete, messages)
        except Exception as exc:
            run.emit("llm_response", node="reason", error=f"{type(exc).__name__}: {exc}")
            return {"done": True, "error": f"LLM call failed: {exc}",
                    "answer": "Run ended: the LLM call failed."}
        run.emit("llm_response", node="reason", reply=reply)

        if reply.get("finish"):
            return {"done": True, "answer": str(reply["finish"])}
        action = reply.get("action") or {}
        tool, args = action.get("tool"), action.get("args")
        if not tool or not isinstance(args, dict):
            return {"done": True, "error": "malformed action",
                    "answer": "Run ended: the LLM produced a malformed action."}
        return {"pending_action": {"tool": tool, "args": args}}

    async def act(state: AgentState) -> dict:
        action = state.get("pending_action") or {}
        tool, args = action.get("tool"), action.get("args") or {}
        run.emit("tool_call", node="act", tool=tool, args=args)
        raw = await asyncio.to_thread(tools_mod.call_tool, tool, args)
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            envelope = {"ok": False, "denied": False, "error": f"non-JSON reply: {raw[:200]}"}
        if envelope.get("denied"):
            run.emit("guard_denied", node="act", tool=tool, args=args,
                     reason=envelope.get("error", "denied"))
        else:
            run.emit("tool_result", node="act", tool=tool, ok=envelope.get("ok", False),
                     error=envelope.get("error"),
                     result_preview=_truncate(str(envelope.get("result", ""))),
                     result=str(envelope.get("result", ""))[:6000])
        history = list(state["history"])
        history.append({"role": "assistant",
                        "content": json.dumps({"thought": "", "action": action})})
        history.append({"role": "user", "content": "observation: " + _truncate(raw, 6000)})
        return {"history": history, "steps": state["steps"] + 1, "pending_action": None}

    def route(state: AgentState) -> str:
        if state["done"] or state["steps"] >= max_steps:
            return "finalize"
        return "act"

    async def finalize(state: AgentState) -> dict:
        if not state["done"] and state["steps"] >= max_steps and not state["error"]:
            run.emit("run_finish", node="finalize", reason="max_steps_reached")
            return {"done": True,
                    "answer": "Stopped after the step limit; no final answer produced."}
        run.emit("run_finish", node="finalize", answer=state["answer"], error=state["error"])
        return {}

    g = StateGraph(AgentState)
    g.add_node("reason", reason)
    g.add_node("act", act)
    g.add_node("finalize", finalize)
    g.add_edge(START, "reason")
    g.add_conditional_edges("reason", route, {"act": "act", "finalize": "finalize"})
    g.add_edge("act", "reason")
    g.add_edge("finalize", END)
    return g.compile()


async def run_agent(task: str, provider: Provider, run: Run, max_steps: int = 10) -> dict:
    final = {"task": task, "history": [{"role": "user", "content": f"task: {task}"}],
             "steps": 0, "done": False, "answer": "", "pending_action": None, "error": ""}
    graph = build_graph(provider, run, max_steps)
    async for update in graph.astream(final, stream_mode="updates"):
        for _node, delta in update.items():
            if delta:
                final.update(delta)
    return final
