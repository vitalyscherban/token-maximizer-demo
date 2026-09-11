from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Completion, Message


class LLMProvider(ABC):
    """Everything the agent needs from a model backend.

    Keeping this surface tiny is itself a token-efficiency decision: the agent
    owns context assembly, so no provider SDK can silently re-inflate the prompt.
    """

    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_output_tokens: int = 512,
    ) -> Completion:
        ...

    @abstractmethod
    async def summarize(self, text: str, max_output_tokens: int = 160) -> Completion:
        ...
