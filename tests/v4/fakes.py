"""Test doubles shared by the v4 tests."""

from __future__ import annotations

from typing import Any

from jarvis.providers.base import ChatResult, Provider, ProviderError, ToolCall


class FakeProvider(Provider):
    """Replays scripted responses. Each script item is a ChatResult, an Exception, or a callable(messages)."""

    type = "fake"
    scripts: dict[str, list[Any]] = {}
    calls: list[dict[str, Any]] = []
    caps: dict[str, set[str]] = {}
    healthy: dict[str, bool] = {}

    def available(self) -> tuple[bool, str]:
        ok = FakeProvider.healthy.get(self.name, True)
        return ok, "fake ok" if ok else "fake down"

    def list_models(self) -> list[str]:
        return list(FakeProvider.caps)

    def capabilities(self, model: str) -> set[str]:
        return FakeProvider.caps.get(model, {"completion", "tools"})

    def chat(self, messages, model, tools=None, on_text=None, cancel=None, timeout=120.0, max_tokens=2048,
             temperature=0.4) -> ChatResult:
        FakeProvider.calls.append({"provider": self.name, "model": model, "messages": messages,
                                   "tools": [t.name for t in (tools or [])]})
        script = FakeProvider.scripts.get(f"{self.name}:{model}") or FakeProvider.scripts.get(self.name) or []
        if not script:
            return ChatResult("default reply.", [], self.name, model)
        item = script.pop(0)
        if callable(item) and not isinstance(item, ChatResult):
            item = item(messages)
        if isinstance(item, Exception):
            raise item
        if on_text and item.text:
            for i in range(0, len(item.text), 7):
                on_text(item.text[i:i + 7])
        return ChatResult(item.text, list(item.tool_calls), self.name, model)

    def embed(self, texts, model, timeout=30.0):
        raise ProviderError("no embeddings in tests", retryable=False)


def reply(text: str = "", calls: list[tuple[str, dict[str, Any]]] | None = None) -> ChatResult:
    return ChatResult(text, [ToolCall(n, a) for n, a in (calls or [])], "fake", "m")
