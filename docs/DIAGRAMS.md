# Architecture diagrams

Every diagram on this page is generated from the code in `src/tokenmax/`. If a
diagram and the code disagree, the code is right — please open a fix.

Diagrams are Mermaid. GitHub, VS Code and most static-site generators render
them natively; no build step is required.

---

## 1. System context

Who talks to what. The demo is a single process with no external dependency in
its default configuration — that is deliberate, so a client demo cannot fail
because of a network blip.

```mermaid
flowchart LR
    user["Presenter / client<br/>(browser)"]
    cli["demo/run_demo.py<br/>console walkthrough"]
    tests["pytest suite<br/>(51 tests, no network)"]

    subgraph proc["tokenmax process"]
        api["FastAPI app<br/>tokenmax.api"]
        agent["TokenEfficientAgent<br/>tokenmax.agent.loop"]
    end

    mock["MockProvider<br/>offline, deterministic"]
    openai["OpenAIProvider<br/>OpenAI-compatible HTTP"]

    user -->|"HTTP + console UI"| api
    api --> agent
    cli --> agent
    tests --> agent
    agent -->|"complete() / summarize()"| mock
    agent -.->|"opt-in via TOKENMAX_PROVIDER=openai"| openai
    openai -.->|"httpx"| ext[("Model API")]

    classDef optional stroke-dasharray: 4 3;
    class openai,ext optional;
```

---

## 2. Component map

The package boundaries, and which component owns which concern.

```mermaid
flowchart TB
    subgraph api["api/ — HTTP surface"]
        routes["routes.py<br/>/chat /compare /report"]
        deps["deps.py<br/>process-wide agent singleton"]
        static["static/index.html<br/>console dashboard"]
    end

    subgraph agentpkg["agent/ — orchestration"]
        loop["loop.py<br/>TokenEfficientAgent.run()"]
    end

    subgraph context["context/ — context assembly"]
        store["store.py<br/>ConversationStore"]
        budget["budget.py<br/>BudgetPlan"]
        compaction["compaction.py<br/>compact()"]
        pruning["pruning.py<br/>prune()"]
    end

    subgraph tools["tools/ — capability surface"]
        registry["registry.py<br/>Tool, ToolRegistry<br/>route() + schema(compact)"]
        builtin["builtin.py<br/>calculator, search_kb, current_time"]
    end

    cache["cache/prompt_cache.py<br/>PromptCache + CacheStats"]
    telemetry["telemetry/ledger.py<br/>TokenLedger, CallRecord"]
    tokens["tokens.py<br/>TokenCounter"]
    providers["providers/<br/>LLMProvider, Mock, OpenAI"]
    models["models.py<br/>Message, Completion, Usage"]
    config["config.py<br/>Settings (TOKENMAX_*)"]

    routes --> deps --> loop
    loop --> store
    loop --> budget
    loop --> compaction
    loop --> pruning
    loop --> registry
    loop --> cache
    loop --> telemetry
    loop --> providers
    registry --> builtin
    compaction --> providers
    compaction --> tokens
    pruning --> tokens
    budget -.-> tokens
    loop --> tokens
    config --> loop
    models -.-> loop
```

---

## 3. The pipeline

`compact → prune → route → slim → cache → call`. This ordering is load-bearing;
see [ARCHITECTURE.md §3](ARCHITECTURE.md#3-the-pipeline-order-matters) for why.

```mermaid
flowchart TD
    start(["run(session_id, user_message)"]) --> sys["ensure system prompt<br/>pin the active user goal<br/>unpin older goals"]
    sys --> read["working = store.history(session)"]
    read --> route["registry.route(query)<br/>top-3 by keyword overlap<br/>fallback: all tools"]
    route --> slim["tool.schema(compact=True)<br/>drop prose descriptions"]
    slim --> plan["BudgetPlan<br/>history_allowance = window - reserved<br/>- system - schemas"]

    plan --> compactq{"tokens ><br/>compaction_trigger<br/>x history_allowance?"}
    compactq -->|yes| compact["compact()<br/>stale turns -> one memo<br/>store.replace()"]
    compactq -->|no| pruneq
    compact --> pruneq{"tokens ><br/>history_allowance?"}
    pruneq -->|yes| prune["prune()<br/>drop lowest<br/>relevance + recency"]
    pruneq -->|no| cacheq
    prune --> cacheq{"cache hit on<br/>normalised prompt<br/>+ schemas?"}

    cacheq -->|hit| hit["Completion(cached=True)<br/>optimized cost = 0"]
    cacheq -->|miss| modelcall["provider.complete(messages, schemas)"]
    modelcall --> put["cache.put(key, completion)"]

    hit --> ledger["ledger.record(CallRecord)<br/>optimized vs. baseline<br/>+ per-technique savings"]
    put --> ledger

    ledger --> toolq{"completion has<br/>a tool_call?"}
    toolq -->|no| done(["ChatResponse<br/>answer, steps, savings"])
    toolq -->|yes| exec["tool.invoke(args)"]
    exec --> trunc["truncate_to_tokens()<br/>middle-out"]
    trunc --> append["append tool result<br/>to store + transcript"]
    append --> read

    classDef step fill:#eef,stroke:#446;
    class compact,prune,route,slim,trunc step;
```

The loop is bounded by `max_steps` (default 6). Exhausting it returns an
explicit "step budget exhausted" answer rather than silently looping.

---

## 4. Two stores, one prompt

The single most important design decision: what is **kept** and what is **sent**
are different objects.

```mermaid
flowchart LR
    subgraph agentbox["TokenEfficientAgent"]
        direction TB
        transcript["<b>transcript</b><br/>ConversationStore<br/>append-only, full fidelity<br/><i>never compacted or pruned</i>"]
        store["<b>store</b><br/>ConversationStore<br/>working context<br/><i>rewritten every step</i>"]
    end

    msg["new Message"] --> transcript
    msg --> store

    transcript --> baseline["baseline cost<br/>full history + all schemas<br/><i>the counterfactual</i>"]
    transcript --> audit["GET /sessions/{id}/transcript<br/>audit trail"]

    store --> compact["compact()"] --> prune["prune()"] --> wire["wire_format()<br/>-> provider"]

    baseline --> ledger[("TokenLedger")]
    wire --> ledger
```

Because the two are separate, lossy optimisation is safe: you can compact forty
turns into a memo and still answer "what exactly did the user say in turn 3?".

---

## 5. Turn sequence

One `POST /api/chat` that needs a tool, end to end.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as api/routes.py
    participant A as TokenEfficientAgent
    participant S as store + transcript
    participant T as ToolRegistry
    participant K as PromptCache
    participant P as LLMProvider
    participant L as TokenLedger

    C->>R: POST /api/chat {session_id, message}
    R->>A: run(session_id, message)
    A->>S: append(system?, user[pinned])
    A->>T: route(query) + schemas(compact=True)
    T-->>A: 3 slim schemas
    A->>A: BudgetPlan(window, reserved, system, schemas)
    A->>P: summarize(stale turns)  [compaction]
    P-->>A: memo
    A->>S: replace(session, [head, memo, recent])
    A->>A: prune(working, history_allowance, query)
    A->>K: get(sha256(normalised prompt + schemas))
    K-->>A: miss
    A->>P: complete(messages, tools)
    P-->>A: Completion(tool_call=search_kb)
    A->>K: put(key, completion)
    A->>L: CallRecord(baseline, optimized, savings{...})
    A->>T: invoke("search_kb", args)
    T-->>A: raw result
    A->>A: truncate_to_tokens(result, 256)
    A->>S: append(assistant tool_call, tool result)

    Note over A,P: loop repeats — next step re-runs the whole pipeline

    A->>P: complete(messages, tools)
    P-->>A: Completion(content="…final answer")
    A->>L: CallRecord(...)
    A-->>R: ChatResponse(answer, steps, baseline, optimized, savings_pct)
    R-->>C: 200 JSON
```

---

## 6. Budget arithmetic

`context/budget.py` resolves one number per call. Fixed costs are paid first, so
the agent can never be surprised by an overflow it caused itself.

```mermaid
flowchart LR
    subgraph window["context_window (e.g. 8192)"]
        direction LR
        reserved["reserved_output<br/>512"]
        sys["system_tokens<br/>system + pinned"]
        schemas["tool_schema_tokens<br/>routed + slimmed"]
        hist["<b>history_allowance</b><br/>the only budget<br/>pruning may spend"]
    end

    hist --> trigger["compaction fires at<br/>history_allowance x compaction_trigger<br/>(default 0.6)"]
```

```
prompt_allowance  = context_window   - reserved_output
history_allowance = prompt_allowance - system_tokens - tool_schema_tokens
compaction_threshold = history_allowance * compaction_trigger
```

---

## 7. Pruning: retention tiers

Retention is tiered. Tool calls and their results are bound into one atomic
group first, because providers reject an orphaned `tool` message.

```mermaid
flowchart TD
    msgs["working context messages"] --> groups["_groups()<br/>bind assistant tool_call<br/>to its tool result"]
    groups --> fits{"tokens <= budget?"}
    fits -->|yes| keep(["keep everything"])
    fits -->|no| score["score each group"]

    score --> t1["<b>Tier 1 — infinite score</b><br/>system prompt, pinned user goal"]
    score --> t2["<b>Tier 2 — infinite score</b><br/>last N groups<br/>(protected_recent_turns)"]
    score --> t3["<b>Tier 3 — ranked</b><br/>relevance(query) + recency x 0.5<br/>+0.4 if already a compacted memo"]

    t3 --> drop["drop lowest-scoring group<br/>re-measure"]
    drop --> under{"under budget?"}
    under -->|no| drop
    under -->|yes| out(["PruneResult(kept, dropped, tokens_saved)"])
    t1 --> floor["hard floor:<br/>stop when only<br/>protected groups remain"]
    t2 --> floor
    floor --> out
```

Relevance is a cheap lexical overlap (`|A ∩ B| / sqrt(|A| + |B|)`), not an
embedding call — spending a model call to decide what to drop from a model call
is a trap.

**The protected window is a hard floor.** If it alone exceeds the budget,
pruning stops anyway: coherence wins over cost. Size `protected_recent_turns` to
leave headroom, and watch `peak_optimized_prompt` when tuning.

---

## 8. Tool routing and schema slimming

Two savings, stacked. Routing decides *which* schemas are sent; slimming decides
*how big* each one is.

```mermaid
flowchart LR
    q["user query"] --> score["score = |query terms ∩ tool.keywords|<br/>+2 if tool name appears<br/>+1 if query has digits and tool is 'numeric'"]
    score --> any{"any tool scored?"}
    any -->|no| all["<b>fall back to all tools</b><br/>recall is never traded for savings"]
    any -->|yes| top["top 3 by score, then name"]
    all --> slimq
    top --> slimq{"enable_schema_slimming?"}
    slimq -->|no| full["full schema<br/>name + description + parameters<br/>with prose"]
    slimq -->|yes| slim["compact schema<br/>name + first sentence<br/>types + required only"]
    full --> send["schemas sent with the call"]
    slim --> send
```

Attribution is measured, not assumed:

```
tool_routing    = tokens(all tools, full)    - tokens(routed tools, full)
schema_slimming = tokens(routed tools, full) - tokens(routed tools, as sent)
```

---

## 9. Prompt cache

```mermaid
flowchart TD
    inp["assembled prompt + tool schemas"] --> norm["normalise each part<br/>strip, lowercase, collapse whitespace"]
    norm --> hash["sha256, parts joined by<br/>a unit-separator byte"]
    hash --> look{"key present?"}
    look -->|no| miss["stats.misses++"]
    look -->|yes| fresh{"expires_at > now?"}
    fresh -->|no| evict["delete entry<br/>stats.evictions++<br/>stats.misses++"] --> miss
    fresh -->|yes| hit["stats.hits++<br/>Completion(cached=True)"]

    miss --> call["provider.complete()"] --> put["put(key, completion)<br/>evict oldest if full<br/>(max_entries 512)"]
    put --> paid["full token cost recorded"]
    hit --> free["<b>optimized cost = 0</b><br/>recorded against a full baseline"]
```

The unit-separator between parts matters: without it `["ab","c"]` and `["a","bc"]` hash
identically, and the cache returns a completion for a prompt that was never
sent. Normalisation is where the real hit rate comes from — "What is your
pricing?" and "what is YOUR   pricing" become one entry.

---

## 10. The ledger: how a saving is proved

Every model call writes one `CallRecord` holding both costs.

```mermaid
flowchart TB
    call["one model call"] --> rec["CallRecord"]
    rec --> b["baseline_prompt<br/>= full transcript + all full schemas"]
    rec --> bc["baseline_completion"]
    rec --> o["optimized_prompt<br/>= what was actually sent<br/>(0 on a cache hit)"]
    rec --> oc["optimized_completion<br/>(0 on a cache hit)"]
    rec --> attr["savings{compaction, pruning,<br/>tool_routing, schema_slimming,<br/>result_truncation, cache}"]

    b --> report["TokenLedger.report(session)"]
    bc --> report
    o --> report
    oc --> report
    attr --> report

    report --> totals["baseline_tokens, optimized_tokens<br/>tokens_saved, savings_pct"]
    report --> peaks["<b>peak_baseline_prompt</b><br/><b>peak_optimized_prompt</b>"]
    report --> bytech["by_technique attribution"]

    peaks --> verdict{"peak > context_window?"}
    verdict -->|yes| broken["the request is rejected —<br/>not expensive, <b>broken</b>"]
    verdict -->|no| fine["fits"]
```

Averages hide the failure that matters. In the demo the naive agent peaks at
1,703 tokens against a 1,200-token window: it does not merely cost more, it
stops working.

---

## 11. Data model

```mermaid
classDiagram
    class Message {
        +str id
        +Role role
        +str content
        +str|None name
        +ToolCall|None tool_call
        +str|None tool_call_id
        +bool pinned
        +bool compacted
        +float created_at
        +wire_format() dict
    }
    class ToolCall {
        +str id
        +str name
        +dict arguments
    }
    class Usage {
        +int prompt_tokens
        +int completion_tokens
        +total() int
    }
    class Completion {
        +str content
        +ToolCall|None tool_call
        +Usage usage
        +bool cached
    }
    class StepTrace {
        +int index
        +str kind
        +str detail
        +int prompt_tokens
        +int completion_tokens
        +bool cached
    }
    class ChatResponse {
        +str session_id
        +str answer
        +Usage usage
        +int baseline_tokens
        +int optimized_tokens
        +int tokens_saved
        +float savings_pct
    }
    class CallRecord {
        +int step
        +str kind
        +int baseline_prompt
        +int baseline_completion
        +int optimized_prompt
        +int optimized_completion
        +bool cached
        +dict savings
    }

    Message "0..1" --> "1" ToolCall : tool_call
    Completion "0..1" --> "1" ToolCall : tool_call
    Completion --> Usage
    ChatResponse --> Usage
    ChatResponse "1" --> "*" StepTrace : steps
    ConversationStore "1" --> "*" Message : per session
    TokenLedger "1" --> "*" CallRecord : per session
```

`pinned` and `compacted` are local bookkeeping only — `wire_format()` never
sends them to a provider.

---

## 12. A/B comparison

`POST /api/compare` builds two fully isolated agents from the same `Settings`
and replays one scenario against both.

```mermaid
flowchart TB
    req["CompareRequest<br/>messages, context_window,<br/>tool_result_max_tokens,<br/>compaction_trigger,<br/>protected_recent_turns"] --> base["Settings.model_copy(update=...)"]

    base --> naive["naive agent<br/>all enable_* = False"]
    base --> opt["optimized agent<br/>all enable_* = True"]

    naive --> nrun["replay scenario<br/>own store, cache, ledger"]
    opt --> orun["replay scenario<br/>own store, cache, ledger"]

    nrun --> nrep["report: total_tokens,<br/>peak_prompt_tokens,<br/>fits_context_window"]
    orun --> orep["report: total_tokens,<br/>peak_prompt_tokens,<br/>fits_context_window"]

    nrep --> diff["tokens_saved, savings_pct"]
    orep --> diff
```

`demo/run_demo.py` extends this into a full **ablation**: it replays the
scenario once per technique with only that flag on, so each technique's
standalone contribution is measured rather than asserted.

---

## 13. Where the tokens actually go

A rough map of one naive step versus one optimized step, using the demo profile
(1,200-token window, three tools).

```mermaid
flowchart LR
    subgraph n["naive step"]
        direction TB
        n1["full transcript<br/>every turn, verbatim"]
        n2["all tool schemas<br/>full prose descriptions"]
        n3["untruncated tool results"]
    end

    subgraph o["optimized step"]
        direction TB
        o1["memo + protected window<br/><i>compaction, pruning</i>"]
        o2["routed slim schemas<br/><i>tool routing, schema slimming</i>"]
        o3["middle-out trimmed results<br/><i>result truncation</i>"]
        o4["…or nothing at all<br/><i>cache hit = 0 tokens</i>"]
    end

    n1 -.-> o1
    n2 -.-> o2
    n3 -.-> o3
    n -.-> o4
```

Ablation on the reference scenario (see the README for the current table):
pruning and tool routing dominate, result truncation is third, and the cache
contributes nothing on a cold run and everything on a warm one.
