from __future__ import annotations

import json

import httpx

from ..models import Completion, Message, ToolCall, Usage
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible Chat Completions client (also works against Azure
    OpenAI-compatible gateways and local servers exposing the same schema)."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_output_tokens: int = 512,
    ) -> Completion:
        payload: dict = {
            "model": self.model,
            "messages": [m.wire_format() for m in messages],
            "max_tokens": max_output_tokens,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        data = await self._post(payload)
        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})

        tool_call = None
        if choice.get("tool_calls"):
            raw = choice["tool_calls"][0]
            arguments = raw["function"].get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            tool_call = ToolCall(id=raw["id"], name=raw["function"]["name"], arguments=arguments)

        return Completion(
            content=choice.get("content") or "",
            tool_call=tool_call,
            usage=Usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            ),
        )

    async def summarize(self, text: str, max_output_tokens: int = 160) -> Completion:
        instruction = (
            "Compress the conversation excerpt into terse notes. Keep decisions, "
            "identifiers, numbers and open questions. Drop pleasantries."
        )
        messages = [
            Message(role="system", content=instruction),
            Message(role="user", content=text),
        ]
        return await self.complete(messages, tools=None, max_output_tokens=max_output_tokens)

    async def _post(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions", headers=self._headers, json=payload
            )
            response.raise_for_status()
            return response.json()
