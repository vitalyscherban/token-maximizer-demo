from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

TECHNIQUES = (
    "compaction",
    "pruning",
    "tool_routing",
    "schema_slimming",
    "result_truncation",
    "cache",
)

TECHNIQUE_DESCRIPTIONS = {
    "compaction": "Old turns replaced by a dense memo before they are sent again.",
    "pruning": "Low-relevance history dropped to fit the history budget.",
    "tool_routing": "Only query-relevant tool schemas are exposed to the model.",
    "schema_slimming": "Tool schemas stripped of prose descriptions.",
    "result_truncation": "Oversized tool output trimmed middle-out before re-entry.",
    "cache": "Normalised prompt cache returns prior completions without a model call.",
}


@dataclass
class CallRecord:
    step: int
    kind: str
    baseline_prompt: int = 0
    baseline_completion: int = 0
    optimized_prompt: int = 0
    optimized_completion: int = 0
    cached: bool = False
    savings: dict[str, int] = field(default_factory=dict)

    @property
    def baseline_total(self) -> int:
        return self.baseline_prompt + self.baseline_completion

    @property
    def optimized_total(self) -> int:
        return self.optimized_prompt + self.optimized_completion


class TokenLedger:
    """Records optimized vs. baseline token spend for every model call.

    The ledger is the demo's proof: without a measured counterfactual, token
    efficiency claims are unverifiable. "Baseline" here means the naive agent -
    full transcript, every tool schema in full, no cache.
    """

    def __init__(self) -> None:
        self._records: dict[str, list[CallRecord]] = defaultdict(list)

    def record(self, session_id: str, record: CallRecord) -> CallRecord:
        self._records[session_id].append(record)
        return record

    def records(self, session_id: str) -> list[CallRecord]:
        return list(self._records[session_id])

    def report(self, session_id: str) -> dict:
        records = self._records[session_id]
        baseline = sum(r.baseline_total for r in records)
        optimized = sum(r.optimized_total for r in records)

        by_technique: dict[str, int] = {name: 0 for name in TECHNIQUES}
        for record in records:
            for name, value in record.savings.items():
                by_technique[name] = by_technique.get(name, 0) + value

        return {
            "session_id": session_id,
            "model_calls": len(records),
            "cached_calls": sum(1 for r in records if r.cached),
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "tokens_saved": max(0, baseline - optimized),
            "savings_pct": round((baseline - optimized) / baseline * 100, 2) if baseline else 0.0,
            # Peak prompt size decides whether a request fits the context window
            # at all - the failure mode that cost averages hide.
            "peak_baseline_prompt": max((r.baseline_prompt for r in records), default=0),
            "peak_optimized_prompt": max((r.optimized_prompt for r in records), default=0),
            "by_technique": {
                name: {"tokens_saved": value, "description": TECHNIQUE_DESCRIPTIONS[name]}
                for name, value in by_technique.items()
                if name in TECHNIQUE_DESCRIPTIONS
            },
            "calls": [
                {
                    "step": r.step,
                    "kind": r.kind,
                    "cached": r.cached,
                    "baseline_tokens": r.baseline_total,
                    "optimized_tokens": r.optimized_total,
                }
                for r in records
            ],
        }

    def totals(self) -> dict:
        baseline = sum(r.baseline_total for rs in self._records.values() for r in rs)
        optimized = sum(r.optimized_total for rs in self._records.values() for r in rs)
        return {
            "sessions": len(self._records),
            "baseline_tokens": baseline,
            "optimized_tokens": optimized,
            "tokens_saved": max(0, baseline - optimized),
            "savings_pct": round((baseline - optimized) / baseline * 100, 2) if baseline else 0.0,
        }
