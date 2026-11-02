# Project progress — Retail Memory POC

Phases 1–3 (evaluation plan and schemas, deterministic dataset, common memory
interface) are recorded in `docs/evaluation_plan.md`; this file tracks the
implementation phases from Phase 4 onward.

---

## Phase 4 — Direct/CLI Cognee adapter

**Goal.** Implement the CLI integration so it satisfies the common memory
interface in `app/memory/base.py` and talks to the same central Cognee API server
the MCP path will use, without letting CLI details leak into the agent.

**Implementation summary**

- `CliMemoryAdapter` (integration id `cli`) implements only the two protected
  hooks `_remember` and `_recall`. Validation, timing, dataset naming, logging and
  result construction stay in the base class.
- Both operations delegate to the API with the CLI's global `--api-url` flag, so
  no second engine or local database is created:
  - `cognee-cli --api-url <url> remember <fact>... --dataset-name retail_<id>`
  - `cognee-cli --api-url <url> recall <query> --datasets retail_<id> --query-type CHUNKS --top-k 10 --output-format json`
- Recall is parsed from the CLI's JSON output. The CLI prints a human-readable
  preamble that itself contains `[` (the dataset list), so the payload is located
  by decoding from each candidate bracket instead of taking the first one.
- `system` entries (Cognee markers such as "memory still warming up") are dropped
  from fragments and logged, so a marker-only response is reported as an empty
  recall rather than as retrieved memory.
- Subprocess failures, non-zero exits and timeouts raise `MemoryOperationError`
  with `integration`/`operation`/`customer_id` attached and the original
  exception chained; the message carries the CLI's own last error line.

**Files changed**

| File | Change |
|---|---|
| `app/memory/cli_adapter.py` | Implemented the adapter (was empty) |
| `tests/test_cli_adapter.py` | New; 12 unit tests with `subprocess.run` mocked |
| `docs/project_progress.md` | New; this entry |
| `docs/decisions.md` | New; the decisions taken during this phase |

`app/memory/base.py`, `tests/test_memory.py` are unchanged from Phase 3.
`app/memory/mcp_adapter.py`, `factory.py`, `chatbot.py`, `products.py`,
`scripts/`, `data/`, `results/` were not touched.

**Tests and verification**

- `uv run python -m pytest -q` → **27 passed** (15 interface contract tests +
  12 CLI adapter tests). No service required.
- CLI syntax verified against the installed CLI rather than documentation:
  `cognee-cli --version` → 1.6.6; `remember --help` and `recall --help` inspected;
  `--api-url` is a global option and must precede the subcommand.
- Live smoke test through the adapter, using a dedicated non-benchmark dataset
  (`retail_smoke_phase4b`, facts about purple umbrellas), on a fresh dataset:
  - `remember` returned `fact_count=2`, `response_chars=231`, **6264.7 ms**
  - `recall` returned 4 fragments, **8735.4 ms**, and ranked the purple-umbrella
    fact first
  - both smoke datasets created during probing were deleted afterwards; the API
    now holds only `__smoke_test__` from Phase 0.
- Failure path exercised for real against a dead port: the CLI exits 1 and prints
  `Error: Could not reach the Cognee API at http://127.0.0.1:9999: [Errno 61]
  Connection refused`, which the adapter surfaces as a `MemoryOperationError`.

**Actual CLI/API configuration used**

| Setting | Value |
|---|---|
| Cognee version | 1.6.6 (`cognee[gliner]` from `uv.lock`) |
| CLI binary | `.venv/bin/cognee-cli` (discovered on PATH, else beside the interpreter) |
| API server | `uv run python -m cognee.api.client` on `http://127.0.0.1:8000` (`/` → `200`) |
| `COGNEE_API_URL` | `http://127.0.0.1:8000` |
| `ENABLE_BACKEND_ACCESS_CONTROL` | `false` (single-user, no auth) |
| Recall mode | pinned `CHUNKS` (`COGNEE_RECALL_QUERY_TYPE` override) |
| `top_k` | 10 |
| Subprocess timeout | 180 s, `cwd` = repository root |

**Limitations and issues discovered**

1. **Dataset scoping is not enforced by this backend.** With
   `ENABLE_BACKEND_ACCESS_CONTROL=false`, every dataset shares one vector index:
   `--datasets retail_<id>` does not filter results, it only labels them with the
   requested dataset. Verified through the CLI and through a direct
   `POST /api/v1/recall` (`datasets` and `dataset_ids` alike), and it matches
   `cognee.modules.search.methods.search`'s note that scoping requires backend
   access control. Consequence: per-customer memory is not isolated, and the smoke
   recall returned two facts belonging to other datasets. Both integrations share
   the backend, so the comparison stays fair, but `relevance_score` can be
   inflated by other customers' facts. Enabling access control would force
   authentication, which the project scope excludes. Decision needed before
   Phase 9 — see `docs/decisions.md` D4.
2. **Fixed per-invocation startup cost.** `cognee-cli --version` alone takes
   3.7 s (and 8.2 s on a cold second run); this is paid before any HTTP request,
   so it dominates CLI latency. It is genuine integration cost, not harness
   overhead, and is the main thing cold start and warm runs will expose.
3. **One document per dataset.** `remember` with text arguments writes a single
   `text_0.txt` document. Re-adding *identical* facts is a no-op success, but
   re-adding *different* facts to the same dataset returns `409
   DocumentUpdateRequiredError`. The benchmark must therefore reset a dataset
   between seed runs, or always re-submit the same facts.
4. **No LLM configured.** Completion search types (`HYBRID_COMPLETION`,
   `GRAPH_COMPLETION`) return a rendered prompt rather than an answer, which is a
   second reason retrieval is pinned to the non-generative `CHUNKS` mode.
5. `BENCHMARK_RUNS=0` in `.env` would produce zero measured iterations in
   Phase 9; it needs a real value before the benchmark runs.

**What Phase 5 depends on**

- The MCP adapter must pin the same `CHUNKS` retrieval mode and `top_k`, and use
  `dataset_for()` from `app/memory/base.py` rather than building the dataset name
  itself.
- It must target the same API server and must not create its own memory store.
- It must mirror two behaviours chosen here so the paths stay comparable:
  dropping `system` markers from fragments, and treating the whole fact list as
  one remember payload.
- The Phase 0 MCP tool schemas (`remember(data, dataset_name, ...)`,
  `recall(query, datasets, search_type)`) map onto the same two hooks.

---

## Phase 5 — MCP Cognee adapter (corrected)

**Goal.** Implement the MCP integration so it satisfies the common memory
interface in `app/memory/base.py` and communicates with the same central Cognee
API server the CLI path uses, through the MCP server's tool interface.

**Implementation summary**

- The previous REST implementation was removed. The adapter now uses
  `streamable_http_client` and `ClientSession` from the MCP SDK against the
  dedicated `COGNEE_MCP_URL` endpoint (default `http://127.0.0.1:8001/mcp`).
- A private event-loop thread owns one transport and one session. The session is
  initialized lazily, performs MCP handshake and tool discovery once, and is
  reused for every subsequent operation on the adapter.
- The actual server schemas are used: one `remember` call sends the complete fact
  list as `data`; `recall` sends `query`, dataset, `search_type=CHUNKS`, and
  `top_k=10`.
- MCP text results are normalized and system marker lines are excluded. Tool and
  initialization failures become chained `MemoryOperationError` instances.
- `close()` exits the MCP context stack and stops the event-loop thread; the
  existing `MemoryAdapter` context manager therefore releases the persistent
  connection correctly.

**Files changed**

| File | Change |
|---|---|
| `app/memory/mcp_adapter.py` | Replaced REST calls with persistent MCP client/session |
| `tests/test_mcp_adapter.py` | Replaced HTTP mocks with MCP session/lifecycle tests |
| `docs/project_progress.md` | Updated with Phase 5 entry |
| `docs/explanations/mcp_adapter.md` | Updated for persistent MCP architecture |
| `docs/decisions.md` | Added persistent lifecycle and cold/warm decisions |

`app/memory/base.py`, `tests/test_memory.py` are unchanged from Phase 3.
`app/memory/cli_adapter.py`, `scripts/`, `data/`, `results/` were not touched.

**Verification**

- Unit tests cover endpoint configuration, handshake/tool discovery through the
  session owner, argument mapping, session reuse, shutdown, context-manager
  cleanup, error wrapping, and exception chaining.
- Live verification completed with Cognee API on `:8000` and the local MCP
  server on `:8001/mcp`, using dedicated dataset `retail_smoke_phase5_lifecycle2`:
  MCP handshake and tool discovery succeeded; one `remember` and two `recall`
  calls succeeded; the adapter reported the same connection object for both
  recalls and shutdown joined the session thread cleanly. Benchmark datasets
  were not used.

**Actual MCP/API configuration used**

| Setting | Value |
|---|---|
| Cognee version | 1.6.6 (`cognee[gliner]` from `uv.lock`) |
| MCP endpoint | `http://127.0.0.1:8001/mcp` via `COGNEE_MCP_URL` |
| API server | `http://127.0.0.1:8000` behind the MCP server |
| Recall mode | pinned `CHUNKS` (`COGNEE_RECALL_QUERY_TYPE`) |
| `top_k` | 10 |
| HTTP timeout | 180 s |

**Limitations and issues discovered**

1. **Per-customer dataset isolation is not enforced by this backend.** With
   `ENABLE_BACKEND_ACCESS_CONTROL=false`, all datasets share one vector index.
   Neither the CLI nor the MCP path can filter results by dataset name; `--datasets`
   only labels results. Confirmed through both CLI (`cognee-cli`) and direct
   `POST /api/v1/recall`. The shared backend means `relevance_score` can be
   inflated by other customers' facts, and "irrelevant memory" is harder to
   isolate. Both paths are affected equally, so the comparison stays fair, but
   interpretation of benchmark results must treat per-customer isolation as an
   out-of-scope limitation (see `docs/decisions.md` D4).
2. **Transport overhead.** MCP pays streamable-HTTP framing and one initial
   handshake, while CLI pays process startup per operation. Both are genuine
   integration costs; cold initialization remains separate from warm calls.

**What Phase 6 depends on**

- Cold-start and end-to-end Phase 9 benchmark implementation, including
  `cold_start` measurement and `response_chars` payload tracking.
- Final decision on D4 (whether to document the scoping limitation or enable
  backend access control, which would introduce authentication outside project scope).
- Factory wiring in `factory.py` so `MEMORY_MODE=mcp` selects this adapter.

---

## Phase 6 — End-to-End Agent Benchmark

[same structure as Phase 4 and Phase 5 entries]
