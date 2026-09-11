from tokenmax.tokens import TokenCounter
from tokenmax.tools import build_default_registry, calculator, search_kb


def test_calculator_evaluates_arithmetic():
    assert calculator("(1200*12)/4") == "3600"


def test_calculator_rejects_code_injection():
    assert calculator("__import__('os').system('echo hi')").startswith("error")


def test_calculator_handles_division_by_zero():
    assert calculator("1/0").startswith("error")


def test_search_kb_returns_grounded_text():
    assert "Starter" in search_kb("pricing tiers")


def test_search_kb_reports_no_match():
    assert search_kb("zzzz") == "no matching knowledge base entries"


def test_routing_selects_relevant_tools():
    registry = build_default_registry()
    names = {tool.name for tool in registry.route("what is your pricing?")}
    assert "search_kb" in names
    assert "current_time" not in names


def test_routing_falls_back_to_all_tools_without_signal():
    registry = build_default_registry()
    assert len(registry.route("hmm")) == len(registry.all())


def test_routing_disabled_returns_everything():
    registry = build_default_registry()
    assert len(registry.route("pricing", enabled=False)) == len(registry.all())


def test_compact_schema_is_cheaper(counter: TokenCounter):
    tool = build_default_registry().get("calculator")
    assert counter.count_schema(tool.schema(compact=True)) < counter.count_schema(tool.schema())


def test_compact_schema_keeps_contract():
    tool = build_default_registry().get("calculator")
    compact = tool.schema(compact=True)
    assert compact["name"] == "calculator"
    assert "expression" in compact["parameters"]["properties"]
    assert compact["parameters"]["required"] == ["expression"]
