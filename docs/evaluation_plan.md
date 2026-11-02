# Phase 1 — Evaluation Plan and Data Schemas

Scope: this document defines *what* the POC measures and *what shape* its data takes.
It deliberately contains no adapter, chatbot, or benchmark code.

The experimental variable is the integration mechanism (`cli` vs `mcp`). Everything
else — dataset, customer facts, queries, product catalog, recommendation logic,
Cognee backend — is held identical.

---

## 1. Operations benchmarked

Only the two operations exposed by the memory interface are measured directly:

| Operation | Signature | Notes |
|---|---|---|
| `remember` | `remember(customer_id, facts)` | One call per customer seed |
| `recall` | `recall(customer_id, query)` | One call per scenario |

Nothing else is invented at this level. A "recall" is always issued for the
customer referenced by a scenario, so every measured recall maps to a
`scenario_id` even though it is not an agent turn.

### Run phases

Every run is tagged with a `phase`, so cold and warm measurements never mix:

| Phase | Meaning | Included in summaries |
|---|---|---|
| `cold_start` | Adapter construction and first request (MCP handshake / first CLI spawn) | No — reported separately |
| `warmup` | Throwaway iterations (`BENCHMARK_WARMUP`, default 3) | No |
| `measured` | Timed iterations (`BENCHMARK_RUNS`, default 20) | Yes |

Seeding/reset work performed before a `remember` measurement is **not** inside
the timed region. This is applied identically to both integrations, so neither
pays for one-time setup that the other avoids.

`cold_start` is part of the main Phase 9 benchmark, not an optional extra. It is
a first-class reported metric (connection/spawn cost at agent start-up) but is
kept out of the per-operation statistics so that warm latency stays comparable.

### Timing boundary

Latency is measured with `time.perf_counter()` immediately around the adapter
call, inside the adapter boundary. It therefore includes subprocess spawn (CLI)
or MCP transport framing, and excludes test-harness bookkeeping. The difference
in what the boundary contains must be documented rather than smoothed over.

---

## 2. Metrics

### Level 1 — integration level (per operation call)

| Metric | Why it exists |
|---|---|
| `integration` | Which mechanism produced the row (`cli` / `mcp`) |
| `phase` | Separates cold start, warmup, measured |
| `scenario_id` | Pairs the call with a scenario so both integrations use the same query |
| `customer_id` | Confirms both sides read the same memory |
| `operation` | `remember` or `recall` |
| `iteration` | Ordering within a phase; lets p95 be computed and outliers inspected |
| `latency_ms` | Primary performance signal |
| `success` | Reliability signal; failures are recorded, never dropped |
| `result_count` | Empty-success detection (a "successful" recall that returned nothing) |
| `response_chars` | Payload size — proxy for tokens/context cost the agent pays |
| `error_type` / `error_message` | Error visibility comparison (exception class vs sanitized protocol error) |

### Level 2 — end-to-end agent level (per scenario turn)

Workflow: `query → identify customer → recall → apply preferences → rank products → respond`.

| Metric | Why it exists |
|---|---|
| `latency_ms` | Full agent turn latency (what a user feels) |
| `recall_latency_ms` | Memory portion of the turn — isolates integration overhead from ranking time |
| `status` | One of `success`, `memory_failure`, `irrelevant_memory`, `recommendation_failure`, `agent_error`; keeps the four failure modes distinguishable |
| `memory_retrieval_success` | Adapter-level outcome, independent of ranking quality |
| `retrieved_chars` | Context cost of retrieved memory in the real agent loop |
| `relevance_score` | `matched expected_memory terms / len(expected_memory)` (0.0–1.0) |
| `recommended_product_ids` | Auditable output, in rank order |
| `recommendation_match` | `matched expected_product_ids / len(expected_product_ids)` within top-k |

Scoring rules (fixed for both integrations):

- `TOP_K = 3` recommendations per scenario.
- `relevance_score` uses case-insensitive substring matching of each
  `expected_memory` term against the concatenated recalled text.
- `recommendation_match` is the fraction of `expected_product_ids` present in the
  top-k; a recommended `excluded_product_ids` entry does not reduce the score but
  is reported in analysis as a violated negative preference.
- Scenario `success` requires: recall succeeded, `relevance_score > 0`, and
  `recommendation_match == 1.0`.

`cold_start` latency is reported separately as its own number and is never
averaged into per-operation latency.

---

## 3. Data schemas

### 3.1 Products — `data/products.json`

```json
{
  "id": "p001",
  "name": "Wireless Earbuds",
  "category": "headphones",
  "price": 2499,
  "features": ["wireless", "lightweight", "travel-friendly", "in-ear"]
}
```

| Field | Necessity |
|---|---|
| `id` | Stable join key for deterministic scoring of recommendations |
| `name` | Human-readable output in the agent response |
| `category` | Coarse filter derived from the query |
| `price` | Required for budget constraints (all prices in INR) |
| `features` | Required for positive/negative preference matching |

No stock, ratings, links, or images: they add no signal to the comparison.
Currency is INR for every row and is therefore documented once, not per row.

### 3.2 Customers — `data/customers.json`

```json
{
  "customer_id": "customer_001",
  "facts": [
    "Prefers affordable wireless headphones.",
    "Has a budget below ₹3,000."
  ]
}
```

| Field | Necessity |
|---|---|
| `customer_id` | Identifies whose memory is written/read; also derives the dataset name |
| `facts` | The exact payload passed to `remember`; plain strings so both adapters send identical input |

Each customer carries 3–5 facts mixing budget, feature, usage-context and negative
preferences. No `name`, email, or history — nothing in the POC branches on them.

**Dataset naming convention (fairness critical):** both adapters derive the memory
scope from the customer id as `retail_<customer_id>`. Neither adapter may compute
a different scope, otherwise the two paths would read different memory.

### 3.3 Scenarios — `data/scenarios.json`

```json
{
  "id": "scenario_01",
  "customer_id": "customer_001",
  "query": "Can you recommend headphones for me?",
  "scenario_type": "budget",
  "expected_memory": ["affordable", "wireless", "3000"],
  "expected_product_ids": ["p001", "p004"],
  "excluded_product_ids": ["p002"],
  "expected_behavior": "Recommend wireless headphones below ₹3,000; avoid bulky over-ear models."
}
```

| Field | Necessity |
|---|---|
| `id` | Primary key in both raw and summary results |
| `customer_id` | Ties scenario to a memory record |
| `query` | The single identical input string sent to both integrations |
| `scenario_type` | Guarantees coverage: `budget`, `feature`, `negative`, `mixed`, `ambiguous` |
| `expected_memory` | Terms used to score recall relevance deterministically |
| `expected_product_ids` | Ground truth for recommendation scoring (no LLM judge) |
| `excluded_product_ids` | Encodes negative preferences so violations are detectable |
| `expected_behavior` | Human-readable intent for the analysis write-up |

Scenarios must never be edited per adapter, and `query` is never rewritten.

---

## 4. Result file formats

### 4.1 Raw integration runs — `results/raw/integration_runs.csv`

One row per operation call. Written by the Phase 9 benchmark; failures included.

```csv
run_id,timestamp_utc,integration,phase,scenario_id,customer_id,operation,iteration,latency_ms,success,result_count,response_chars,error_type,error_message
```

### 4.2 Raw end-to-end runs — `results/raw/e2e_runs.csv`

One row per scenario turn per integration.

```csv
run_id,timestamp_utc,integration,phase,scenario_id,customer_id,query,latency_ms,recall_latency_ms,status,memory_retrieval_success,retrieved_chars,relevance_score,recommended_product_ids,recommendation_match,error_type,error_message
```

`recommended_product_ids` is serialized as a `|`-separated list to keep the CSV
flat (no nested quoting).

### 4.3 Summary — `results/comparison.csv`

Long format, one row per `(level, operation, integration)` so both levels and all
operations live in one comparable table.

```csv
level,operation,integration,runs,successes,failures,success_rate,latency_mean_ms,latency_p50_ms,latency_p95_ms,latency_min_ms,latency_max_ms,notes
```

- `level`: `integration` or `e2e`
- `operation`: `remember`, `recall`, `scenario`, or `cold_start`
- Warm rows are computed from `measured` rows only.
- Cold start gets its own `operation=cold_start` row per integration (`runs=1`,
  so mean/p50/p95/min/max all equal the single value) and is never mixed into
  the `remember`/`recall` statistics.

---

## 5. Determinism rules

1. `data/*.json` is committed and never regenerated during a benchmark run.
2. The same committed files are read by both integrations — no per-adapter copies.
3. Warmup iterations are fixed and identical for both integrations.
4. Measured iteration count is identical for both integrations (`BENCHMARK_RUNS`).
5. Any protocol-level difference that cannot be removed is written into
   `docs/evaluation_plan.md` or the final analysis, not compensated for.

---

## 6. Developer / engineering evaluation criteria

Recorded from the actual build experience, without numeric scores:

| Criterion | Evidence to capture |
|---|---|
| Components required | Processes that must be running per integration |
| Dependencies | Packages/CLIs needed on top of the shared Cognee backend |
| Configuration complexity | Env vars and config files per path |
| Implementation complexity | Lines and branches inside each adapter |
| Debugging experience | What is visible on failure (traceback vs protocol error) |
| Error visibility | Concrete exception text captured from an induced failure |
| Maintainability | What changes when the Cognee version or memory scope changes |
| Extensibility | Cost of adding a third operation to each adapter |

---

## 7. Cold start and optional extended benchmarks

### 7.1 Cold-start cost — in the main benchmark

Already captured as the `cold_start` phase in section 1 and reported as its own
number. It represents what an agent pays when it starts up or reconnects: MCP
handshake versus first CLI process spawn. It is measured in every Phase 9 run for
both integrations and must not be averaged into warm per-operation latency.

### 7.2 Optional extended / stress benchmarks — not implemented yet

These target the likeliest real pain points but are explicitly out of scope until
the core benchmark runs end to end. They must not block or complicate Phases 2–9.

| Benchmark | Method | Signal it exposes |
|---|---|---|
| Concurrent recalls | 5 parallel scenarios against the same adapter | MCP reuses one session while CLI spawns processes; p95 under contention |
| Failure-mode injection | Stop the Cognee API, then issue one `recall` per integration | Time-to-error and the actual message quality (traceback vs protocol error) |
| Recall payload size | Aggregate `response_chars` / `retrieved_chars` per turn | Mean bytes of recalled context, a proxy for LLM token cost |

Each would be a separate script or flag writing its own raw file under
`results/raw/`; none of them changes the schemas defined here.

---

## 8. Handoff to Phase 2

Phase 2 expands these schemas into the full deterministic dataset
(~15–20 products, 5 customers × 3–5 facts, ~10 scenarios) and adds
`scripts/generate_data.py` for reproducibility. The files committed in Phase 1 are
shape-valid samples using the final field names, so Phase 2 only adds rows — no
schema migration is required.

Phase 9 implements the core benchmark only (including cold start and the
`response_chars` metric). The three stress benchmarks in section 7.2 are deferred
and remain unimplemented until the core run is working.
