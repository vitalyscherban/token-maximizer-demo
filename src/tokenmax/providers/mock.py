from __future__ import annotations

import re

from ..models import Completion, Message, ToolCall, Usage
from ..tokens import TokenCounter
from .base import LLMProvider

_MATH_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:[-+*/^]\s*[-+]?\d+(?:\.\d+)?)+")
_STOPWORDS = {
    "what", "which", "the", "and", "for", "with", "that", "this", "does", "how",
    "our", "your", "are", "is", "do", "of", "to", "in", "a", "an", "on", "about",
    "tell", "me", "explain", "please", "can", "you",
}


class MockProvider(LLMProvider):
    """Deterministic, offline model stand-in.

    Client demos must never fail because of a missing API key, a rate limit or a
    non-deterministic answer. This provider reproduces the parts of real model
    behaviour the architecture depends on - tool selection, grounded answers and
    usage accounting - without a network call.
    """

    name = "mock"

    def __init__(self, counter: TokenCounter | None = None, model: str = "mock-1") -> None:
        self.model = model
        self.counter = counter or TokenCounter(model)

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        max_output_tokens: int = 512,
    ) -> Completion:
        prompt_tokens = self._prompt_tokens(messages, tools)
        tool_names = {t["name"] for t in (tools or [])}

        last = messages[-1] if messages else None
        question = self._latest_question(messages)

        if last is not None and last.role == "tool":
            content = self._answer_from_tools(question, messages)
        elif "calculator" in tool_names and (match := _MATH_RE.search(question)):
            return self._tool_completion(
                prompt_tokens, "calculator", {"expression": match.group(0).replace(" ", "")}
            )
        elif "search_kb" in tool_names and (terms := self._keywords(question)):
            return self._tool_completion(prompt_tokens, "search_kb", {"query": " ".join(terms)})
        elif "current_time" in tool_names and "time" in question.lower():
            return self._tool_completion(prompt_tokens, "current_time", {})
        else:
            content = (
                f"Here is what I can tell you about '{question.strip()}' from the "
                "conversation so far."
            )

        content, _ = self.counter.truncate_to_tokens(content, max_output_tokens)
        return Completion(
            content=content,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=self.counter.count_text(content),
            ),
        )

    async def summarize(self, text: str, max_output_tokens: int = 160) -> Completion:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
        facts = [s for s in sentences if any(ch.isdigit() for ch in s)] or sentences
        summary = "Earlier context: " + " ".join(facts[:4])
        summary, _ = self.counter.truncate_to_tokens(summary, max_output_tokens)
        return Completion(
            content=summary,
            usage=Usage(
                prompt_tokens=self.counter.count_text(text),
                completion_tokens=self.counter.count_text(summary),
            ),
        )

    def _tool_completion(self, prompt_tokens: int, name: str, arguments: dict) -> Completion:
        call = ToolCall(name=name, arguments=arguments)
        completion_tokens = self.counter.count_text(name + str(arguments))
        return Completion(
            tool_call=call,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
        )

    def _prompt_tokens(self, messages: list[Message], tools: list[dict] | None) -> int:
        total = self.counter.count_messages(messages)
        for tool in tools or []:
            total += self.counter.count_schema(tool)
        return total

    @staticmethod
    def _latest_question(messages: list[Message]) -> str:
        for message in reversed(messages):
            if message.role == "user":
                return message.content
        return ""

    @staticmethod
    def _keywords(text: str) -> list[str]:
        words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text)]
        return [w for w in words if w not in _STOPWORDS][:5]

    def _answer_from_tools(self, question: str, messages: list[Message]) -> str:
        evidence = [m.content for m in messages if m.role == "tool"][-2:]
        joined = " ".join(evidence)
        return f"{question.strip()} -> {joined}".strip()
