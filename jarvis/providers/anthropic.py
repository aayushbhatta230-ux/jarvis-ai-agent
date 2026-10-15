"""Anthropic Messages API provider (Claude models)."""

from __future__ import annotations

import json
import logging
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
API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    type = "anthropic"

    def __init__(self, name: str, settings: dict[str, Any]) -> None:
        super().__init__(name, settings)
        self.base_url = self.base_url or "https://api.anthropic.com"

    def _key(self) -> str | None:
        return secrets.get(self.settings.get("secret", "ANTHROPIC_API_KEY"))

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._key() or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def available(self) -> tuple[bool, str]:
        if not self._key():
            return False, f"no API key (store secret {self.settings.get('secret', 'ANTHROPIC_API_KEY')})"
        try:
            r = httpx.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=8)
            if r.status_code in (401, 403):
                return False, "authentication failed"
            r.raise_for_status()
            return True, "reachable"
        except Exception as exc:  # noqa: BLE001
            return False, f"unreachable: {exc.__class__.__name__}"

    def list_models(self) -> list[str]:
        r = httpx.get(f"{self.base_url}/v1/models", headers=self._headers(), timeout=8)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    def capabilities(self, model: str) -> set[str]:
        return {"completion", "tools", "vision"}

    @staticmethod
    def _convert(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []

        def push(role: str, blocks: list[dict[str, Any]]) -> None:
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})

        for m in messages:
            role = m["role"]
            if role == "system":
                system_parts.append(m.get("content") or "")
                continue
            if role == "tool":
                push("user", [{"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                               "content": m.get("content") or "(no output)"}])
                continue
            blocks: list[dict[str, Any]] = []
            for img in m.get("images") or []:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                           "data": img}})
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]})
            if not blocks:
                blocks = [{"type": "text", "text": "(empty)"}]
            push("assistant" if role == "assistant" else "user", blocks)
        return "\n\n".join(system_parts), out

    def chat(self, messages: list[dict[str, Any]], model: str, tools: list[ToolSpec] | None = None,
             on_text: StreamCallback | None = None, cancel: CancelToken | None = None,
             timeout: float = 120.0, max_tokens: int = 2048, temperature: float = 0.4) -> ChatResult:
        system, converted = self._convert(messages)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": converted,
            "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                             for t in tools]
        text_parts: list[str] = []
        blocks: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/v1/messages", json=body, headers=self._headers(),
                              timeout=httpx.Timeout(timeout, connect=8)) as r:
                if r.status_code == 429:
                    raise RateLimited("anthropic rate limited",
                                      retry_after=float(r.headers.get("retry-after", "30") or 30))
                if r.status_code == 404:
                    raise ModelUnavailable(f"anthropic: model {model} not found")
                if r.status_code in (401, 403):
                    raise ProviderError("anthropic: authentication failed", retryable=False)
                if r.status_code >= 400:
                    detail = r.read().decode("utf-8", "replace")[:300]
                    raise ProviderError(f"anthropic HTTP {r.status_code}: {detail}",
                                        retryable=r.status_code >= 500 or r.status_code == 529)
                for line in r.iter_lines():
                    if cancel is not None and cancel.cancelled:
                        raise Cancelled(cancel.reason)
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:].strip())
                    etype = ev.get("type")
                    if etype == "content_block_start":
                        block = ev.get("content_block", {})
                        blocks[ev["index"]] = {"type": block.get("type"), "id": block.get("id", ""),
                                               "name": block.get("name", ""), "json": ""}
                    elif etype == "content_block_delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_parts.append(delta.get("text", ""))
                            if on_text:
                                on_text(delta.get("text", ""))
                        elif delta.get("type") == "input_json_delta":
                            blocks.setdefault(ev["index"], {"type": "tool_use", "id": "", "name": "", "json": ""})
                            blocks[ev["index"]]["json"] += delta.get("partial_json", "")
                    elif etype == "message_delta":
                        usage.update(ev.get("usage", {}))
                    elif etype == "error":
                        raise ProviderError(f"anthropic stream error: {ev.get('error')}")
        except httpx.TimeoutException as exc:
            raise ProviderError(f"anthropic timed out after {timeout}s") from exc
        except httpx.TransportError as exc:
            raise ProviderError(f"anthropic connection failed: {exc}") from exc
        calls: list[ToolCall] = []
        for _, b in sorted(blocks.items()):
            if b["type"] != "tool_use":
                continue
            call = ToolCall(name=b["name"], arguments=parse_arguments(b["json"] or "{}"))
            if b["id"]:
                call.id = b["id"]
            calls.append(call)
        return ChatResult(text="".join(text_parts).strip(), tool_calls=calls, provider=self.name,
                          model=model, usage=usage)
