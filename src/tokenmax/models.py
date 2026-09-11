from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """A single conversation entry.

    ``pinned`` marks content that must survive pruning (system rules, the active
    user goal). ``compacted`` marks a synthetic memo that replaced older turns.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    role: Role
    content: str = ""
    name: str | None = None
    tool_call: ToolCall | None = None
    tool_call_id: str | None = None
    pinned: bool = False
    compacted: bool = False
    created_at: float = Field(default_factory=time.time)

    def wire_format(self) -> dict[str, Any]:
        """Minimal provider payload - no local bookkeeping fields are sent."""
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        if self.tool_call:
            payload["tool_calls"] = [
                {
                    "id": self.tool_call.id,
                    "type": "function",
                        "function": {
                            "name": self.tool_call.name,
                            "arguments": self.tool_call.arguments,
                        },
                    }
                ]
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


class Completion(BaseModel):
    content: str = ""
    tool_call: ToolCall | None = None
    usage: Usage = Field(default_factory=Usage)
    cached: bool = False


class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    message: str
    max_steps: int = 6


class StepTrace(BaseModel):
    index: int
    kind: Literal["model", "tool"]
    detail: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached: bool = False


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    steps: list[StepTrace]
    usage: Usage
    baseline_tokens: int
    optimized_tokens: int
    tokens_saved: int
    savings_pct: float
