# Module map

Read in this order. Each entry says what the module owns, what to look at, and
the decision it encodes.

```
src/tokenmax/
├─ agent/loop.py          the pipeline — read this first
├─ context/
│  ├─ store.py            ConversationStore
│  ├─ budget.py           BudgetPlan
│  ├─ compaction.py       compact()
│  └─ pruning.py          prune()
├─ tools/
│  ├─ registry.py         Tool, ToolRegistry (routing + slimming)
│  └─ builtin.py          calculator, search_kb, current_time
├─ cache/prompt_cache.py  PromptCache, CacheStats
├─ telemetry/ledger.py    TokenLedger, CallRecord
├─ providers/
│  ├─ base.py             LLMProvider (2 methods)
│  ├─ mock.py             offline, deterministic
│  ├─ openai_provider.py  OpenAI-compatible HTTP
│  └─ factory.py          build_provider()
├─ api/
│  ├─ main.py             FastAPI app
│  ├─ routes.py           /api/*
│  ├─ deps.py             process-wide agent singleton
│  └─ static/index.html   console dashboard
├─ models.py              Message, ToolCall, Usage, Completion, Chat*, StepTrace
├─ tokens.py              TokenCounter (counting + middle-out truncation)
└─ config.py              Settings
demo/run_demo.py          console walkthrough with ablation
tests/                    51 tests, no network
```

---

## `agent/loop.py` — `TokenEfficientAgent`

The orchestrator, and the only module that knows the full pipeline order.

| Member | Role |
|---|---|
| `run()` | one turn: loops `compact → prune → route → slim → cache → call → tool` up to `max_steps` |
| `transcript` | append-only `ConversationStore`; never reduced |
| `store` | working context; rewritten by compaction each step |
| `_plan()` | builds the `BudgetPlan` from system/pinned tokens + schema tokens |
| `_tool_savings()` | measures routing and slimming by differencing schema token counts |
| `_complete()` | cache lookup over the final assembled prompt, then the provider call |
| `_run_tool()` | invokes a tool, converts faults to `error: …` strings, truncates output |
| `_baseline_prompt_tokens()` | the counterfactual: full transcript + all full schemas |

Two details that are easy to miss:

- `_add_user_turn()` **unpins older user goals**. Only the active goal is
  pinned, so a long conversation does not accumulate an ever-growing set of
  unprunable messages.
- Truncation savings are **deferred**: a result truncated in step *n* is
  attributed to the ledger record of step *n+1*, because that is the call whose
  prompt it shrank.

---

## `context/store.py` — `ConversationStore`

A dict of session id to message list. Deliberately boring.

Its value is that the agent holds **two instances**. The store keeps full
history; the assembled prompt is always a derived view. That separation is what
makes lossy optimisation safe — nothing the user said is destroyed, it just
stops being sent.

Swap this for Redis or Postgres without touching anything else; no other module
depends on it being in memory.

---

## `context/budget.py` — `BudgetPlan`

A frozen dataclass that resolves one number per call:

```
prompt_allowance  = context_window   - reserved_output
history_allowance = prompt_allowance - system_tokens - tool_schema_tokens
```

`history_allowance` is the only budget pruning may spend. Fixed costs are
subtracted first, so the agent cannot be surprised by an overflow it caused
itself. `compaction_threshold(trigger)` returns the earlier point at which
compaction fires.

---

## `context/compaction.py` — `compact()`

Replaces the oldest unprotected turns with one dense memo produced by
`provider.summarize()`. The memo is a `system` message with `compacted=True`.

Guards, in order:

1. Under threshold → no-op.
2. Body no larger than the protected window → no-op.
3. Everything stale is already a memo → no-op (never re-summarise a summary for
   nothing).

Returns a `CompactionResult` carrying `tokens_saved` **and** the `Usage` of the
summarisation call, so the cost of compaction is counted honestly against its
own saving.

---

## `context/pruning.py` — `prune()`

Drops the least useful history until it fits the budget.

- `_groups()` binds each assistant `tool_call` to its `tool` result. Providers
  reject an orphaned tool message; this is the single most common bug in
  hand-rolled pruning.
- `relevance()` is lexical overlap, `|A ∩ B| / sqrt(|A| + |B|)`, over stopworded
  word sets. Spending a model call to decide what to drop from a model call is a
  trap; upgrade to embeddings only after measuring that you need to.
- Score = `relevance(query) + recency * 0.5`, with `+0.4` for existing memos
  (already cheap, prefer keeping them). Pinned, system and the last
  `protected_recent` groups score infinity and are never dropped.
- Removal re-measures after each drop, and **stops at the protected floor** even
  if the result still exceeds budget.

---

## `tools/registry.py` — `Tool`, `ToolRegistry`

Two techniques live here.

`route(query, limit=3)` scores tools by keyword overlap, `+2` when the tool name
appears in the query, `+1` for numeric tools when the query contains digits.
**When nothing scores it returns every tool** — a router that silently hides the
one tool the agent needed is worse than no router.

`Tool.schema(compact=True)` keeps names, types and required flags, and drops
prose: the description collapses to its first sentence and per-property
descriptions disappear. On a 12-tool agent this alone removes hundreds of tokens
from *every* call.

`invoke()` awaits awaitable handlers, so sync and async tools register the same
way.

---

## `tools/builtin.py`

Three tools and a five-entry knowledge base, enough to make the demo answer real
questions.

| Tool | Handler | Notes |
|---|---|---|
| `calculator` | `calculator()` | AST-walking evaluator over a whitelist of binary ops. Never `eval()` on untrusted input. |
| `search_kb` | `search_kb()` | lexical match over `KNOWLEDGE_BASE`, top 3 hits |
| `current_time` | `current_time()` | UTC timestamp; exists mainly to give the router a third, clearly-irrelevant option |

---

## `cache/prompt_cache.py` — `PromptCache`

Normalised exact-match cache over `(assembled prompt, tool schemas)`.

- `normalize()` strips, lowercases and collapses whitespace. That is where the
  hit rate comes from: "What is your pricing?" and "what is YOUR   pricing" are
  one entry.
- `key()` is SHA-256 with a `\x1f` separator after every part, so `["ab","c"]`
  and `["a","bc"]` cannot collide.
- TTL expiry counts as both an eviction and a miss. At `max_entries` (512) the
  soonest-expiring entry is dropped.
- `CacheStats` tracks hits, misses, stores, evictions, hit rate and tokens
  saved — surfaced on `GET /api/sessions/{id}/report`.

A hit costs zero tokens and is recorded as `optimized_total = 0` against a full
baseline, which is why a warm run reports 100% savings.

---

## `telemetry/ledger.py` — `TokenLedger`, `CallRecord`

One `CallRecord` per model call holding *both* costs plus per-technique
attribution. `TECHNIQUES` and `TECHNIQUE_DESCRIPTIONS` are the canonical names
used by the API and the console.

`report(session)` returns totals, per-technique attribution, the per-call list,
and — the field that matters most — `peak_baseline_prompt` /
`peak_optimized_prompt`. A single turn that exceeds the context window is a hard
error, not an expensive success, and averages hide it.

`totals()` aggregates across every session for the cross-session headline.

Without a measured counterfactual, "we reduced tokens by 45%" is unfalsifiable.
This module is what makes it a query.

---

## `providers/`

`LLMProvider` exposes exactly two methods, `complete()` and `summarize()`. The
surface is tiny on purpose: the agent owns context assembly, so no provider
SDK's convenience layer can silently re-inflate the prompt behind your back.

- `MockProvider` — offline and deterministic. Reproduces tool selection,
  grounded answers and usage accounting, not model quality.
- `OpenAIProvider` — `httpx` against any OpenAI-compatible `/chat/completions`.
- `build_provider()` — selects from `Settings`, and fails loudly if
  `provider=openai` has no API key.

---

## `tokens.py` — `TokenCounter`

Counting and truncation.

- `tiktoken` when importable, deterministic heuristic otherwise; `backend`
  reports which. Relative savings hold either way.
- `count_message()` charges `MESSAGE_OVERHEAD_TOKENS` (4) per message and
  `PRIMING_OVERHEAD_TOKENS` (3) per assembled prompt, because chat APIs bill
  framing you did not write.
- `truncate_to_tokens()` is **middle-out**: two thirds head, one third tail, with
  a `...[truncated]...` marker. Identifiers and headers live at the start;
  conclusions, totals and error messages live at the end. Head-only truncation
  reliably removes the answer.

---

## `models.py`

Pydantic models shared by every layer. `Message.pinned` and `Message.compacted`
are local bookkeeping — `wire_format()` sends only `role`, `content`, and the
tool-call fields, so no optimisation metadata ever reaches a provider.

---

## `api/`

`routes.py` holds the eight endpoints (see [API.md](API.md)). `deps.py` provides
the process-wide agent singleton so cold-versus-warm cost is observable across
requests. `/api/compare` deliberately constructs its own pair of agents so the
A/B run never pollutes the shared one. `static/index.html` is the console: the
A/B runner, a live chat box with per-turn accounting, and the technique list
fetched from `/api/techniques`.

---

## `demo/run_demo.py`

The console walkthrough, in five parts: naive baseline, per-technique ablation,
optimized cold run, optimized warm run, then the "does it even fit the context
window?" verdict and a context-hygiene summary.

The ablation is what makes the attribution defensible — each technique is
measured with only that flag on, rather than inferred from the combined run.

---

## `tests/`

51 tests, no network, covering token counting and truncation, pruning retention
and tool-call atomicity, compaction guards, cache normalisation and TTL, tool
routing and fault handling, the agent loop, and every API endpoint.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```
