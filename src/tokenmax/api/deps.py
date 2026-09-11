from __future__ import annotations

from functools import lru_cache

from ..agent import TokenEfficientAgent
from ..config import get_settings


@lru_cache
def get_agent() -> TokenEfficientAgent:
    """Process-wide agent singleton so the store, cache and ledger accumulate
    across requests - a demo of cold vs. warm cost needs warm state."""
    return TokenEfficientAgent(settings=get_settings())
