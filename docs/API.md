# API reference

Base URL when run locally: `http://127.0.0.1:8000`. All agent endpoints are
mounted under `/api`; `/` serves the console dashboard.

```powershell
.\.venv\Scripts\python.exe -m uvicorn tokenmax.api.main:app --reload
```

Interactive OpenAPI docs are available at `/docs` (FastAPI default).

One agent instance is shared process-wide (`api/deps.py`, `@lru_cache`), so the
store, cache and ledger accumulate across requests. A demo of cold versus warm
cost needs warm state — restart the server to reset it, or `DELETE` the session.

---

## GET /api/health

Reports which backends are actually live. Useful mid-demo when someone asks
whether the numbers come from a real tokenizer.

```json
{
  "status": "ok",
  "provider": "mock",
  "model": "mock-1",
  "token_backend": "heuristic"
}
```

| Field | Meaning |
|---|---|
| `provider` | `mock` (offline, deterministic) or `openai` |
| `model` | model id passed to the provider |
| `token_backend` | `tiktoken` if the extra is installed, else `heuristic` |

---

## GET /api/techniques

The six levers and the budget knobs currently in force.

```json
{
  "techniques": [
    { "name": "compaction", "description": "Old turns replaced by a dense memo before they are sent again.", "enabled": true },
    { "name": "pruning", "description": "…", "enabled": true },
    { "name": "tool_routing", "description": "…", "enabled": true },
    { "name": "schema_slimming", "description": "…", "enabled": true },
    { "name": "result_truncation", "description": "…", "enabled": true },
    { "name": "cache", "description": "…", "enabled": true }
  ],
  "budget": {
    "context_window": 8192,
    "reserved_output": 512,
    "compaction_trigger": 0.6,
    "tool_result_max_tokens": 256
  }
}
```

---

## POST /api/chat

Run one turn through the full pipeline.

**Request** (`ChatRequest`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `session_id` | string | random 12-hex | reuse it to continue a conversation |
| `message` | string | — | required; must not be blank (`422` otherwise) |
| `max_steps` | int | `6` | hard cap on model+tool iterations |

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "content-type: application/json" \
  -d '{"session_id":"demo","message":"What are your pricing tiers?"}'
```

**Response** (`ChatResponse`)

```json
{
  "session_id": "demo",
  "answer": "Pricing tiers: Starter $0/mo …",
  "steps": [
    { "index": 0, "kind": "model", "detail": "tool call: search_kb({'query': '…'})",
      "prompt_tokens": 214, "completion_tokens": 18, "cached": false },
    { "index": 0, "kind": "tool", "detail": "search_kb -> [pricing] Pricing tiers…",
      "prompt_tokens": 0, "completion_tokens": 0, "cached": false },
    { "index": 1, "kind": "model", "detail": "final answer",
      "prompt_tokens": 286, "completion_tokens": 42, "cached": false }
  ],
  "usage": { "prompt_tokens": 500, "completion_tokens": 60 },
  "baseline_tokens": 921,
  "optimized_tokens": 560,
  "tokens_saved": 361,
  "savings_pct": 39.2
}
```

| Field | Meaning |
|---|---|
| `steps` | ordered trace; `kind` is `model` or `tool` |
| `usage` | tokens actually consumed, including compaction summaries |
| `baseline_tokens` | what a naive agent would have spent on the same turn |
| `optimized_tokens` | what this agent spent (`0` for a cached call) |
| `savings_pct` | `(baseline - optimized) / baseline * 100` |

Send the **same message twice in one session** to demonstrate the cache: the
second response reports `optimized_tokens: 0` and `cached: true` on its steps.

---

## POST /api/compare

Replay one scenario twice — naive versus optimized — on two isolated agents.
This backs the console's A/B button.

**Request** (`CompareRequest`, every field optional)

| Field | Type | Default | Why this default |
|---|---|---|---|
| `messages` | string[] | six-turn product Q&A ending in a repeat + a calculation | the repeat exercises the cache; the calculation exercises tool routing |
| `context_window` | int | `1200` | deliberately small so six turns behave like a long production session |
| `tool_result_max_tokens` | int | `60` | small enough that truncation visibly fires |
| `compaction_trigger` | float | `0.75` | late enough that pruning stays visible |
| `protected_recent_turns` | int | `2` | the hard floor on prompt size — must leave headroom under the window |

**Response**

```json
{
  "scenario": ["What are your pricing tiers?", "…"],
  "context_window": 1200,
  "naive_tokens": 14284,
  "optimized_tokens": 7774,
  "tokens_saved": 6510,
  "savings_pct": 45.58,
  "detail": {
    "naive": {
      "turns": [{ "message": "…", "answer": "…", "tokens": 1703 }],
      "total_tokens": 14284,
      "peak_prompt_tokens": 1703,
      "fits_context_window": false,
      "report": { "…": "full ledger report" }
    },
    "optimized": { "…": "same shape, fits_context_window: true" }
  }
}
```

`fits_context_window` is the line to point at in a demo. The naive run does not
merely cost more — at its peak it exceeds the window and the request would be
rejected.

Both agents are constructed fresh per request, so `/compare` never pollutes the
shared singleton agent used by `/chat`.

---

## GET /api/sessions

```json
{
  "sessions": ["demo", "demo-warm"],
  "ledger": {
    "sessions": 2,
    "baseline_tokens": 28568,
    "optimized_tokens": 7774,
    "tokens_saved": 20794,
    "savings_pct": 72.79
  }
}
```

---

## GET /api/sessions/{session_id}/report

The ledger report for one session, plus cache statistics. `404` if the session
is unknown.

```json
{
  "session_id": "demo",
  "model_calls": 12,
  "cached_calls": 0,
  "baseline_tokens": 14284,
  "optimized_tokens": 7774,
  "tokens_saved": 6510,
  "savings_pct": 45.58,
  "peak_baseline_prompt": 1703,
  "peak_optimized_prompt": 886,
  "by_technique": {
    "compaction":        { "tokens_saved": 482,  "description": "…" },
    "pruning":           { "tokens_saved": 0,    "description": "…" },
    "tool_routing":      { "tokens_saved": 2688, "description": "…" },
    "schema_slimming":   { "tokens_saved": 264,  "description": "…" },
    "result_truncation": { "tokens_saved": 51,   "description": "…" },
    "cache":             { "tokens_saved": 0,    "description": "…" }
  },
  "calls": [
    { "step": 0, "kind": "model", "cached": false, "baseline_tokens": 921, "optimized_tokens": 560 }
  ],
  "cache": { "hits": 0, "misses": 12, "stores": 12, "evictions": 0, "hit_rate": 0.0, "tokens_saved": 0 }
}
```

Totals above are from the cold run of `demo/run_demo.py`. Note that
`by_technique` attribution (`2688` for routing, `482` for compaction) is *not*
the same as the ablation table in the README (`2688`, `812`): attribution
measures what each technique saved **while the others were also running**, while
ablation measures each one standalone. Both are honest; they answer different
questions.

Two fields deserve more attention than the headline percentage:

- **`peak_optimized_prompt`** — the number to tune against. Averages hide the
  single turn that overflows the window, and an overflow is a hard error, not an
  expensive success.
- **`by_technique`** — attribution recorded per call. A `0` here is not
  necessarily a broken technique; pruning commonly reads `0` in a combined run
  because compaction already kept the prompt under budget. Use the ablation in
  `demo/run_demo.py` to measure a technique standalone.

---

## GET /api/sessions/{session_id}/transcript

The proof that nothing was lost, only unsent. `404` if the session is unknown.

```json
{
  "full_transcript": [{ "id": "…", "role": "user", "content": "…", "pinned": false, "compacted": false }],
  "working_context":  [{ "id": "…", "role": "system", "content": "[compacted memory of 6 earlier turns] …", "compacted": true }]
}
```

`full_transcript` is append-only and never pruned. `working_context` is the
derived view that actually gets assembled into a prompt. In a live demo the two
lists diverging is the whole point.

---

## DELETE /api/sessions/{session_id}

Drops the session from both stores. Idempotent — deleting an unknown session
still returns `200`. The ledger records and the cache are **not** cleared.

```json
{ "deleted": "demo" }
```

---

## Errors

| Status | When |
|---|---|
| `404` | `report` / `transcript` for a session that has no transcript |
| `422` | blank `/api/chat` message, or a body that fails Pydantic validation |
| `500` | `TOKENMAX_PROVIDER=openai` without `TOKENMAX_API_KEY`, or an upstream provider failure |

Tool faults do **not** produce an error response. An unknown tool, bad arguments
or a raising handler is converted into an `error: …` string and fed back to the
model as a tool result, so a single broken tool cannot kill the agent loop.
