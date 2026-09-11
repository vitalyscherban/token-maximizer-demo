from __future__ import annotations

from ..config import Settings
from ..tokens import TokenCounter
from .base import LLMProvider
from .mock import MockProvider
from .openai_provider import OpenAIProvider


def build_provider(settings: Settings, counter: TokenCounter) -> LLMProvider:
    if settings.provider == "openai":
        if not settings.api_key:
            raise ValueError("TOKENMAX_API_KEY is required when TOKENMAX_PROVIDER=openai")
        return OpenAIProvider(
            api_key=settings.api_key,
            model=settings.model,
            base_url=settings.base_url or "https://api.openai.com/v1",
        )
    if settings.provider == "mock":
        return MockProvider(counter=counter, model=settings.model)
    raise ValueError(f"Unknown provider: {settings.provider!r}")
