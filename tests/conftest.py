import pytest

from tokenmax.agent import TokenEfficientAgent
from tokenmax.config import Settings
from tokenmax.tokens import TokenCounter

ALL_FLAGS = {
    "enable_compaction": True,
    "enable_pruning": True,
    "enable_tool_routing": True,
    "enable_schema_slimming": True,
    "enable_result_truncation": True,
    "enable_cache": True,
}


@pytest.fixture
def counter() -> TokenCounter:
    return TokenCounter("mock-1")


@pytest.fixture
def optimized_settings() -> Settings:
    return Settings(provider="mock", model="mock-1", _env_file=None).model_copy(update=ALL_FLAGS)


@pytest.fixture
def naive_settings() -> Settings:
    return Settings(provider="mock", model="mock-1", _env_file=None).model_copy(
        update={key: False for key in ALL_FLAGS}
    )


@pytest.fixture
def agent(optimized_settings: Settings) -> TokenEfficientAgent:
    return TokenEfficientAgent(settings=optimized_settings)
