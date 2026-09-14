# Configuration reference

All configuration lives in `src/tokenmax/config.py` as a single
`pydantic-settings` model. Every value is overridable by an environment variable
with the `TOKENMAX_` prefix, or by a `.env` file in the working directory.

```powershell
copy .env.example .env
```

`get_settings()` is `@lru_cache`d, so the process reads the environment once.
Change a variable, restart the process. Endpoints that need different settings
(`/api/compare`) build their own `Settings` copy rather than mutating the global
one.

---

## Provider

| Variable | Default | Notes |
|---|---|---|
| `TOKENMAX_PROVIDER` | `mock` | `mock` or `openai` |
| `TOKENMAX_MODEL` | `mock-1` | model id; also selects the `tiktoken` encoding |
| `TOKENMAX_API_KEY` | _unset_ | **required** when provider is `openai` |
| `TOKENMAX_BASE_URL` | `https://api.openai.com/v1` | point at any OpenAI-compatible endpoint |

`mock` is the default on purpose. A client demo must not be able to fail because
of a rate limit or a network blip, and the token numbers must be identical every
time you present them. Swap to `openai` when you need to talk about answer
*quality*; the token-efficiency story is unchanged either way.

Selecting `openai` without an API key raises at agent construction:
`TOKENMAX_API_KEY is required when TOKENMAX_PROVIDER=openai`.

---

## Budget

| Variable | Default | Notes |
|---|---|---|
| `TOKENMAX_CONTEXT_WINDOW` | `8192` | total window assumed for budget arithmetic |
| `TOKENMAX_RESERVED_OUTPUT` | `512` | held back for the completion; also passed as `max_output_tokens` |
| `TOKENMAX_COMPACTION_TRIGGER` | `0.6` | fraction of the history allowance at which compaction fires |
| `TOKENMAX_PROTECTED_RECENT_TURNS` | `4` | most-recent turns that are never pruned or compacted |
| `TOKENMAX_TOOL_RESULT_MAX_TOKENS` | `256` | per tool result, before middle-out truncation |
| `TOKENMAX_CACHE_TTL_SECONDS` | `900` | prompt cache entry lifetime |

Derived per call (`context/budget.py`):

```
prompt_allowance  = context_window   - reserved_output
history_allowance = prompt_allowance - system_tokens - tool_schema_tokens
compaction_threshold = history_allowance * compaction_trigger
```

### Tuning notes

**`context_window`** should match the real model's window. The demo profiles use
a deliberately tiny `1200` so a six-turn conversation exercises compaction and
pruning the way a long production session would; at a roomy 128k nothing fires
and the demo shows nothing.

**`compaction_trigger`** fires compaction *early*, well before the hard limit.
Compaction costs a summarisation call; you want it to happen cheaply and ahead
of time, not in a panic at 99%. Raising it towards `0.9` leaves more work for
pruning, which makes pruning's contribution visible in the ledger — that is why
`demo/run_demo.py` uses `0.9` and `/api/compare` uses `0.75`.

**`protected_recent_turns`** is a **hard floor on prompt size**. Pruning stops
when only protected groups remain, even if the result still exceeds the budget:
conversational coherence wins over cost. If the protected window alone does not
fit under `context_window`, no amount of pruning will save you. Watch
`peak_optimized_prompt` in the ledger report when tuning this, not the average.

**`tool_result_max_tokens`** trades grounding for cost. Truncation is middle-out
— head and tail survive, the middle is marked — because identifiers live at the
start and conclusions, totals and error messages live at the end.

**`cache_ttl_seconds`** governs staleness. The cache is an exact-match store over
the normalised assembled prompt, so a short TTL is the only guard against a
knowledge source changing underneath a cached answer.

---

## Technique switches

Each of the six techniques is an independent boolean, all `true` by default.

| Variable | Technique | Implementation |
|---|---|---|
| `TOKENMAX_ENABLE_COMPACTION` | compaction | `context/compaction.py` |
| `TOKENMAX_ENABLE_PRUNING` | pruning | `context/pruning.py` |
| `TOKENMAX_ENABLE_TOOL_ROUTING` | tool routing | `tools/registry.py` |
| `TOKENMAX_ENABLE_SCHEMA_SLIMMING` | schema slimming | `tools/registry.py` |
| `TOKENMAX_ENABLE_RESULT_TRUNCATION` | result truncation | `tokens.py` |
| `TOKENMAX_ENABLE_CACHE` | prompt cache | `cache/prompt_cache.py` |

Turning one off mid-demo and re-running is the point of these flags: the ledger
immediately shows what that technique was worth. `GET /api/techniques` reports
the live state, so the console always reflects reality rather than a hard-coded
list.

Setting all six to `false` produces the naive agent — full transcript, every
tool schema in full, no cache. That is exactly how the baseline in
`/api/compare` and the ablation in `demo/run_demo.py` are constructed.

---

## Token counting

`TokenCounter` uses `tiktoken` when it is importable and a deterministic
heuristic otherwise. There is no setting: it is capability detection.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[tiktoken]"
```

`GET /api/health` reports `token_backend` so you always know which path is live.
Absolute counts differ between backends; **relative savings hold either way**,
which is the claim the demo actually makes.

---

## Demo profiles

Three profiles exist in the repo. They are not settings files — they are
`Settings.model_copy(update=...)` calls — but they are worth knowing.

| Profile | Where | Window | Reserved | Trigger | Protected | Tool result |
|---|---|---|---|---|---|---|
| default | `config.py` | 8192 | 512 | 0.6 | 4 | 256 |
| API compare | `api/routes.py` `CompareRequest` | 1200 | 512 | 0.75 | 2 | 60 |
| console demo | `demo/run_demo.py` `DEMO_PROFILE` | 1200 | 200 | 0.9 | 4 | 60 |

The `/api/compare` profile is request-settable, so you can change the window or
the trigger live from the console without restarting anything.
