from __future__ import annotations

import ast
import datetime as dt
import operator
import re

from .registry import Tool, ToolRegistry

# --- A tiny knowledge base so the demo has something real to retrieve. --------
KNOWLEDGE_BASE: dict[str, str] = {
    "pricing": (
        "Pricing tiers: Starter $0/mo with 50k tokens, Team $99/mo with 5M tokens "
        "and per-seat analytics, Enterprise custom with private networking, SSO "
        "and a 99.9% uptime SLA. Overage is billed at $2 per additional 1M tokens."
    ),
    "latency": (
        "Median end-to-end latency is 820 ms for cached answers and 2.4 s for "
        "tool-augmented answers. The p95 target is 4 s. Cache hits bypass the "
        "model entirely and return in under 40 ms."
    ),
    "security": (
        "Transport is TLS 1.3. Data at rest uses AES-256. Prompts and completions "
        "are retained for 30 days by default and can be set to zero retention. "
        "The platform is SOC 2 Type II audited."
    ),
    "onboarding": (
        "Onboarding takes 3 steps: connect a model provider, import a knowledge "
        "source, then publish an agent. A typical pilot is live in 5 business days."
    ),
    "architecture": (
        "The reference architecture separates the transcript store from the "
        "assembled prompt. Compaction runs first, pruning second, caching wraps "
        "both, and every call is written to a token ledger."
    ),
}

_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
        return _SAFE_BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    raise ValueError("unsupported expression")


def calculator(expression: str) -> str:
    """Arithmetic only - never eval() untrusted input."""
    cleaned = expression.replace("^", "**")
    if not re.fullmatch(r"[0-9+\-*/%.()\s*]+", cleaned):
        return "error: expression contains unsupported characters"
    try:
        return f"{_eval(ast.parse(cleaned, mode='eval')):g}"
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as exc:
        return f"error: {exc}"


def search_kb(query: str) -> str:
    terms = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", query)}
    hits = [
        f"[{topic}] {text}"
        for topic, text in KNOWLEDGE_BASE.items()
        if topic in terms or terms & {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}
    ]
    return "\n".join(hits[:3]) if hits else "no matching knowledge base entries"


def current_time(timezone: str = "UTC") -> str:
    now = dt.datetime.now(dt.timezone.utc)
    return f"{now.isoformat(timespec='seconds')} ({timezone})"


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="calculator",
            description=(
                "Evaluate an arithmetic expression. Supports + - * / % and "
                "exponentiation with parentheses."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Arithmetic expression, e.g. '(1200*12)/4'.",
                    }
                },
                "required": ["expression"],
            },
            handler=calculator,
            keywords={"numeric", "calculate", "compute", "sum", "total", "cost", "math",
                      "multiply", "divide", "percent", "budget", "price"},
        )
    )
    registry.register(
        Tool(
            name="search_kb",
            description=(
                "Search the product knowledge base for grounded facts about "
                "pricing, latency, security, onboarding and architecture."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language search terms.",
                    }
                },
                "required": ["query"],
            },
            handler=search_kb,
            keywords={"pricing", "price", "cost", "latency", "security", "compliance",
                      "onboarding", "architecture", "sla", "retention", "docs", "product",
                      "tier", "plan", "soc"},
        )
    )
    registry.register(
        Tool(
            name="current_time",
            description="Return the current UTC timestamp.",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA timezone name."}
                },
                "required": [],
            },
            handler=current_time,
            keywords={"time", "date", "now", "today", "timestamp", "clock"},
        )
    )
    return registry
