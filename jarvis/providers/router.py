"""Model routing: pick a working provider/model per role, with fallback.

Roles (``fast``, ``chat``, ``agent``, ``code``, ``vision``, ``embed``) map to
ordered candidate lists in config. A candidate is used only when its provider
is enabled and reachable, the model actually exists, and the model reports
the capabilities the call needs (tools, vision, embedding). Failures put a
candidate on a cooldown so a broken endpoint is not hammered; rate limits
honour ``Retry-After``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.core.config import Config
from jarvis.core.events import EventBus
from jarvis.providers.anthropic import AnthropicProvider
from jarvis.providers.base import (
    ChatResult,
    ModelUnavailable,
    Provider,
    ProviderError,
    RateLimited,
    StreamCallback,
    ToolSpec,
)
from jarvis.providers.ollama import OllamaProvider
from jarvis.providers.openai_compat import OpenAICompatProvider

log = logging.getLogger(__name__)

PROVIDER_TYPES: dict[str, type[Provider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}

ROLE_NEEDS: dict[str, set[str]] = {
    "fast": {"completion"},
    "chat": {"completion"},
    "agent": {"completion", "tools"},
    "code": {"completion", "tools"},
    "vision": {"completion", "vision"},
    "embed": {"embedding"},
}


class NoModelAvailable(ProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass
class Candidate:
    provider: str
    model: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, spec: str) -> Candidate:
        provider, _, model = spec.partition(":")
        return cls(provider.strip(), model.strip())


class ModelRouter:
    def __init__(self, config: Config, events: EventBus | None = None) -> None:
        self.config = config
        self.events = events
        self.providers: dict[str, Provider] = {}
        self._cooldown: dict[str, float] = {}
        self._health: dict[str, tuple[float, bool, str]] = {}
        self._lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        provs: dict[str, Provider] = {}
        keep_alive = self.config.get("models.keep_alive", "20m")
        for name, settings in (self.config.get("models.providers", {}) or {}).items():
            if not settings.get("enabled", True):
                continue
            cls = PROVIDER_TYPES.get(settings.get("type", name))
            if cls is None:
                log.warning("unknown provider type for %s", name)
                continue
            provs[name] = cls(name, {"keep_alive": keep_alive, **settings})
        self.providers = provs
        self._health.clear()

    # -------------------------------------------------------------- health
    def provider_ok(self, name: str, max_age: float = 60.0) -> tuple[bool, str]:
        prov = self.providers.get(name)
        if prov is None:
            return False, "disabled or not configured"
        cached = self._health.get(name)
        if cached and time.time() - cached[0] < max_age:
            return cached[1], cached[2]
        ok, detail = prov.available()
        self._health[name] = (time.time(), ok, detail)
        return ok, detail

    def candidate_ok(self, cand: Candidate, needs: set[str]) -> tuple[bool, str]:
        if time.time() < self._cooldown.get(cand.key, 0):
            return False, "cooling down after a failure"
        ok, detail = self.provider_ok(cand.provider)
        if not ok:
            return False, detail
        prov = self.providers[cand.provider]
        try:
            if isinstance(prov, OllamaProvider) and not prov.has_model(cand.model):
                return False, f"model not installed (ollama pull {cand.model})"
            if hasattr(prov, "has_model") and not isinstance(prov, OllamaProvider) and not prov.has_model(cand.model):
                return False, "model not offered by this provider"
            caps = prov.capabilities(cand.model)
        except ModelUnavailable as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"capability probe failed: {exc}"
        missing = needs - caps
        if missing:
            return False, f"missing capabilities: {', '.join(sorted(missing))}"
        return True, "ok"

    def candidates(self, role: str) -> list[Candidate]:
        override = (self.config.get("models.override") or "").strip()
        specs = list(self.config.get(f"models.roles.{role}", []) or [])
        if override and role != "embed":
            specs = [override] + [s for s in specs if s != override]
        return [Candidate.parse(s) for s in specs if ":" in s]

    def resolve(self, role: str, needs: set[str] | None = None) -> Candidate:
        needs = needs or ROLE_NEEDS.get(role, {"completion"})
        reasons = []
        for cand in self.candidates(role):
            ok, why = self.candidate_ok(cand, needs)
            if ok:
                return cand
            reasons.append(f"{cand.key}: {why}")
        raise NoModelAvailable(f"No usable model for role '{role}'. " + "; ".join(reasons))

    def status(self) -> dict[str, Any]:
        """Per-role resolution report used by diagnostics and the settings view."""
        report: dict[str, Any] = {}
        for role, needs in ROLE_NEEDS.items():
            rows = []
            for cand in self.candidates(role):
                ok, why = self.candidate_ok(cand, needs)
                rows.append({"candidate": cand.key, "ok": ok, "detail": why})
            report[role] = rows
        return report

    # ---------------------------------------------------------------- calls
    def _fail(self, cand: Candidate, exc: Exception) -> None:
        if isinstance(exc, RateLimited):
            delay = exc.retry_after
        elif isinstance(exc, ProviderError) and not exc.retryable:
            delay = 300.0
        else:
            delay = 45.0
        if "has been started" in str(exc):
            delay = 0.0  # Ollama was just auto-started: retry immediately
        self._cooldown[cand.key] = time.time() + delay
        self._health.pop(cand.provider, None)
        log.warning("model %s failed (%s); cooling down %.0fs", cand.key, exc, delay)
        if self.events:
            self.events.publish("model.fallback", candidate=cand.key, error=str(exc)[:200])

    def chat(self, role: str, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None,
             on_text: StreamCallback | None = None, cancel: CancelToken | None = None,
             max_tokens: int = 2048, temperature: float = 0.4, needs: set[str] | None = None) -> ChatResult:
        needs = set(needs or ROLE_NEEDS.get(role, {"completion"}))
        if tools:
            needs.add("tools")
        if any(m.get("images") for m in messages):
            needs.add("vision")
        timeout = float(self.config.get("models.request_timeout", 120))
        errors: list[str] = []
        emitted = False

        def guarded(piece: str) -> None:
            nonlocal emitted
            emitted = True
            if on_text:
                on_text(piece)

        for cand in self.candidates(role):
            ok, why = self.candidate_ok(cand, needs)
            if not ok:
                errors.append(f"{cand.key}: {why}")
                continue
            prov = self.providers[cand.provider]
            try:
                if self.events:
                    self.events.publish("model.call", role=role, candidate=cand.key)
                return prov.chat(messages, cand.model, tools=tools, on_text=guarded, cancel=cancel,
                                 timeout=timeout, max_tokens=max_tokens, temperature=temperature)
            except Cancelled:
                raise
            except ProviderError as exc:
                self._fail(cand, exc)
                errors.append(f"{cand.key}: {exc}")
                if emitted:  # partial output already streamed; don't splice a second model's answer
                    raise
            except Exception as exc:  # noqa: BLE001 - malformed responses, decode errors...
                self._fail(cand, exc)
                errors.append(f"{cand.key}: {exc.__class__.__name__}: {exc}")
                if emitted:
                    raise ProviderError(str(exc)) from exc
        raise NoModelAvailable(f"All models failed for role '{role}': " + " | ".join(errors))

    def embed(self, texts: list[str]) -> tuple[str, list[list[float]]]:
        errors = []
        for cand in self.candidates("embed"):
            ok, why = self.candidate_ok(cand, {"embedding"})
            if not ok:
                errors.append(f"{cand.key}: {why}")
                continue
            try:
                return cand.key, self.providers[cand.provider].embed(texts, cand.model)
            except Exception as exc:  # noqa: BLE001
                self._fail(cand, exc)
                errors.append(f"{cand.key}: {exc}")
        raise NoModelAvailable("No embedding model available: " + "; ".join(errors))
