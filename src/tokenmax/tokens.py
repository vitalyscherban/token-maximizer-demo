from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from .models import Message

# Per-message framing overhead charged by chat APIs (role, delimiters).
MESSAGE_OVERHEAD_TOKENS = 4
# Overhead for the assistant priming sequence at the end of a prompt.
PRIMING_OVERHEAD_TOKENS = 3

_WORD_RE = re.compile(r"\w+|[^\w\s]")


@lru_cache
def _tiktoken_encoder(model: str):
    try:
        import tiktoken
    except ImportError:  # pragma: no cover - exercised only without the extra
        return None
    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:  # pragma: no cover
            return None


class TokenCounter:
    """Counts tokens with tiktoken when available, otherwise with a stable
    heuristic. The heuristic keeps the demo dependency-free and deterministic;
    both paths produce the same *relative* savings story."""

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model
        self._encoder = _tiktoken_encoder(model)

    @property
    def backend(self) -> str:
        return "tiktoken" if self._encoder else "heuristic"

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is not None:
            return len(self._encoder.encode(text))
        # ~0.75 tokens per word plus punctuation, floored at 1 for non-empty text.
        return max(1, round(len(_WORD_RE.findall(text)) * 1.3))

    def count_message(self, message: Message) -> int:
        total = MESSAGE_OVERHEAD_TOKENS + self.count_text(message.content)
        if message.name:
            total += self.count_text(message.name)
        if message.tool_call:
            total += self.count_text(message.tool_call.name)
            total += self.count_text(str(message.tool_call.arguments))
        return total

    def count_messages(self, messages: Iterable[Message]) -> int:
        return sum(self.count_message(m) for m in messages) + PRIMING_OVERHEAD_TOKENS

    def count_schema(self, schema: dict) -> int:
        return self.count_text(str(schema))

    def truncate_to_tokens(self, text: str, max_tokens: int) -> tuple[str, bool]:
        """Middle-out truncation: keeps the head and tail of a payload, which is
        where identifiers and conclusions usually live."""
        if max_tokens <= 0 or self.count_text(text) <= max_tokens:
            return text, False
        marker = "\n...[truncated]...\n"
        budget = max(1, max_tokens - self.count_text(marker))
        head_budget = budget * 2 // 3
        tail_budget = budget - head_budget
        head = self._slice(text, head_budget, from_start=True)
        tail = self._slice(text, tail_budget, from_start=False)
        return f"{head}{marker}{tail}", True

    def _slice(self, text: str, budget: int, *, from_start: bool) -> str:
        if self._encoder is not None:
            ids = self._encoder.encode(text)
            chunk = ids[:budget] if from_start else ids[-budget:]
            return self._encoder.decode(chunk)
        approx_chars = max(1, int(budget / 1.3 * 5))
        return text[:approx_chars] if from_start else text[-approx_chars:]
