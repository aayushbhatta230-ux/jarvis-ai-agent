"""Tool registry: discoverable, schema-validated, permission-checked tools.

A tool is a plain function decorated with :func:`tool`. The decorator
records its JSON-schema parameters, permission category and timeout. The
:class:`ToolRegistry` validates arguments, asks the permission manager,
runs the function with a timeout, audits the call and returns a uniform
:class:`ToolResult` that the agent can reason about.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jarvis.core.cancel import Cancelled, CancelToken
from jarvis.core.logging_setup import redact
from jarvis.core.permissions import PermissionDenied, most_severe
from jarvis.providers.base import ToolSpec

if TYPE_CHECKING:
    from jarvis.app import Jarvis

log = logging.getLogger(__name__)
MAX_OUTPUT_CHARS = 12_000


class ToolError(Exception):
    """A tool failed in an expected way; the message is shown to the model."""


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    images: list[str] = field(default_factory=list)  # base64 PNGs for vision models
    duration: float = 0.0

    def for_model(self) -> str:
        if self.ok:
            text = self.output or json.dumps(self.data, default=str)[:MAX_OUTPUT_CHARS] or "(done)"
        else:
            text = f"ERROR: {self.error}"
            if self.output:
                text += f"\n{self.output}"
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n... [truncated {len(text) - MAX_OUTPUT_CHARS} chars]"
        return text


@dataclass
class ToolContext:
    app: Jarvis
    cancel: CancelToken
    task_id: str | None = None

    def check(self) -> None:
        self.cancel.check()

    def progress(self, message: str) -> None:
        self.app.events.publish("tool.progress", task_id=self.task_id, message=message)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    category: str
    func: Callable[..., Any]
    timeout: float = 60.0
    group: str = "general"
    escalate: Callable[[dict[str, Any]], str] | None = None
    summarize: Callable[[dict[str, Any]], str] | None = None

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


_DECLARED: list[Tool] = []


def tool(name: str, description: str, params: dict[str, Any] | None = None, required: list[str] | None = None,
         category: str = "read", timeout: float = 60.0, group: str = "general",
         escalate: Callable[[dict[str, Any]], str] | None = None,
         summarize: Callable[[dict[str, Any]], str] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a tool. ``params`` maps argument name -> JSON-schema fragment."""

    schema = {"type": "object", "properties": params or {}, "required": required or []}

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        _DECLARED.append(Tool(name, description, schema, category, fn, timeout, group, escalate, summarize))
        return fn

    return deco


_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Validate and lightly coerce arguments against a tool schema."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be a JSON object")
    props: dict[str, Any] = schema.get("properties", {})
    clean: dict[str, Any] = {}
    for key in schema.get("required", []):
        if key not in args or args[key] in (None, ""):
            raise ToolError(f"missing required argument '{key}'")
    for key, value in args.items():
        if key not in props:
            continue  # ignore hallucinated extras instead of failing the step
        spec = props[key]
        expected = spec.get("type")
        if expected and value is not None:
            if expected == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
                value = int(value)
            elif expected == "number" and isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    pass
            elif expected == "boolean" and isinstance(value, str):
                value = value.strip().lower() in ("true", "1", "yes", "y")
            elif expected == "array" and isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
            py = _TYPES.get(expected)
            wrong_type = py is not None and not isinstance(value, py)
            bool_as_number = expected in ("integer", "number") and isinstance(value, bool)
            if wrong_type or bool_as_number:
                raise ToolError(f"argument '{key}' must be {expected}, got {type(value).__name__}")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolError(f"argument '{key}' must be one of {spec['enum']}")
        clean[key] = value
    return clean


class ToolRegistry:
    def __init__(self, app: Jarvis) -> None:
        self.app = app
        self.tools: dict[str, Tool] = {}
        self._pool = cf.ThreadPoolExecutor(max_workers=6, thread_name_prefix="jarvis-tool")

    def load_builtin(self) -> None:
        # Importing the modules runs their @tool declarations.
        from jarvis.tools import (  # noqa: F401
            code,
            communication,
            desktop,
            files,
            live,
            memory_tools,
            productivity,
            research,
            screen,
            shell,
            system,
        )

        for t in _DECLARED:
            self.tools[t.name] = t

    def register(self, t: Tool) -> None:
        self.tools[t.name] = t

    def specs(self, groups: set[str] | None = None, names: set[str] | None = None) -> list[ToolSpec]:
        out = []
        for t in self.tools.values():
            if groups and t.group not in groups:
                continue
            if names and t.name not in names:
                continue
            out.append(t.spec())
        return out

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "group": t.group, "category": t.category, "description": t.description,
             "parameters": t.parameters}
            for t in sorted(self.tools.values(), key=lambda x: (x.group, x.name))
        ]

    def execute(self, name: str, args: dict[str, Any], cancel: CancelToken | None = None,
                task_id: str | None = None) -> ToolResult:
        start = time.time()
        cancel = cancel or CancelToken()
        t = self.tools.get(name)
        if t is None:
            close = [n for n in self.tools if name.split("_")[0] in n][:5]
            return ToolResult(False, error=f"unknown tool '{name}'. Similar: {', '.join(close) or 'none'}")
        try:
            clean = validate(t.parameters, args or {})
        except ToolError as exc:
            return ToolResult(False, error=f"invalid arguments for {name}: {exc}")
        category = t.category
        if t.escalate:
            try:
                category = most_severe(category, t.escalate(clean))
            except Exception:  # noqa: BLE001
                log.exception("escalation check failed for %s", name)
        summary = t.summarize(clean) if t.summarize else f"{name} {json.dumps(clean, default=str)[:300]}"
        perms = self.app.permissions
        self.app.events.publish("tool.start", task_id=task_id, tool=name, args=redact(json.dumps(clean, default=str))[:500],
                                category=category)
        try:
            perms.authorize(name, category, summary, task_id=task_id, args=clean)
        except PermissionDenied as exc:
            result = ToolResult(False, error=str(exc), duration=time.time() - start)
            self.app.events.publish("tool.end", task_id=task_id, tool=name, ok=False, error=result.error)
            return result
        ctx = ToolContext(self.app, cancel, task_id)
        future = self._pool.submit(self._invoke, t, ctx, clean)
        try:
            result = future.result(timeout=t.timeout)
        except cf.TimeoutError:
            cancel.cancel(f"{name} timed out")
            result = ToolResult(False, error=f"{name} timed out after {t.timeout:.0f}s")
        result.duration = time.time() - start
        perms.audit(name, category, clean, "allowed", result.ok, (result.output or result.error)[:500], task_id)
        self.app.events.publish("tool.end", task_id=task_id, tool=name, ok=result.ok,
                                error=result.error[:300], duration=round(result.duration, 2))
        return result

    @staticmethod
    def _invoke(t: Tool, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            value = t.func(ctx, **args)
        except Cancelled as exc:
            return ToolResult(False, error=f"cancelled: {exc}")
        except (ToolError, PermissionDenied) as exc:
            return ToolResult(False, error=str(exc))
        except FileNotFoundError as exc:
            return ToolResult(False, error=f"not found: {exc.filename or exc}")
        except PermissionError as exc:
            return ToolResult(False, error=f"OS permission error: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected failures to the agent
            log.exception("tool %s crashed", t.name)
            return ToolResult(False, error=f"{exc.__class__.__name__}: {exc}")
        if isinstance(value, ToolResult):
            return value
        if isinstance(value, str):
            return ToolResult(True, output=value)
        if isinstance(value, dict):
            return ToolResult(True, output=json.dumps(value, indent=1, default=str, ensure_ascii=False), data=value)
        if isinstance(value, list):
            return ToolResult(True, output=json.dumps(value, indent=1, default=str, ensure_ascii=False),
                              data={"items": value})
        return ToolResult(True, output=str(value) if value is not None else "done")

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
