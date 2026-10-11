"""Memory tools: let the agent remember, recall and forget."""

from __future__ import annotations

from typing import Any

from jarvis.memory.store import KINDS
from jarvis.tools.registry import ToolContext, ToolError, tool


@tool("remember", "Store a durable memory about the user, their projects, preferences, decisions, routines or "
      "a fix that worked. Store facts that will help later; never store secrets or passwords.",
      {"content": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
       "project": {"type": "string", "description": "Project path if project-specific"},
       "importance": {"type": "number", "description": "0..1; preferences the user stated explicitly ~0.8"}},
      required=["content"], category="read", group="memory")
def remember(ctx: ToolContext, content: str, kind: str = "fact", project: str = "", importance: float = 0.5) -> dict[str, Any]:
    from jarvis.core.logging_setup import redact

    if redact(content) != content:
        raise ToolError("that looks like a secret; store credentials with the secrets manager instead")
    return ctx.app.memory.add(content, kind=kind, project=project, importance=max(0.0, min(1.0, importance)),
                              source="agent")


@tool("recall", "Search long-term memory for relevant facts, preferences, project details or past fixes.",
      {"query": {"type": "string"}, "kind": {"type": "string"}, "limit": {"type": "integer"}},
      required=["query"], group="memory")
def recall(ctx: ToolContext, query: str, kind: str = "", limit: int = 8) -> list[dict[str, Any]]:
    rows = ctx.app.memory.search(query, k=limit, kinds=[kind] if kind else None, min_score=0.15)
    return [{"id": r["id"], "kind": r["kind"], "content": r["content"], "score": r["score"]} for r in rows]


@tool("forget", "Delete a memory by id (use recall to find the id).", {"id": {"type": "integer"}},
      required=["id"], category="write", group="memory")
def forget(ctx: ToolContext, id: int) -> str:  # noqa: A002
    if not ctx.app.memory.delete(id):
        raise ToolError(f"no memory with id {id}")
    return f"memory {id} deleted"


@tool("task_history", "Look up previous JARVIS tasks (goals, outcomes, errors) to reuse what worked.",
      {"query": {"type": "string"}, "limit": {"type": "integer"}}, group="memory")
def task_history(ctx: ToolContext, query: str = "", limit: int = 10) -> list[dict[str, Any]]:
    sql = "SELECT id, goal, status, result, error, updated FROM tasks"
    params: tuple[Any, ...] = ()
    if query:
        sql += " WHERE goal LIKE ? OR result LIKE ?"
        params = (f"%{query}%", f"%{query}%")
    sql += " ORDER BY updated DESC LIMIT ?"
    rows = ctx.app.db.query(sql, (*params, limit))
    for r in rows:
        r["result"] = (r["result"] or "")[:400]
    return rows
