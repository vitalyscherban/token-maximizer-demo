from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every token-efficiency knob is exposed here so a
    demo audience can toggle a single technique and watch the ledger change."""

    model_config = SettingsConfigDict(env_prefix="TOKENMAX_", env_file=".env", extra="ignore")

    provider: str = "mock"
    model: str = "mock-1"
    api_key: str | None = None
    base_url: str | None = None

    context_window: int = 8192
    reserved_output: int = 512

    # Fraction of the history budget that triggers compaction of older turns.
    compaction_trigger: float = 0.6
    # Number of most-recent turns that are never pruned or compacted.
    protected_recent_turns: int = 4

    tool_result_max_tokens: int = 256
    cache_ttl_seconds: int = 900

    # Feature switches - used by the /compare endpoint and the demo script.
    enable_pruning: bool = True
    enable_compaction: bool = True
    enable_cache: bool = True
    enable_tool_routing: bool = True
    enable_schema_slimming: bool = True
    enable_result_truncation: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
