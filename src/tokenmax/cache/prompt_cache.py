from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from ..models import Completion

_WS_RE = re.compile(r"\s+")


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0
    evictions: int = 0
    prompt_tokens_saved: int = 0
    completion_tokens_saved: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

    @property
    def tokens_saved(self) -> int:
        return self.prompt_tokens_saved + self.completion_tokens_saved

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stores": self.stores,
            "evictions": self.evictions,
            "hit_rate": self.hit_rate,
            "tokens_saved": self.tokens_saved,
        }


@dataclass
class _Entry:
    completion: Completion
    expires_at: float
    hits: int = 0


@dataclass
class PromptCache:
    """Normalised exact-match cache over (prompt, tools) pairs.

    Normalisation (case, whitespace, punctuation-light) turns near-identical
    phrasings into the same key, which is where most of the real-world hit rate
    comes from in support and Q&A workloads.
    """

    ttl_seconds: int = 900
    max_entries: int = 512
    stats: CacheStats = field(default_factory=CacheStats)
    _entries: dict[str, _Entry] = field(default_factory=dict)

    @staticmethod
    def normalize(text: str) -> str:
        return _WS_RE.sub(" ", text.strip().lower())

    def key(self, parts: list[str]) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(self.normalize(part).encode("utf-8"))
            digest.update(b"\x1f")
        return digest.hexdigest()

    def get(self, key: str) -> Completion | None:
        entry = self._entries.get(key)
        if entry is None:
            self.stats.misses += 1
            return None
        if entry.expires_at < time.time():
            del self._entries[key]
            self.stats.evictions += 1
            self.stats.misses += 1
            return None
        entry.hits += 1
        self.stats.hits += 1
        self.stats.prompt_tokens_saved += entry.completion.usage.prompt_tokens
        self.stats.completion_tokens_saved += entry.completion.usage.completion_tokens
        return entry.completion.model_copy(update={"cached": True})

    def put(self, key: str, completion: Completion) -> None:
        if len(self._entries) >= self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].expires_at)
            del self._entries[oldest]
            self.stats.evictions += 1
        self._entries[key] = _Entry(
            completion=completion, expires_at=time.time() + self.ttl_seconds
        )
        self.stats.stores += 1

    def clear(self) -> None:
        self._entries.clear()
