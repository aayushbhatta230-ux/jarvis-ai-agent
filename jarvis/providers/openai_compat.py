"""OpenAI-compatible chat-completions provider.

Works with OpenAI and any gateway exposing ``/v1/chat/completions``
(OpenRouter, Groq, LM Studio, vLLM, LiteLLM, ...). Configure ``base_url``
and the name of the secret that holds the API key.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from jarvis.core import secrets
from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.providers.base import (
    ChatResult,
    ModelUnavailable,
    Provider,
    ProviderError,
    RateLimited,
    StreamCallback,
    ToolCall,
    ToolSpec,
    parse_arguments,
)

log = logging.getLogger(__name__)


class OpenAICompatProvider(Provider):
    type = "openai"

    def __init__(self, name: str, settings: dict[str, Any]) -> None:
        super().__init__(name, settings)
        self.base_url = self.base_url or "https://api.openai.com/v1"
        self._models: tuple[float, list[str]] = (0.0, [])

    def _key(self) -> str | None:
        secret = self.settings.get("secret")
        return secrets.get(secret) if secret else None

    def _headers(self) -> dict[str, str]:
        key = self._key()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def available(self) -> tuple[bool, str]:
        if self.settings.get("secret") and not self._key():
            return False, f"no API key (store secret {self.settings['secret']})"
        try:
            self.list_models()
            return True, "reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {exc.__class__.__name__}: {str(exc)[:120]}"

    def list_models(self) -> list[str]:
        ts, cached = self._models
        if cached and time.time() - ts < 300:
            return cached
        r = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=8)
        if r.status_code in (401, 403):
            raise ProviderError("authentication failed", retryable=False)
        r.raise_for_status()
        names = [m.get("id", "") for m in r.json().get("data", [])]
        self._models = (time.time(), names)
        return names

    def capabilities(self, model: str) -> set[str]:
        caps = {"completion", "tools"}
        hints = self.settings.get("vision_models")
        lower = model.lower()
        if hints is not None:
            if model in hints:
                caps.add("vision")
        elif any(k in lower for k in ("gpt-4o", "gpt-4.1", "gpt-5", "vision", "vl", "gemini", "claude")):
            caps.add("vision")
        if "embed" in lower:
            caps = {"embedding"}
        return caps

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""),
                            "content": m.get("content") or ""})
                continue
            if m.get("images"):
                parts: list[dict[str, Any]] = [{"type": "text", "text": m.get("content") or ""}]
                for img in m["images"]:
                    parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}})
                out.append({"role": role, "content": parts})
                continue
            msg: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
            if role == "assistant" and m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                    for tc in m["tool_calls"]
                ]
            out.append(msg)
        return out

    def chat(self, messages: list[dict[str, Any]], model: str, tools: list[ToolSpec] | None = None,
             on_text: StreamCallback | None = None, cancel: CancelToken | None = None,
             timeout: float = 120.0, max_tokens: int = 2048, temperature: float = 0.4) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._convert(messages),
            "stream": True,
            "max_completion_tokens": max_tokens,
        }
        if self.settings.get("send_temperature", False):
            body["temperature"] = temperature
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.parameters}}
                for t in tools
            ]
        text_parts: list[str] = []
        pending: dict[int, dict[str, Any]] = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions", json=body,
                              headers=self._headers(), timeout=httpx.Timeout(timeout, connect=8)) as r:
                if r.status_code == 429:
                    retry = float(r.headers.get("retry-after", "30") or 30)
                    raise RateLimited(f"{self.name} rate limited", retry_after=retry)
                if r.status_code == 404:
                    raise ModelUnavailable(f"{self.name}: model {model} not found")
                if r.status_code in (401, 403):
                    raise ProviderError(f"{self.name}: authentication failed", retryable=False)
                if r.status_code >= 400:
                    detail = r.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(f"{self.name} HTTP {r.status_code}: {detail}",
                                        retryable=r.status_code >= 500)
                for line in r.iter_lines():
                    if cancel is not None and cancel.cancelled:
                        raise Cancelled(cancel.reason)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
                            if on_text:
                                on_text(piece)
                        for tc in delta.get("tool_calls") or []:
                            slot = pending.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{self.name} timed out after {timeout}s") from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"{self.name} connection failed: {exc}") from exc
        calls = [
            ToolCall(name=s["name"], arguments=parse_arguments(s["args"]), **({"id": s["id"]} if s["id"] else {}))
            for _, s in sorted(pending.items())
        ]
        return ChatResult(text="".join(text_parts).strip(), tool_calls=calls, provider=self.name, model=model)

    def embed(self, texts: list[str], model: str, timeout: float = 30.0) -> list[list[float]]:
        r = httpx.post(f"{self.base_url}/embeddings", json={"model": model, "input": texts},
                       headers=self._headers(), timeout=timeout)
        if r.status_code == 404:
            raise ModelUnavailable(f"{self.name}: embedding model {model} not found")
        r.raise_for_status()
        return [d["embedding"] for d in r.json().get("data", [])]
