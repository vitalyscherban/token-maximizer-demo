from tokenmax.context import prune, relevance, tokenize
from tokenmax.models import Message, ToolCall
from tokenmax.tokens import TokenCounter


def _history() -> list[Message]:
    messages = [Message(role="system", content="You are an assistant.", pinned=True)]
    for topic in ["weather", "sports", "recipes", "travel", "movies"]:
        messages.append(Message(role="user", content=f"Tell me about {topic} " * 30))
        messages.append(Message(role="assistant", content=f"Here is {topic} info " * 30))
    messages.append(Message(role="user", content="What are the pricing tiers?", pinned=True))
    return messages


def test_tokenize_drops_stopwords():
    assert "the" not in tokenize("the pricing model")
    assert "pricing" in tokenize("the pricing model")


def test_relevance_prefers_overlap():
    terms = tokenize("pricing tiers")
    on_topic = Message(role="user", content="pricing tiers explained")
    off_topic = Message(role="user", content="weather forecast tomorrow")
    assert relevance(on_topic, terms) > relevance(off_topic, terms)


def test_prune_is_noop_when_under_budget(counter: TokenCounter):
    messages = [Message(role="user", content="hi")]
    result = prune(messages, counter, budget=10_000, query="hi")
    assert result.kept == messages and not result.dropped


def test_prune_fits_budget_and_keeps_pinned(counter: TokenCounter):
    messages = _history()
    full = counter.count_messages(messages)
    result = prune(messages, counter, budget=full // 3, query="pricing tiers", protected_recent=2)

    assert result.dropped
    assert result.tokens_after <= result.tokens_before
    assert result.tokens_saved > 0
    kept_ids = {m.id for m in result.kept}
    for message in messages:
        if message.pinned or message.role == "system":
            assert message.id in kept_ids


def test_prune_keeps_recent_window(counter: TokenCounter):
    messages = _history()
    result = prune(messages, counter, budget=200, query="pricing", protected_recent=3)
    kept_ids = {m.id for m in result.kept}
    for message in messages[-3:]:
        assert message.id in kept_ids


def test_prune_never_orphans_a_tool_result(counter: TokenCounter):
    call = ToolCall(name="search_kb", arguments={"query": "x"})
    messages = [
        Message(role="system", content="sys", pinned=True),
        *[Message(role="user", content="noise " * 60) for _ in range(6)],
        Message(role="assistant", tool_call=call),
        Message(role="tool", name="search_kb", content="result " * 60, tool_call_id=call.id),
        Message(role="user", content="and now?", pinned=True),
    ]
    result = prune(messages, counter, budget=150, query="and now", protected_recent=1)
    kept_call_ids = {m.tool_call.id for m in result.kept if m.tool_call}
    for message in result.kept:
        if message.role == "tool":
            assert message.tool_call_id in kept_call_ids
