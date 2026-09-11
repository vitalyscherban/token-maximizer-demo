from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetPlan:
    """Resolved token allowances for a single model call."""

    context_window: int
    reserved_output: int
    system_tokens: int
    tool_schema_tokens: int

    @property
    def prompt_allowance(self) -> int:
        return max(0, self.context_window - self.reserved_output)

    @property
    def history_allowance(self) -> int:
        """What is left for conversation history once the fixed costs are paid."""
        return max(0, self.prompt_allowance - self.system_tokens - self.tool_schema_tokens)

    def compaction_threshold(self, trigger: float) -> int:
        return int(self.history_allowance * trigger)

    def as_dict(self) -> dict[str, int]:
        return {
            "context_window": self.context_window,
            "reserved_output": self.reserved_output,
            "system_tokens": self.system_tokens,
            "tool_schema_tokens": self.tool_schema_tokens,
            "prompt_allowance": self.prompt_allowance,
            "history_allowance": self.history_allowance,
        }
