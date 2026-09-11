"""Console walkthrough of the token-efficiency pipeline.

Run with:  python demo/run_demo.py

The demo deliberately uses a small context window. A six-turn conversation then
behaves like a long production session, so compaction and pruning actually fire
instead of sitting idle behind a roomy 128k window.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tokenmax.agent import TokenEfficientAgent
from tokenmax.config import Settings

SCENARIO = [
    "What are your pricing tiers?",
    "What is the latency profile of the platform?",
    "How do you handle security and data retention?",
    "Walk me through onboarding.",
    "Describe the reference architecture.",
    "If Team is 99 per seat, what is 99*25?",
]

ALL_FLAGS = {
    "enable_compaction": True,
    "enable_pruning": True,
    "enable_tool_routing": True,
    "enable_schema_slimming": True,
    "enable_result_truncation": True,
    "enable_cache": True,
}

DEMO_PROFILE = {
    "context_window": 1200,
    "reserved_output": 200,
    "tool_result_max_tokens": 60,
    "protected_recent_turns": 4,
    # Late trigger on purpose: compaction handles the bulk, and pruning is left
    # visible as the hard backstop that guarantees the prompt fits.
    "compaction_trigger": 0.9,
}


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


async def replay(agent: TokenEfficientAgent, session: str, *, quiet: bool = False) -> dict:
    for message in SCENARIO:
        response = await agent.run(session, message)
        if not quiet:
            cached = any(step.cached for step in response.steps)
            print(
                f"  {message[:44]:<46} tokens={response.optimized_tokens:>6}"
                f"  {'<- cache hit' if cached else ''}"
            )
    report = agent.ledger.report(session)
    report["cache"] = agent.cache.stats.as_dict()
    report["working_context_messages"] = len(agent.store.history(session))
    report["full_transcript_messages"] = len(agent.transcript.history(session))
    return report


async def main_async() -> None:
    base = Settings().model_copy(update=DEMO_PROFILE)
    print(
        f"provider={base.provider}  model={base.model}  "
        f"context_window={base.context_window}  reserved_output={base.reserved_output}"
    )

    rule("1. Naive agent (every optimisation off)")
    off = dict.fromkeys(ALL_FLAGS, False)
    naive_agent = TokenEfficientAgent(settings=base.model_copy(update=off))
    naive = await replay(naive_agent, "demo")
    naive_total = naive["optimized_tokens"]

    rule("2. Ablation - each technique enabled on its own")
    print(f"  {'technique':<22}{'tokens':>9}{'saved':>9}{'share':>9}")
    for flag in ALL_FLAGS:
        agent = TokenEfficientAgent(settings=base.model_copy(update={**off, flag: True}))
        report = await replay(agent, "demo", quiet=True)
        total = report["optimized_tokens"]
        saved = max(0, naive_total - total)
        pct = round(saved / naive_total * 100, 1) if naive_total else 0.0
        print(f"  {flag.removeprefix('enable_'):<22}{total:>9}{saved:>9}{pct:>8}%")
    print(
        "  note: cache reads 0 here because each question is asked once on a "
        "cold cache.\n        Its payoff is the warm run in step 4."
    )

    rule("3. Optimized agent - everything on, cold cache")
    optimized_agent = TokenEfficientAgent(settings=base.model_copy(update=ALL_FLAGS))
    optimized = await replay(optimized_agent, "demo")

    rule("4. Same agent, new session, identical questions (warm cache)")
    warm = await replay(optimized_agent, "demo-warm")

    cold_total = optimized["optimized_tokens"]
    warm_total = warm["optimized_tokens"]
    pct = round((naive_total - cold_total) / naive_total * 100, 2) if naive_total else 0.0
    warm_pct = round((naive_total - warm_total) / naive_total * 100, 2) if naive_total else 0.0

    rule("Result")
    print(f"  naive                 : {naive_total:>7} tokens")
    print(f"  optimized (cold)      : {cold_total:>7} tokens   ({pct}% saved)")
    print(f"  optimized (warm cache): {warm_total:>7} tokens   ({warm_pct}% saved)")

    rule("Attribution (cold run, as recorded by the ledger)")
    for name, data in optimized["by_technique"].items():
        print(f"  {name:<20} {data['tokens_saved']:>7}")
    print(
        "  note: pruning reads 0 because compaction already keeps the prompt "
        "under budget.\n        Pruning is the hard backstop - see its own row "
        "in the ablation above."
    )

    rule("Does it even fit the context window?")
    window = base.context_window
    for label, report in (("naive", naive), ("optimized", optimized)):
        peak = report["peak_optimized_prompt"]
        verdict = "fits" if peak <= window else "OVERFLOWS - request would be rejected"
        print(f"  {label:<12} peak prompt {peak:>6} / {window} -> {verdict}")

    rule("Context hygiene (optimized, cold run)")
    print(f"  full transcript retained : {optimized['full_transcript_messages']} messages")
    print(f"  working context sent     : {optimized['working_context_messages']} messages")
    print(f"  cache (after warm run)   : {warm['cache']}")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
