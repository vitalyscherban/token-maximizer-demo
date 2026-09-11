from __future__ import annotations

from dataclasses import dataclass

from ..models import Message, Usage
from ..providers.base import LLMProvider
from ..tokens import TokenCounter


@dataclass
class CompactionResult:
    messages: list[Message]
    compacted_count: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    usage: Usage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage is None:
            self.usage = Usage()

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


async def compact(
    messages: list[Message],
    provider: LLMProvider,
    counter: TokenCounter,
    threshold: int,
    protected_recent: int = 4,
) -> CompactionResult:
    """Replace the oldest unprotected turns with one dense memo.

    Compaction is lossy but cheap and it runs *before* pruning, so the agent
    trades a large block of verbatim history for a small block of durable facts
    instead of losing that history outright.
    """
    tokens_before = counter.count_messages(messages)
    if tokens_before <= threshold:
        return CompactionResult(messages=list(messages), tokens_before=tokens_before,
                                tokens_after=tokens_before)

    head = [m for m in messages if m.role == "system" or m.pinned]
    body = [m for m in messages if m not in head]
    if len(body) <= protected_recent:
        return CompactionResult(messages=list(messages), tokens_before=tokens_before,
                                tokens_after=tokens_before)

    stale, recent = body[:-protected_recent], body[-protected_recent:]
    # Do not re-summarise an existing memo on its own; nothing would be gained.
    if all(m.compacted for m in stale):
        return CompactionResult(messages=list(messages), tokens_before=tokens_before,
                                tokens_after=tokens_before)

    transcript = "\n".join(f"{m.role}: {m.content}" for m in stale if m.content)
    completion = await provider.summarize(transcript)
    memo = Message(
        role="system",
        content=f"[compacted memory of {len(stale)} earlier turns] {completion.content}",
        compacted=True,
    )

    merged = [*head, memo, *recent]
    return CompactionResult(
        messages=merged,
        compacted_count=len(stale),
        tokens_before=tokens_before,
        tokens_after=counter.count_messages(merged),
        usage=completion.usage,
    )
