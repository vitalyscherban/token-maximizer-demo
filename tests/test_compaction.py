from tokenmax.context import compact
from tokenmax.models import Message
from tokenmax.providers import MockProvider
from tokenmax.tokens import TokenCounter


def _history(turns: int = 10) -> list[Message]:
    messages = [Message(role="system", content="You are an assistant.", pinned=True)]
    for i in range(turns):
        messages.append(Message(role="user", content=f"Question {i} about topic {i} " * 20))
        messages.append(Message(role="assistant", content=f"Answer {i} with detail " * 20))
    return messages


async def test_compaction_is_noop_under_threshold(counter: TokenCounter):
    provider = MockProvider(counter=counter)
    messages = _history(1)
    result = await compact(messages, provider, counter, threshold=100_000)
    assert result.compacted_count == 0
    assert result.messages == messages


async def test_compaction_replaces_old_turns(counter: TokenCounter):
    provider = MockProvider(counter=counter)
    messages = _history()
    result = await compact(messages, provider, counter, threshold=200, protected_recent=4)

    assert result.compacted_count > 0
    assert result.tokens_after < result.tokens_before
    assert result.tokens_saved > 0
    assert any(m.compacted for m in result.messages)


async def test_compaction_preserves_pinned_head_and_recent_tail(counter: TokenCounter):
    provider = MockProvider(counter=counter)
    messages = _history()
    result = await compact(messages, provider, counter, threshold=200, protected_recent=4)

    assert result.messages[0].id == messages[0].id
    assert [m.id for m in result.messages[-4:]] == [m.id for m in messages[-4:]]


async def test_compaction_does_not_resummarize_a_lone_memo(counter: TokenCounter):
    provider = MockProvider(counter=counter)
    messages = [
        Message(role="system", content="sys", pinned=True),
        Message(role="system", content="[compacted memory] notes", compacted=True),
        *[Message(role="user", content="recent " * 10) for _ in range(4)],
    ]
    result = await compact(messages, provider, counter, threshold=1, protected_recent=4)
    assert result.compacted_count == 0
