from tokenmax.models import Message, ToolCall
from tokenmax.tokens import MESSAGE_OVERHEAD_TOKENS, TokenCounter


def test_empty_text_is_free(counter: TokenCounter):
    assert counter.count_text("") == 0


def test_count_scales_with_length(counter: TokenCounter):
    short = counter.count_text("pricing")
    long = counter.count_text("pricing " * 50)
    assert 0 < short < long


def test_message_includes_framing_overhead(counter: TokenCounter):
    message = Message(role="user", content="hello world")
    assert counter.count_message(message) == MESSAGE_OVERHEAD_TOKENS + counter.count_text(
        "hello world"
    )


def test_tool_call_arguments_are_counted(counter: TokenCounter):
    plain = Message(role="assistant", content="")
    with_call = Message(
        role="assistant", tool_call=ToolCall(name="calculator", arguments={"expression": "2+2"})
    )
    assert counter.count_message(with_call) > counter.count_message(plain)


def test_truncation_is_noop_under_budget(counter: TokenCounter):
    text, truncated = counter.truncate_to_tokens("short text", 100)
    assert text == "short text" and truncated is False


def test_truncation_shrinks_and_marks(counter: TokenCounter):
    original = "alpha beta gamma delta " * 200
    text, truncated = counter.truncate_to_tokens(original, 40)
    assert truncated is True
    assert "[truncated]" in text
    assert counter.count_text(text) < counter.count_text(original)


def test_truncation_keeps_head_and_tail(counter: TokenCounter):
    original = "HEADMARK " + ("filler " * 300) + "TAILMARK"
    text, _ = counter.truncate_to_tokens(original, 60)
    assert "HEADMARK" in text and "TAILMARK" in text


def test_backend_is_reported(counter: TokenCounter):
    assert counter.backend in {"tiktoken", "heuristic"}
