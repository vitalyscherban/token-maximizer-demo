from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..agent import TokenEfficientAgent
from ..config import get_settings
from ..models import ChatRequest, ChatResponse
from ..telemetry import TECHNIQUE_DESCRIPTIONS
from .deps import get_agent

router = APIRouter()


class CompareRequest(BaseModel):
    messages: list[str] = Field(
        default_factory=lambda: [
            "What are your pricing tiers?",
            "What is the latency profile?",
            "How do you handle security and data retention?",
            "Walk me through onboarding.",
            "What are your pricing tiers?",
            "If Team is 99 per seat, what is 99*25?",
        ],
        description="Scenario turns replayed against both configurations.",
    )
    context_window: int = Field(
        default=1200,
        description=(
            "Deliberately small so a six-turn conversation exercises compaction "
            "and pruning the way a long production session would."
        ),
    )
    tool_result_max_tokens: int = 60
    compaction_trigger: float = Field(
        default=0.75,
        description="Late enough that pruning stays visible, early enough to keep headroom.",
    )
    protected_recent_turns: int = Field(
        default=2,
        description=(
            "The protected window is the hard floor on prompt size - pruning "
            "will never drop it, so it must leave headroom under the window."
        ),
    )


@router.get("/health")
async def health(agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    return {
        "status": "ok",
        "provider": agent.provider.name,
        "model": agent.provider.model,
        "token_backend": agent.counter.backend,
    }


@router.get("/techniques")
async def techniques(agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    settings = agent.settings
    enabled = {
        "compaction": settings.enable_compaction,
        "pruning": settings.enable_pruning,
        "tool_routing": settings.enable_tool_routing,
        "schema_slimming": settings.enable_schema_slimming,
        "result_truncation": settings.enable_result_truncation,
        "cache": settings.enable_cache,
    }
    return {
        "techniques": [
            {"name": name, "description": TECHNIQUE_DESCRIPTIONS[name], "enabled": enabled[name]}
            for name in enabled
        ],
        "budget": {
            "context_window": settings.context_window,
            "reserved_output": settings.reserved_output,
            "compaction_trigger": settings.compaction_trigger,
            "tool_result_max_tokens": settings.tool_result_max_tokens,
        },
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest, agent: TokenEfficientAgent = Depends(get_agent)
) -> ChatResponse:
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="message must not be empty")
    return await agent.run(request.session_id, request.message, max_steps=request.max_steps)


@router.get("/sessions")
async def sessions(agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    return {"sessions": agent.transcript.sessions(), "ledger": agent.ledger.totals()}


@router.get("/sessions/{session_id}/report")
async def report(session_id: str, agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    if session_id not in agent.transcript.sessions():
        raise HTTPException(status_code=404, detail="unknown session")
    data = agent.ledger.report(session_id)
    data["cache"] = agent.cache.stats.as_dict()
    return data


@router.get("/sessions/{session_id}/transcript")
async def transcript(session_id: str, agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    if session_id not in agent.transcript.sessions():
        raise HTTPException(status_code=404, detail="unknown session")
    return {
        "full_transcript": [m.model_dump() for m in agent.transcript.history(session_id)],
        "working_context": [m.model_dump() for m in agent.store.history(session_id)],
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, agent: TokenEfficientAgent = Depends(get_agent)) -> dict:
    agent.store.reset(session_id)
    agent.transcript.reset(session_id)
    return {"deleted": session_id}


@router.post("/compare")
async def compare(request: CompareRequest) -> dict:
    """Replay one scenario twice - naive vs. optimized - on isolated agents."""
    base = get_settings().model_copy(
        update={
            "context_window": request.context_window,
            "tool_result_max_tokens": request.tool_result_max_tokens,
            "compaction_trigger": request.compaction_trigger,
            "protected_recent_turns": request.protected_recent_turns,
        }
    )
    flags = {
        "enable_compaction": True,
        "enable_pruning": True,
        "enable_tool_routing": True,
        "enable_schema_slimming": True,
        "enable_result_truncation": True,
        "enable_cache": True,
    }
    naive = TokenEfficientAgent(
        settings=base.model_copy(update={key: False for key in flags})
    )
    optimized = TokenEfficientAgent(settings=base.model_copy(update=flags))

    results = {}
    for label, agent in (("naive", naive), ("optimized", optimized)):
        turns = []
        for message in request.messages:
            response = await agent.run("compare", message)
            turns.append(
                {
                    "message": message,
                    "answer": response.answer,
                    "tokens": response.optimized_tokens,
                }
            )
        report = agent.ledger.report("compare")
        results[label] = {
            "turns": turns,
            "total_tokens": sum(t["tokens"] for t in turns),
            "peak_prompt_tokens": report["peak_optimized_prompt"],
            "fits_context_window": report["peak_optimized_prompt"] <= request.context_window,
            "report": report,
        }

    naive_total = results["naive"]["total_tokens"]
    optimized_total = results["optimized"]["total_tokens"]
    return {
        "scenario": request.messages,
        "context_window": request.context_window,
        "naive_tokens": naive_total,
        "optimized_tokens": optimized_total,
        "tokens_saved": max(0, naive_total - optimized_total),
        "savings_pct": (
            round((naive_total - optimized_total) / naive_total * 100, 2) if naive_total else 0.0
        ),
        "detail": results,
    }
