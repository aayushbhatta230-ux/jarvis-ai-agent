"""Provider-neutral chat types and the provider interface.

Internal message format (a plain dict per message):

* ``{"role": "system" | "user" | "assistant", "content": str}``
* user messages may carry ``"images": [base64_png, ...]``
* assistant messages may carry ``"tool_calls": [ToolCall-as-dict, ...]``
* tool results: ``{"role": "tool", "tool_call_id": str, "name": str, "content": str}``

Each provider converts to and from its own wire format.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.cancel import CancelToken

StreamCallback = Callable[[str], None]


class ProviderError(Exception):
    """A provider call failed. ``retryable`` hints whether a retry may help."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class RateLimited(ProviderError):
    def __init__(self, message: str, retry_after: float = 30.0) -> None:
        super().__init__(message, retryable=True)
        self.retry_after = retry_after


class ModelUnavailable(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: "call_" + uuid.uuid4().hex[:12])

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            msg["tool_calls"] = [tc.as_dict() for tc in self.tool_calls]
        return msg


@dataclass
class ToolSpec:
    """JSON-schema description of a tool, as offered to a model."""

    name: str
    description: str
    parameters: dict[str, Any]


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except (TypeError, json.JSONDecodeError):
        return {"_raw": str(raw)}


class Provider:
    """Base class for model providers."""

    type: str = "base"

    def __init__(self, name: str, settings: dict[str, Any]) -> None:
        self.name = name
        self.settings = settings
        self.base_url = str(settings.get("base_url", "")).rstrip("/")

    # Implementations override the following.
    def available(self) -> tuple[bool, str]:
        """Cheap readiness check: configured, reachable, authenticated."""
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def capabilities(self, model: str) -> set[str]:
        """Return a subset of {"completion", "tools", "vision", "embedding", "thinking"}."""
        raise NotImplementedError

    def chat(self, messages: list[dict[str, Any]], model: str, tools: list[ToolSpec] | None = None,
             on_text: StreamCallback | None = None, cancel: CancelToken | None = None,
             timeout: float = 120.0, max_tokens: int = 2048, temperature: float = 0.4) -> ChatResult:
        raise NotImplementedError

    def embed(self, texts: list[str], model: str, timeout: float = 30.0) -> list[list[float]]:
        raise ModelUnavailable(f"{self.name} does not provide embeddings")
