from __future__ import annotations

from ..cache import PromptCache
from ..config import Settings, get_settings
from ..context import BudgetPlan, ConversationStore, compact, prune
from ..models import ChatResponse, Message, StepTrace, Usage
from ..providers import LLMProvider, build_provider
from ..telemetry import CallRecord, TokenLedger
from ..tokens import TokenCounter
from ..tools import ToolRegistry, build_default_registry

SYSTEM_PROMPT = (
    "You are a concise product assistant. Answer from the provided context and "
    "tool results only. If a fact is missing, say so instead of guessing."
)


class TokenEfficientAgent:
    """Agent loop where context assembly, not the model call, is the main event.

    Every turn runs the same pipeline:
    compact -> prune -> route tools -> slim schemas -> cache lookup -> model call,
    and every call is written to the ledger against a naive baseline.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        provider: LLMProvider | None = None,
        registry: ToolRegistry | None = None,
        store: ConversationStore | None = None,
        cache: PromptCache | None = None,
        ledger: TokenLedger | None = None,
        counter: TokenCounter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.counter = counter or TokenCounter(self.settings.model)
        self.provider = provider or build_provider(self.settings, self.counter)
        self.registry = registry or build_default_registry()
        self.store = store or ConversationStore()
        # Never compacted or pruned: the audit trail and the baseline yardstick.
        self.transcript = ConversationStore()
        self.cache = cache or PromptCache(ttl_seconds=self.settings.cache_ttl_seconds)
        self.ledger = ledger or TokenLedger()

    async def run(self, session_id: str, user_message: str, max_steps: int = 6) -> ChatResponse:
        self._ensure_system(session_id)
        self._add_user_turn(session_id, user_message)

        steps: list[StepTrace] = []
        usage = Usage()
        baseline = optimized = 0
        pending_truncation_savings = 0
        answer = ""

        for step in range(max_steps):
            working = self.store.history(session_id)
            tools = self.registry.route(user_message, enabled=self.settings.enable_tool_routing)
            schemas = self.registry.schemas(tools, compact=self.settings.enable_schema_slimming)

            plan = self._plan(working, schemas)
            savings: dict[str, int] = {}
            if pending_truncation_savings:
                savings["result_truncation"] = pending_truncation_savings
                pending_truncation_savings = 0
            savings.update(self._tool_savings(tools, schemas))

            if self.settings.enable_compaction:
                result = await compact(
                    working,
                    self.provider,
                    self.counter,
                    threshold=plan.compaction_threshold(self.settings.compaction_trigger),
                    protected_recent=self.settings.protected_recent_turns,
                )
                if result.compacted_count:
                    self.store.replace(session_id, result.messages)
                    working = result.messages
                    savings["compaction"] = result.tokens_saved
                    usage = usage + result.usage

            if self.settings.enable_pruning:
                pruned = prune(
                    working,
                    self.counter,
                    budget=plan.history_allowance,
                    query=user_message,
                    protected_recent=self.settings.protected_recent_turns,
                )
                if pruned.dropped:
                    savings["pruning"] = pruned.tokens_saved
                working = pruned.kept

            completion, cache_key = await self._complete(working, schemas)

            record = CallRecord(
                step=step,
                kind="cache" if completion.cached else "model",
                baseline_prompt=self._baseline_prompt_tokens(session_id),
                baseline_completion=completion.usage.completion_tokens,
                optimized_prompt=0 if completion.cached else completion.usage.prompt_tokens,
                optimized_completion=0 if completion.cached else completion.usage.completion_tokens,
                cached=completion.cached,
                savings=savings,
            )
            if completion.cached:
                record.savings["cache"] = record.baseline_total
            self.ledger.record(session_id, record)
            baseline += record.baseline_total
            optimized += record.optimized_total
            if not completion.cached:
                usage = usage + completion.usage
                if cache_key and self.settings.enable_cache:
                    self.cache.put(cache_key, completion)

            if completion.tool_call is None:
                answer = completion.content
                self._append(session_id, Message(role="assistant", content=answer))
                steps.append(
                    StepTrace(
                        index=step,
                        kind="model",
                        detail="final answer",
                        prompt_tokens=record.optimized_prompt,
                        completion_tokens=record.optimized_completion,
                        cached=completion.cached,
                    )
                )
                break

            call = completion.tool_call
            self._append(session_id, Message(role="assistant", tool_call=call))
            steps.append(
                StepTrace(
                    index=step,
                    kind="model",
                    detail=f"tool call: {call.name}({call.arguments})",
                    prompt_tokens=record.optimized_prompt,
                    completion_tokens=record.optimized_completion,
                    cached=completion.cached,
                )
            )

            output, truncation_savings = await self._run_tool(call.name, call.arguments)
            pending_truncation_savings += truncation_savings
            self._append(
                session_id,
                Message(role="tool", name=call.name, content=output, tool_call_id=call.id),
            )
            steps.append(
                StepTrace(index=step, kind="tool", detail=f"{call.name} -> {output[:80]}")
            )
        else:
            answer = "Step budget exhausted before a final answer was produced."
            self._append(session_id, Message(role="assistant", content=answer))

        return ChatResponse(
            session_id=session_id,
            answer=answer,
            steps=steps,
            usage=usage,
            baseline_tokens=baseline,
            optimized_tokens=optimized,
            tokens_saved=max(0, baseline - optimized),
            savings_pct=round((baseline - optimized) / baseline * 100, 2) if baseline else 0.0,
        )

    # -- pipeline helpers ----------------------------------------------------

    def _ensure_system(self, session_id: str) -> None:
        if not self.store.history(session_id):
            system = Message(role="system", content=SYSTEM_PROMPT, pinned=True)
            self._append(session_id, system)

    def _add_user_turn(self, session_id: str, user_message: str) -> None:
        # Only the active goal stays pinned; older goals become prunable.
        history = self.store.history(session_id)
        for message in history:
            if message.role == "user":
                message.pinned = False
        self.store.replace(session_id, history)
        self._append(session_id, Message(role="user", content=user_message, pinned=True))

    def _append(self, session_id: str, message: Message) -> None:
        self.store.append(session_id, message)
        self.transcript.append(session_id, message)

    def _plan(self, working: list[Message], schemas: list[dict]) -> BudgetPlan:
        fixed = [m for m in working if m.role == "system" or m.pinned]
        return BudgetPlan(
            context_window=self.settings.context_window,
            reserved_output=self.settings.reserved_output,
            system_tokens=self.counter.count_messages(fixed),
            tool_schema_tokens=sum(self.counter.count_schema(s) for s in schemas),
        )

    def _tool_savings(self, routed: list, schemas: list[dict]) -> dict[str, int]:
        all_tools = self.registry.all()
        full_all = sum(self.counter.count_schema(t.schema()) for t in all_tools)
        full_routed = sum(self.counter.count_schema(t.schema()) for t in routed)
        sent = sum(self.counter.count_schema(s) for s in schemas)
        savings = {}
        if full_all - full_routed > 0:
            savings["tool_routing"] = full_all - full_routed
        if full_routed - sent > 0:
            savings["schema_slimming"] = full_routed - sent
        return savings

    async def _complete(self, working: list[Message], schemas: list[dict]):
        cache_key = None
        if self.settings.enable_cache:
            cache_key = self.cache.key(
                [m.role + ":" + m.content for m in working] + [str(schemas)]
            )
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit, cache_key
        completion = await self.provider.complete(
            working, tools=schemas, max_output_tokens=self.settings.reserved_output
        )
        return completion, cache_key

    async def _run_tool(self, name: str, arguments: dict) -> tuple[str, int]:
        tool = self.registry.get(name)
        if tool is None:
            return f"error: unknown tool {name!r}", 0
        try:
            output = await tool.invoke(arguments)
        except TypeError as exc:
            return f"error: invalid arguments for {name}: {exc}", 0
        except Exception as exc:  # tool faults must not kill the agent loop
            return f"error: {name} failed: {exc}", 0

        if not self.settings.enable_result_truncation:
            return output, 0
        before = self.counter.count_text(output)
        trimmed, was_truncated = self.counter.truncate_to_tokens(
            output, self.settings.tool_result_max_tokens
        )
        if not was_truncated:
            return output, 0
        return trimmed, max(0, before - self.counter.count_text(trimmed))

    def _baseline_prompt_tokens(self, session_id: str) -> int:
        """What a naive agent would have sent: full transcript, all schemas."""
        history = self.transcript.history(session_id)
        schemas = sum(self.counter.count_schema(t.schema()) for t in self.registry.all())
        return self.counter.count_messages(history) + schemas
