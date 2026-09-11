import time

from tokenmax.cache import PromptCache
from tokenmax.models import Completion, Usage


def _completion(text: str = "answer") -> Completion:
    return Completion(content=text, usage=Usage(prompt_tokens=120, completion_tokens=30))


def test_miss_then_hit_counts_savings():
    cache = PromptCache()
    key = cache.key(["user:what is pricing"])

    assert cache.get(key) is None
    cache.put(key, _completion())
    hit = cache.get(key)

    assert hit is not None and hit.cached is True
    assert cache.stats.hits == 1 and cache.stats.misses == 1
    assert cache.stats.tokens_saved == 150
    assert cache.stats.hit_rate == 0.5


def test_key_normalisation_merges_equivalent_prompts():
    cache = PromptCache()
    assert cache.key(["  What Is   PRICING "]) == cache.key(["what is pricing"])


def test_key_separates_different_prompts():
    cache = PromptCache()
    assert cache.key(["pricing"]) != cache.key(["latency"])


def test_field_boundary_prevents_collisions():
    cache = PromptCache()
    assert cache.key(["ab", "c"]) != cache.key(["a", "bc"])


def test_expired_entry_is_evicted():
    cache = PromptCache(ttl_seconds=0)
    key = cache.key(["x"])
    cache.put(key, _completion())
    time.sleep(0.01)

    assert cache.get(key) is None
    assert cache.stats.evictions == 1


def test_capacity_eviction():
    cache = PromptCache(max_entries=2)
    for i in range(3):
        cache.put(cache.key([str(i)]), _completion())
    assert cache.stats.evictions == 1
