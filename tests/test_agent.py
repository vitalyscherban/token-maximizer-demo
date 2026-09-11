from tokenmax.agent import TokenEfficientAgent
from tokenmax.config import Settings

SCENARIO = [
    "What are your pricing tiers?",
    "What is the latency profile?",
    "How do you handle security and retention?",
    "Walk me through onboarding.",
    "What are your pricing tiers?",
]


async def _replay(agent: TokenEfficientAgent, session: str = "s") -> int:
    for message in SCENARIO:
        await agent.run(session, message)
    return agent.ledger.report(session)["optimized_tokens"]


async def test_agent_answers_and_uses_tools(agent: TokenEfficientAgent):
    response = await agent.run("s1", "What are your pricing tiers?")
    assert response.answer
    assert any(step.kind == "tool" for step in response.steps)
    assert "Starter" in response.answer


async def test_agent_reports_savings_against_baseline(agent: TokenEfficientAgent):
    await agent.run("s2", "What are your pricing tiers?")
    response = await agent.run("s2", "What is the latency profile?")
    assert response.baseline_tokens > 0
    assert response.optimized_tokens <= response.baseline_tokens


async def test_optimized_agent_beats_naive(optimized_settings: Settings, naive_settings: Settings):
    optimized = await _replay(TokenEfficientAgent(settings=optimized_settings))
    naive = await _replay(TokenEfficientAgent(settings=naive_settings))
    assert optimized < naive


async def test_repeat_question_hits_cache(agent: TokenEfficientAgent):
    first = await agent.run("s3", "What are your pricing tiers?")
    second = await agent.run("s4", "what are YOUR   Pricing tiers?")
    assert first.optimized_tokens > 0
    assert agent.cache.stats.hits >= 1
    assert second.optimized_tokens < first.optimized_tokens


async def test_full_transcript_is_never_lost(agent: TokenEfficientAgent):
    for message in SCENARIO:
        await agent.run("s5", message)
    assert len(agent.transcript.history("s5")) >= len(agent.store.history("s5"))
    assert agent.transcript.history("s5")[0].role == "system"


async def test_unknown_tool_does_not_crash_loop(agent: TokenEfficientAgent):
    output, saved = await agent._run_tool("nope", {})
    assert output.startswith("error") and saved == 0


async def test_tool_result_is_truncated(agent: TokenEfficientAgent):
    agent.settings = agent.settings.model_copy(update={"tool_result_max_tokens": 10})
    output, saved = await agent._run_tool("search_kb", {"query": "pricing latency security"})
    assert saved > 0
    assert "[truncated]" in output


async def test_step_budget_is_enforced(agent: TokenEfficientAgent):
    response = await agent.run("s6", "What is 2+2 and pricing?", max_steps=1)
    assert len(response.steps) <= 2


async def test_ledger_attributes_savings(agent: TokenEfficientAgent):
    for message in SCENARIO:
        await agent.run("s7", message)
    report = agent.ledger.report("s7")
    assert report["tokens_saved"] > 0
    assert any(v["tokens_saved"] > 0 for v in report["by_technique"].values())
