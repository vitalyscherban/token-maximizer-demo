from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models import Message
from ..tokens import TokenCounter

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "are", "you", "our", "your", "what", "which", "how", "about", "into", "then",
}


@dataclass
class PruneResult:
    kept: list[Message]
    dropped: list[Message] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)


def tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)} - _STOPWORDS


def relevance(message: Message, query_terms: set[str]) -> float:
    """Jaccard-style overlap between a message and the active query."""
    if not query_terms:
        return 0.0
    terms = tokenize(message.content)
    if not terms:
        return 0.0
    overlap = len(terms & query_terms)
    return overlap / math.sqrt(len(terms) + len(query_terms))


def _groups(messages: list[Message]) -> list[list[Message]]:
    """Group each assistant tool call with its tool result.

    Providers reject a tool result whose originating call is missing, so pruning
    must treat the pair as one atomic unit.
    """
    groups: list[list[Message]] = []
    by_call_id: dict[str, list[Message]] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_call:
            group = [message]
            groups.append(group)
            by_call_id[message.tool_call.id] = group
        elif message.role == "tool" and message.tool_call_id in by_call_id:
            by_call_id[message.tool_call_id].append(message)
        else:
            groups.append([message])
    return groups


def prune(
    messages: list[Message],
    counter: TokenCounter,
    budget: int,
    query: str,
    protected_recent: int = 4,
) -> PruneResult:
    """Drop the least useful history until it fits ``budget``.

    Retention order: pinned content, then the protected recent window, then
    everything else ranked by ``relevance + recency``.
    """
    groups = _groups(messages)
    tokens_before = counter.count_messages(messages)
    if tokens_before <= budget:
        return PruneResult(
            kept=list(messages), tokens_before=tokens_before, tokens_after=tokens_before
        )

    query_terms = tokenize(query)
    total = len(groups)
    scored: list[tuple[float, int, list[Message]]] = []
    for index, group in enumerate(groups):
        head = group[0]
        protected = head.pinned or head.role == "system" or index >= total - protected_recent
        if protected:
            score = math.inf
        else:
            recency = (index + 1) / total
            score = max(relevance(m, query_terms) for m in group) + recency * 0.5
            if head.compacted:
                score += 0.4  # memos are already cheap; prefer keeping them
        scored.append((score, index, group))

    survivors = {index for _, index, _ in scored}
    ordered = sorted(scored, key=lambda item: item[1])

    def survivor_tokens() -> int:
        return counter.count_messages([m for _, i, g in ordered if i in survivors for m in g])

    # Drop cheapest-value groups first, re-measuring after each removal.
    for score, index, _group in sorted(scored, key=lambda item: (item[0], item[1])):
        if math.isinf(score) or survivor_tokens() <= budget:
            break
        survivors.discard(index)

    kept: list[Message] = []
    dropped: list[Message] = []
    for _, index, group in ordered:
        (kept if index in survivors else dropped).extend(group)

    return PruneResult(
        kept=kept,
        dropped=dropped,
        tokens_before=tokens_before,
        tokens_after=counter.count_messages(kept),
    )
