"""Ollama provider (local inference) using the native HTTP API."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.providers.base import (
    ChatResult,
    ModelUnavailable,
    Provider,
    ProviderError,
    StreamCallback,
    ToolCall,
    ToolSpec,
    parse_arguments,
)

log = logging.getLogger(__name__)
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)


class OllamaProvider(Provider):
    type = "ollama"

    def __init__(self, name: str, settings: dict[str, Any]) -> None:
        super().__init__(name, settings)
        self.base_url = self.base_url or "http://127.0.0.1:11434"
        self.keep_alive = settings.get("keep_alive", "20m")
        self._caps: dict[str, set[str]] = {}
        self._models: tuple[float, list[str]] = (0.0, [])

    def available(self) -> tuple[bool, str]:
        try:
            r = httpx.get(f"{self.base_url}/api/version", timeout=3)
            r.raise_for_status()
            return True, f"ollama {r.json().get('version', '?')}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Ollama not reachable at {self.base_url}: {exc.__class__.__name__}"

    def list_models(self) -> list[str]:
        ts, cached = self._models
        if cached and time.time() - ts < 30:
            return cached
        r = httpx.get(f"{self.base_url}/api/tags", timeout=5)
        r.raise_for_status()
        names = [m["name"] for m in r.json().get("models", [])]
        self._models = (time.time(), names)
        return names

    def has_model(self, model: str) -> bool:
        names = self.list_models()
        return model in names or f"{model}:latest" in names

    def capabilities(self, model: str) -> set[str]:
        if model in self._caps:
            return self._caps[model]
        r = httpx.post(f"{self.base_url}/api/show", json={"model": model}, timeout=10)
        if r.status_code == 404:
            raise ModelUnavailable(f"model {model} is not installed in Ollama")
        r.raise_for_status()
        data = r.json()
        caps = set(data.get("capabilities") or [])
        if not caps:  # older Ollama: infer from metadata
            caps = {"completion"}
            if "embed" in model or "bert" in json.dumps(data.get("details", {})):
                caps = {"embedding"}
            if data.get("projector_info"):
                caps.add("vision")
            if ".Tools" in (data.get("template") or ""):
                caps.add("tools")
        self._caps[model] = caps
        return caps

    def running(self) -> list[dict[str, Any]]:
        r = httpx.get(f"{self.base_url}/api/ps", timeout=5)
        r.raise_for_status()
        return r.json().get("models", [])

    # ------------------------------------------------------------------ chat
    def _convert(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            msg: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
            if m.get("images"):
                msg["images"] = m["images"]
            if role == "assistant" and m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in m["tool_calls"]
                ]
            if role == "tool":
                msg["tool_name"] = m.get("name", "")
            out.append(msg)
        return out

    def chat(self, messages: list[dict[str, Any]], model: str, tools: list[ToolSpec] | None = None,
             on_text: StreamCallback | None = None, cancel: CancelToken | None = None,
             timeout: float = 120.0, max_tokens: int = 2048, temperature: float = 0.4) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._convert(messages),
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": 8192},
        }
        try:
            caps = self.capabilities(model)
        except ModelUnavailable:
            raise
        except Exception:  # noqa: BLE001 - capability probing is best effort
            caps = set()
        if "thinking" in caps:
            body["think"] = False  # direct answers; keeps voice latency low
        if tools:
            if caps and "tools" not in caps:
                raise ModelUnavailable(f"{model} does not support tool calling")
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description,
                                                   "parameters": t.parameters}}
                for t in tools
            ]
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        usage: dict[str, Any] = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/api/chat", json=body,
                              timeout=httpx.Timeout(timeout, connect=5)) as r:
                if r.status_code == 404:
                    raise ModelUnavailable(f"model {model} not found in Ollama")
                if r.status_code >= 400:
                    detail = r.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(f"Ollama HTTP {r.status_code}: {detail}",
                                        retryable=r.status_code >= 500)
                for line in r.iter_lines():
                    if cancel is not None and cancel.cancelled:
                        raise Cancelled(cancel.reason)
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ProviderError(f"Ollama: {chunk['error']}")
                    msg = chunk.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece:
                        text_parts.append(piece)
                        if on_text:
                            on_text(piece)
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function", {})
                        calls.append(ToolCall(name=fn.get("name", ""), arguments=parse_arguments(fn.get("arguments"))))
                    if chunk.get("done"):
                        usage = {k: chunk.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration")}
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Ollama timed out after {timeout}s") from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"Ollama connection failed: {exc}") from exc
        text = _THINK_BLOCK.sub("", "".join(text_parts)).strip()
        return ChatResult(text=text, tool_calls=calls, provider=self.name, model=model, usage=usage)

    def embed(self, texts: list[str], model: str, timeout: float = 30.0) -> list[list[float]]:
        try:
            r = httpx.post(f"{self.base_url}/api/embed", json={"model": model, "input": texts,
                                                               "keep_alive": self.keep_alive},
                           timeout=timeout)
        except httpx.TransportError as exc:
            raise ProviderError(f"Ollama embed failed: {exc}") from exc
        if r.status_code == 404:
            raise ModelUnavailable(f"embedding model {model} not installed")
        r.raise_for_status()
        return r.json().get("embeddings", [])
