from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Handler = Callable[..., Any | Awaitable[Any]]

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    keywords: set[str] = field(default_factory=set)

    def schema(self, compact: bool = False) -> dict[str, Any]:
        """Full JSON schema, or a slimmed variant.

        The compact form keeps names, types and required flags but drops prose
        descriptions. Capable models rarely need them, and on a 12-tool agent
        this alone removes hundreds of prompt tokens on *every* call.
        """
        if not compact:
            return {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        properties = {
            key: {"type": value.get("type", "string")}
            for key, value in self.parameters.get("properties", {}).items()
        }
        return {
            "name": self.name,
            "description": self.description.split(".")[0],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": self.parameters.get("required", []),
            },
        }

    async def invoke(self, arguments: dict[str, Any]) -> str:
        result = self.handler(**arguments)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) else str(result)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def route(self, query: str, enabled: bool = True, limit: int = 3) -> list[Tool]:
        """Select the tools worth exposing for this query.

        Sending every schema on every turn is the most common silent token leak
        in agent systems. When routing finds no signal it falls back to the full
        set, so recall is never traded for savings.
        """
        if not enabled:
            return self.all()
        terms = {w.lower() for w in _WORD_RE.findall(query)}
        digits = any(ch.isdigit() for ch in query)

        scored: list[tuple[int, Tool]] = []
        for tool in self._tools.values():
            score = len(terms & tool.keywords)
            if tool.name.lower() in terms:
                score += 2
            if digits and "numeric" in tool.keywords:
                score += 1
            if score:
                scored.append((score, tool))

        if not scored:
            return self.all()
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [tool for _, tool in scored[:limit]]

    def schemas(self, tools: list[Tool], compact: bool = False) -> list[dict[str, Any]]:
        return [tool.schema(compact=compact) for tool in tools]
