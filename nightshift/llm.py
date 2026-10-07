"""LLM providers: MockLLM (deterministic canned scripts) and OpenRouterLLM.

Both emit strict-JSON agent replies:
  {"thought": "...", "action": {"tool": "...", "args": {...}}}
  {"thought": "...", "finish": "final answer"}
"""

from __future__ import annotations

import json
import re

from nightshift import config


class MockLLM:
    """Replays canned replies in order (deterministic, zero API spend)."""

    name = "mock"
    model = "canned-script"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0, "calls": 0}

    def complete(self, messages: list[dict]) -> dict:
        if not self.script:
            raise RuntimeError("MockLLM script exhausted — agent kept asking")
        self.calls += 1
        return self.script.pop(0)


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str = "https://openrouter.ai/api/v1"):
        self.model = model or config.OPENROUTER_MODEL
        self.api_key = api_key or config.OPENROUTER_API_KEY
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not configured for live mode")
        self.base_url = base_url.rstrip("/")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0, "calls": 0}

    def complete(self, messages: list[dict]) -> dict:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0,
                  "max_tokens": 700, "messages": messages},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        self.usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.usage["completion_tokens"] += usage.get("completion_tokens", 0)
        self.usage["total_tokens"] += usage.get("total_tokens", 0)
        self.usage["calls"] += 1
        content = data["choices"][0]["message"]["content"] or ""
        return parse_agent_json(content)

    def spend_usd(self) -> float:
        return (self.usage["prompt_tokens"] * config.PRICES_PER_TOKEN["in"]
                + self.usage["completion_tokens"] * config.PRICES_PER_TOKEN["out"])


class MockLLMLiveShape(MockLLM):
    """Mock provider that reports a live-shaped spend of 0.0 (mock runs are
    labeled MOCK; spend shown is real zeros, never invented)."""

    def spend_usd(self) -> float:
        return 0.0


def parse_agent_json(content: str) -> dict:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"LLM reply is not valid agent JSON: {content[:200]!r}") from err


class Provider:
    def __init__(self, name: str, model: str, llm):
        self.name = name
        self.model = model
        self.llm = llm

    def complete(self, messages):
        return self.llm.complete(messages)

    def spend_usd(self):
        return getattr(self.llm, "spend_usd", lambda: 0.0)()


def make_provider(mode: str, mock_script: list[dict] | None = None) -> Provider:
    if mode == "live":
        return Provider("openrouter", config.OPENROUTER_MODEL, OpenRouterLLM())
    return Provider("mock", "canned-script", MockLLM(mock_script or []))
