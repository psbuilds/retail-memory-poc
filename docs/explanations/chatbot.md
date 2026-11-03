# `app/chatbot.py` — deterministic retail chatbot

## 1. File Purpose

`RetailChatbot` is the application layer between the common `MemoryAdapter` and
the deterministic product engine. It can receive either CLI or MCP adapter
without changing its code. It never calls Cognee REST, CLI subprocesses, or MCP
SDK methods directly.

## 2. Classes and Data Structures

`RememberResponse` and `ChatResponse` are frozen, slotted dataclasses. They keep
operation status, errors, latency, extracted preferences, recalled memories,
and recommendation IDs easy to serialize later without implementing benchmark
CSV output here.

`RetailChatbot` stores only the injected adapter, an immutable catalog tuple, and
the recommendation limit. It depends on the `MemoryAdapter` abstraction rather
than a concrete integration.

## 3. Functions and Methods

`remember(customer_id, facts)` passes validated facts to the adapter exactly once
and exposes the adapter's `RememberResult` count and latency. Adapter validation
and error hierarchy remain authoritative.

`respond(customer_id, query)` recalls exactly once, extracts query preferences,
filters relevant memory fragments, extracts memory preferences, merges memory
first and query second, then calls `recommend_products` once. Recall failures
return `memory_failure` with no fallback recommendations. Empty successful recall
continues with query-only preferences.

`extract_preferences(text)` uses deterministic regular expressions and keyword
tables for categories, budgets, brands, wireless/wired, ANC, features, and
negative phrases. Negated wireless and ANC statements are resolved before
positive keyword handling; the last mutually exclusive scalar mention wins.
It does not parse with an LLM or external service.

`relevant_memory_fragments` ignores category-specific memory that conflicts with
the current category. `merge_preferences` applies a small category applicability
map so, for example, memory-derived wireless constraints do not constrain
footwear, while supported lightweight features can remain relevant. Multiple
equally signaled memory categories do not select one by dictionary order.

`merge_preferences` gives current query scalar values precedence. Feature and
exclusion lists are conflict-resolved: a current required feature removes a
stored exclusion, and a current exclusion removes a stored requirement. Current
query constraints are retained even when they are incompatible with the chosen
category, producing an honest empty result rather than silently relaxing them.

Recommendation exceptions return `recommendation_failure`; recall failures return
`memory_failure`; valid empty results return `no_recommendations`; input errors
return `invalid_input`; successful recommendations return `success`.

## 4. Execution Flow

```text
respond(customer, query)
  → adapter.recall exactly once
  → extract query preferences
  → retain relevant memory fragments
  → extract memory preferences
  → merge memory then query
  → recommend_products(catalog, preferences, limit=3)
  → ChatResponse
```

Recall latency comes from `RecallResult.latency_ms`; extraction, recommendation,
and total application timing use `perf_counter()` separately. Adapter cold-start
behavior remains outside this application layer and belongs to Phase 9.

## 5. Architecture and Design Reasoning

Dependency injection keeps CLI and MCP fair: the same extraction, relevance,
preference precedence, product catalog, and ranking execute for either adapter.
The chatbot does not inspect integration labels or scenario ground truth. Memory
failure is distinct from successful empty recall, and constraints are never
relaxed to force a product.

Natural-language extraction is intentionally small and explainable for this POC;
Phase 8 does not add an LLM. A future benchmark can call the same `respond`
method for both integrations without changing recommendation behavior.
