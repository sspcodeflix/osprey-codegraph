"""LLM provider shim for the Ask feature (ARCHITECTURE.md §10).

Local-first: Ollama is the default and runs entirely on this machine — no
code leaves the network. Anthropic is the cloud opt-in, active only when a
key is configured. Both speak one tiny interface: chat(messages, tools) ->
text or tool calls. The model never sees the database, only the typed tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from osprey.config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)   # echoed back verbatim
    usage: dict = field(default_factory=dict)         # prompt/completion tokens


class OllamaProvider:
    def __init__(self, model: str | None = None):
        self.model = model or settings.chat_model
        self.url = settings.ollama_url

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        payload = {
            "model": self.model, "messages": messages,
            "stream": False, "think": False,
            "options": {"temperature": 0.2, "num_ctx": 16384},
        }
        if tools:
            payload["tools"] = tools
        res = httpx.post(f"{self.url}/api/chat", json=payload, timeout=300)
        res.raise_for_status()
        data = res.json()
        msg = data["message"]
        calls = [
            ToolCall(str(i), c["function"]["name"],
                     c["function"].get("arguments") or {})
            for i, c in enumerate(msg.get("tool_calls") or [])
        ]
        usage = {"prompt_tokens": data.get("prompt_eval_count", 0),
                 "completion_tokens": data.get("eval_count", 0)}
        return ChatResponse(msg.get("content") or "", calls, msg, usage)

    def tool_result_message(self, call: ToolCall, result: object) -> dict:
        return {"role": "tool", "tool_name": call.name,
                "content": json.dumps(result, default=str)}


class AnthropicProvider:
    """Cloud opt-in: requires OSPREY_ANTHROPIC_API_KEY. Sends code excerpts
    to Anthropic — deployments must surface that choice to the org."""

    def __init__(self, model: str | None = None):
        if not settings.anthropic_api_key:
            raise RuntimeError("OSPREY_ANTHROPIC_API_KEY is not set")
        self.model = model or "claude-sonnet-5"
        self.key = settings.anthropic_api_key

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        system = "\n".join(m["content"] for m in messages
                           if m["role"] == "system")
        anth_messages = [m for m in messages if m["role"] != "system"]
        anth_tools = [{"name": t["function"]["name"],
                       "description": t["function"]["description"],
                       "input_schema": t["function"]["parameters"]}
                      for t in tools]
        res = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key,
                     "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": 2048, "system": system,
                  "messages": anth_messages, "tools": anth_tools},
            timeout=120)
        res.raise_for_status()
        data = res.json()
        text = "".join(b["text"] for b in data["content"]
                       if b["type"] == "text")
        calls = [ToolCall(b["id"], b["name"], b["input"])
                 for b in data["content"] if b["type"] == "tool_use"]
        return ChatResponse(text, calls,
                            {"role": "assistant", "content": data["content"]})

    def tool_result_message(self, call: ToolCall, result: object) -> dict:
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id,
             "content": json.dumps(result, default=str)}]}


class OpenAICompatProvider:
    """OpenAI-compatible chat-completions backends (DeepSeek et al.). Cloud
    opt-in: code excerpts leave the machine when this provider is active."""

    def __init__(self, model: str, base_url: str, api_key: str,
                 label: str = "openai-compatible"):
        if not api_key:
            raise RuntimeError(f"{label}: API key is not configured")
        self.model, self.base_url, self.key = model, base_url, api_key

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        payload = {"model": self.model, "messages": messages,
                   "temperature": 0.2}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        res = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json=payload, timeout=300)
        res.raise_for_status()
        data = res.json()
        msg = data["choices"][0]["message"]
        calls = [
            ToolCall(c["id"], c["function"]["name"],
                     json.loads(c["function"].get("arguments") or "{}"))
            for c in (msg.get("tool_calls") or [])
        ]
        usage = {"prompt_tokens":
                 data.get("usage", {}).get("prompt_tokens", 0),
                 "completion_tokens":
                 data.get("usage", {}).get("completion_tokens", 0)}
        return ChatResponse(msg.get("content") or "", calls, msg, usage)

    def tool_result_message(self, call: ToolCall, result: object) -> dict:
        return {"role": "tool", "tool_call_id": call.id,
                "content": json.dumps(result, default=str)}


def get_provider():
    if settings.chat_provider == "anthropic":
        return AnthropicProvider()
    if settings.chat_provider == "deepseek":
        return OpenAICompatProvider(
            settings.deepseek_model, settings.deepseek_base_url,
            settings.deepseek_api_key, "deepseek")
    return OllamaProvider()
