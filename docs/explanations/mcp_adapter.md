# docs/explanations/mcp_adapter.md — MCP Cognee Adapter

## 1. File Purpose

**What this file solves:**

The `app/memory/mcp_adapter.py` file implements the MCP (Model-Context-Protocol) integration path for the Retail Memory POC. Its purpose is to allow the retail AI agent to store and retrieve customer memories by communicating with the central Cognee API server through the MCP server's tool interface, rather than shelling out to `cognee-cli`. This file is the MCP counterpart to `cli_adapter.py` and together they satisfy the project's core requirement: switching between CLI/direct and MCP memory implementations without changing the agent logic or the common memory interface.

**Why it is needed in the overall project architecture:**

The POC evaluates two integration mechanisms — CLI/direct and MCP — while keeping all other variables identical (same Cognee backend, same customer data, same product catalog, same scenarios, same recommendation logic). The `McpMemoryAdapter` in this file is the MCP path's implementation of the `MemoryAdapter` ABC from `app/memory/base.py`. Without it, only the CLI path would be operational, and the fair comparison between the two integration mechanisms would be impossible.

**Which other components interact with it:**

- **`app/main.py`** — the project's entry point, which can instantiate either adapter via the factory or configuration.
- **`app/memory/base.py`** — defines the `MemoryAdapter` ABC; `McpMemoryAdapter` inherits from it and implements the two protected hooks `_remember` and `_recall`. The base class provides validation, dataset naming via `dataset_for()`, timing, logging, result construction (`RememberResult`/`RecallResult`), and error wrapping.
- **`app/memory/factory.py`** — (currently a stub) likely selects the adapter mode based on `MEMORY_MODE` configuration; the factory would return a `McpMemoryAdapter` instance when `MEMORY_MODE=mcp`.
- **Retail AI agent / chatbot** — depends only on the `MemoryAdapter` interface; it calls `adapter.remember()` and `adapter.recall()` without knowing whether the underlying mechanism is CLI or MCP.
- **Benchmark scripts** (`scripts/benchmark.py`) — measure latency and success/failure by calling the public `remember()`/`recall()` methods; results are comparable between the two adapters because both conform to the same interface.
- **Tests** (`tests/test_mcp_adapter.py`) — verify the adapter's behavior in isolation.

## 2. Classes and Data Structures

### `McpMemoryAdapter`

- **Class declaration**: `class McpMemoryAdapter(MemoryAdapter)`
- **Integration identifier**: `integration = "mcp"` — a class variable used by the base class for logging and error reporting (e.g., `integration=mcp` appears in `MemoryOperationError` messages).
- **`__init__` parameters**:
  - `api_url` (optional, default `"http://127.0.0.1:8000"`) — the base URL of the central Cognee API server. Both the CLI and MCP paths must hit the same backend, so this URL is shared.
  - `recall_query_type` (optional, default `"CHUNKS"`) — the search type passed to the API's `/api/v1/recall` endpoint. Matched to the CLI adapter's retrieval settings for fairness.
  - `top_k` (optional, default `10`) — the maximum number of fragments to return from recall. Matched to the CLI adapter's `top_k=10` so both paths retrieve the same number of results.
  - `timeout_seconds` (optional, default `180.0`) — timeout for outbound HTTP calls to the Cognee API.
- **Inheritance**: Inherits from `MemoryAdapter` ABC. The base class provides:
  - Public `remember()` method that validates inputs, calls `dataset_for()`, measures wall-clock latency, calls the protected `_remember()`, and wraps results in `RememberResult`.
  - Public `recall()` method with the same pattern, producing `RecallResult`.
  - `RememberResult` dataclass: `integration`, `customer_id`, `dataset`, `fact_count`, `response_chars`, `latency_ms`.
  - `RecallResult` dataclass: `integration`, `customer_id`, `dataset`, `query`, `fragments` (tuple of strings), plus computed properties `text`, `result_count`, `response_chars`.
  - Error hierarchy: `MemoryAdapterError` > `MemoryConfigurationError`, `MemoryOperationError`. The base class catches exceptions and chains them with `raise ... from exc`, preserving the original CLI/API error message.
  - `_log_failure()` method for warning-log integration failures.

No other classes or data structures are defined in this file.

## 3. Functions and Methods

### `_remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int`

- **Inputs**:
  - `customer_id`: the customer identifier (validated by the base class before this hook is called).
  - `facts`: a tuple of fact strings to store as memory for this customer. Each fact becomes a separate text item in the Cognee dataset named by `dataset`.
  - `dataset`: the memory scope, derived via `dataset_for(customer_id)` from the base class (format: `retail_<customer_id>`). Both adapters use the same function, ensuring the dataset name is consistent.
- **What it does step by step**:
  1. Builds a multipart form upload: for each fact, creates a file entry named `text_{i}.txt` (e.g., `text_0.txt`, `text_1.txt`) with the fact content and `text/plain` MIME type. This mirrors what `cognee-cli` does internally.
  2. Sends an HTTP POST to `{self.api_url}/api/v1/remember` with `files` (the fact files) and `data={"datasetName": dataset}`.
  3. Calls `response.raise_for_status()` to surface HTTP errors (4xx/5xx) as exceptions.
  4. Returns `len(facts)` — the number of facts submitted — as the "acknowledgement length" expected by the base class's `remember()` method (which uses this as `response_chars`).
- **Return value**: `int` — the count of facts stored. The base class uses this as `RememberResult.fact_count`.
- **Why it is needed**: This is the MCP path's hook for storing customer preferences. The base class's public `remember()` method cannot be implemented generically because the actual storage mechanism differs between CLI (subprocess) and MCP (HTTP POST). This hook isolates that difference.

### `_recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]`

- **Inputs**:
  - `customer_id`: the customer identifier (validated by the base class).
  - `query`: the search query string; the Cognee API uses this to find relevant memories.
  - `dataset`: the memory scope, same `retail_<customer_id>` format from `dataset_for()`.
- **What it does step by step**:
  1. Builds a JSON payload: `{"query": query, "search_type": self.recall_query_type, "top_k": self.top_k, "datasets": [dataset]}`.
  2. Sends an HTTP POST to `{self.api_url}/api/v1/recall` with `json=payload`.
  3. Calls `response.raise_for_status()`.
  4. Parses the JSON response body. The API returns a list of search result items, each with fields like `text`, `content`, `source`, etc.
  5. Iterates over the results and:
     - Skips entries where `source == "system"` (system markers, logged at warning level). This is the same filtering logic the CLI adapter uses to exclude non-memory items.
     - Extracts the `text` field (or falls back to `content`). Strips whitespace.
     - If the stripped text is non-empty, appends it to a local `fragments` list.
  6. Returns `tuple(fragments)` — an immutable tuple of memory fragments relevant to the query.
- **Return value**: `tuple[str, ...]` — the raw context fragments. The base class further cleans these (strips each fragment, discards empties) and constructs a `RecallResult` with `result_count = len(cleaned_fragments)`, `text = FRAGMENT_SEPARATOR.join(cleaned_fragments)`, and `response_chars`.
- **Why it is needed**: This is the MCP path's hook for retrieving customer memories. The base class cannot implement the recall logic generically because the CLI uses subprocess output parsing and the MCP uses JSON API response parsing.

### `__init__` (constructor)

- Initializes the four configuration fields (`api_url`, `recall_query_type`, `top_k`, `timeout_seconds`) and stores them as instance attributes.
- No abstract or virtual methods are called here; it's a plain data initialization block.

## 4. Important Python Syntax and Concepts

### `from __future__ import annotations`

- Makes all annotations strings by default (PEP 563). This avoids premature evaluation of type hints that might reference names not yet in scope. Used consistently across all three adapter files (`base.py`, `cli_adapter.py`, `mcp_adapter.py`).

### Type hints throughout

- Function signatures use `str`, `tuple[str, ...]`, `int`, `float`, `list[tuple[str, tuple[str, str, str]]]`, etc.
- The `__init__` method uses `str | None = None` syntax (Python 3.10+ union type syntax).
- Return types are declared for every public and protected method. This is part of the project's code quality rule: "Prefer clear names. Type hints. Minimal dependencies."

### `class McpMemoryAdapter(MemoryAdapter)`

- Classic Python class inheritance. `McpMemoryAdapter` IS-A `MemoryAdapter`, fulfilling the contract that both adapters depend on the common abstraction, not on Cognee CLI or MCP protocol details.

### `httpx.Client(timeout=self.timeout_seconds) as client:`

- Uses the `httpx` library (already a dependency through Cognee) to create an HTTP client with a per-request timeout. The `with` statement ensures the client connection is properly closed after each operation.
- This pattern is used in both `_remember` and `_recall` — a new client is created each call. This is intentional: the project avoids hidden global state and keeps the timing measurement clean (each call's overhead is measured independently).

### `response.raise_for_status()`

- Standard `httpx` pattern: raises an `httpx.HTTPStatusError` if the HTTP response status is 4xx or 5xx. This exception is caught by the base class's public `remember()`/`recall()` methods and re-raised as `MemoryOperationError` with the integration/operation/customer_id context attached, and the original exception chained via `raise ... from exc`.

### `for i, fact in enumerate(facts):` / `files.append(("data", (fname, fact, "text/plain")))`

- Enumerate over the facts to assign each a sequential filename (`text_0.txt`, `text_1.txt`, etc.), mirroring what `cognee-cli` does internally. The form field name is `"data"` (matching Cognee's expected multipart field), and each entry is a 3-tuple `(name, (filename, content, mime_type))` as required by `httpx`.

### `if isinstance(entry, dict) and entry.get("source") == "system":`

- Type-safe check: first confirms the entry is a dict, then checks if the `source` field equals `"system"`. System markers are non-memory entries returned by the Cognee API; both the CLI and MCP adapters filter them out the same way.

### `tuple(fragments)`

- Converts the local `list[str]` of fragments into a `tuple[str, ...]` before returning. The base class's `RecallResult` expects `fragments` to be a tuple (immutable, as documented). This also ensures the return type matches the abstract method signature `_recall` → `tuple[str, ...]`.

## 5. Execution Flow

### Remember flow (public method → hook → API)

1. **Agent/benchmark calls** `adapter.remember(customer_id, facts)` (inherited from `MemoryAdapter` in `base.py`).
2. **Base class validates** `customer_id` (via `_validate_customer_id`) and `facts` (via `_validate_facts`).
3. **Base class computes** `dataset = dataset_for(customer_id)` → e.g., `"retail_customer_001"`.
4. **Base class starts timing** `perf_counter()`.
5. **Base calls** `self._remember(customer_id, cleaned, dataset)` — dispatches to `McpMemoryAdapter._remember()`.
6. **`_remember` builds multipart form**: each fact → `text_{i}.txt` file entry; form field `"datasetName": dataset`.
7. **`_remember` POSTs** to `{api_url}/api/v1/remember` via `httpx.Client`.
8. **`response.raise_for_status()`** — if the API returns 409 (e.g., duplicate item) or 500, an exception is raised.
9. **`_return len(facts)`** — the acknowledgement length.
10. **Base class stops timing** and constructs `RememberResult(integration="mcp", customer_id=..., dataset=..., fact_count=len(cleaned), response_chars=max(0, int(response_chars)), latency_ms=...)`.
11. **Result returned** to the caller.

### Recall flow (public method → hook → API)

1. **Agent/benchmark calls** `adapter.recall(customer_id, query)` (inherited from `MemoryAdapter`).
2. **Base class validates** `customer_id` and `query`.
3. **Base class computes** `dataset = dataset_for(customer_id)`.
4. **Base class starts timing**.
5. **Calls** `self._recall(customer_id, query, dataset)` — dispatches to `McpMemoryAdapter._recall()`.
6. **`_recall` builds payload**: `{"query": query, "search_type": "CHUNKS", "top_k": 10, "datasets": [dataset]}`.
7. **POSTs** to `{api_url}/api/v1/recall` via `httpx.Client`.
8. **`response.raise_for_status()`**.
9. **Parses JSON response**: iterates over the returned list of result items.
10. **Filters system markers** (`source == "system"`), extracts `text` or `content`, strips whitespace, discards empty strings.
11. **Returns** `tuple(fragments)`.
12. **Base class stops timing**, cleans fragments (strips each, discards empties), constructs `RecallResult(integration="mcp", customer_id=..., dataset=..., query=..., fragments=cleaned, latency_ms=...)`.
13. **Result returned** to the caller.

### Data flow summary

- **Input**: Customer ID + facts (for remember) or query (for recall).
- **Validation**: Base class ensures non-empty, well-formed inputs.
- **Dataset naming**: `retail_<customer_id>` — shared function, same result for both adapters.
- **Integration-specific work**: CLI → subprocess; MCP → HTTP POST + JSON parsing.
- **Output**: `RememberResult` or `RecallResult` with identical field structure, so the benchmark and agent code cannot distinguish the integration mechanism.

## 6. Architecture and Design Reasoning

### How this file supports the CLI-vs-MCP comparison

The entire project's research question is: *How do CLI/direct integration and MCP integration compare when connecting an AI application to the same memory system?* This file is the MCP half of that comparison. The design principles that make the comparison fair are:

1. **Same backend**: Both adapters communicate with the identical central Cognee API at `http://127.0.0.1:8000`. The MCP server acts as a proxy, forwarding tool calls over HTTP to the same backend that `cognee-cli` hits directly. No second memory engine is started; there is no separate local database.

2. **Same dataset naming**: Both adapters use `dataset_for(customer_id)` from `base.py`. The dataset scope is `retail_<customer_id>`, derived in one place only. If both adapters computed their own scope, they could diverge and invalidate the comparison.

3. **Same retrieval settings**: `recall_query_type = "CHUNKS"` and `top_k = 10` in the MCP adapter match the CLI adapter's configuration. Both retrieve up to 10 chunks in CHUNKS format.

4. **Same result contract**: The public `remember()` and `recall()` methods in `base.py` construct `RememberResult` and `RecallResult` with identical fields (`integration`, `customer_id`, `dataset`, `fact_count`/`result_count`, `response_chars`, `latency_ms`). The agent and benchmark code depend only on these fields, not on how the underlying API was called.

5. **Integration-specific details contained**: All MCP protocol details — the HTTP POST formats, the multipart form upload for remember, the JSON response parsing for recall, the `source == "system"` marker filtering — are inside this single file. The rest of the project (agent, benchmark, factory) knows nothing about `httpx`, MCP tool calls, or CLI subprocess syntax.

6. **Error handling consistency**: Both adapters raise `MemoryOperationError` (from `base.py`) when the HTTP call or subprocess fails. The original error is chained via `raise ... from exc`, so `__cause__` preserves the Cognee API's own message (e.g., `Could not reach the Cognee API... Connection refused`). The `integration` and `operation` fields make it easy to filter or group errors by integration method in benchmark analysis.

7. **No duplicated shared logic**: Validation (`_validate_customer_id`, `_validate_facts`, `_validate_query`), dataset naming (`dataset_for`), timing, and logging are all in `base.py`. If the MCP adapter duplicated any of that, the two paths could drift apart and the comparison would be compromised. The adapter implements *only* the integration-specific hooks: `_remember` and `_recall`.

### Why the MCP adapter is structured this way

- **Minimal dependencies**: Uses only `httpx` (already required by Cognee's own HTTP client) and standard library types. No new packages are introduced.
- **Explicit configuration**: The `__init__` accepts optional parameters for `api_url`, `recall_query_type`, `top_k`, and `timeout_seconds`. This makes the adapter configurable without hardcoding, and the factory or configuration file can set these once.
- **Async-sync neutrality**: The protected hooks `_remember` and `_recall` are synchronous (`def`, not `async def`). The base class's public methods are also synchronous. This keeps the interface simple; if async were needed, the whole `MemoryAdapter` ABC would need to change, which would affect the CLI adapter too. The current design keeps both adapters synchronous and synchronous-friendly.
- **Testability**: Because the hooks are plain functions that make HTTP calls, they can be unit-tested by mocking `httpx.Client` or `httpx.post`. The existing `tests/test_mcp_adapter.py` does exactly this — it mocks the HTTP layer so the tests don't require a running Cognee service.

### Relationship to the CLI adapter

| Aspect | CLI Adapter (`cli_adapter.py`) | MCP Adapter (`mcp_adapter.py`) |
|---|---|---|
| **Integration mechanism** | `subprocess.run(["cognee-cli", ...])` | `httpx.Client.post()` to `/api/v1/remember` / `/api/v1/recall` |
| **Dataset naming** | Uses `dataset_for()` from `base.py` | Uses `dataset_for()` from `base.py` |
| **Retrieval settings** | `top_k=10`, `CHUNKS` (CLI flags) | `top_k=10`, `CHUNKS` (payload JSON) |
| **Result parsing** | Parses CLI stdout JSON; drops system markers | Parses API JSON response; drops `source == "system"` entries |
| **Error handling** | Wraps subprocess errors in `MemoryOperationError` | Wraps `httpx` errors in `MemoryOperationError` |
| **Public API** | Same `remember()` / `recall()` from `MemoryAdapter` | Same `remember()` / `recall()` from `MemoryAdapter` |

Both adapters conform to the exact same `MemoryAdapter` ABC. The only difference is the hook implementation, and the project's fairness rules require that everything else — validation, timing, result construction, error hierarchy, dataset naming — be identical.

## Files Created/Updated

- **Created**: `docs/explanations/mcp_adapter.md` — this file.
- **Previously created** (Phase 4): `docs/explanations/base.md`, `docs/explanations/cli_adapter.md`.
- **Previously created** (Phase 3): `docs/explanations/base.md` (common interface).

## Missing Explanation Files

The following implementation files currently **lack** explanation documents in `docs/explanations/`, even though they are materially implemented:

| Source file | Explanation file | Status |
|---|---|---|
| `app/memory/base.py` | `docs/explanations/base.md` | ✅ Created (Phase 3) |
| `app/memory/cli_adapter.py` | `docs/explanations/cli_adapter.md` | ✅ Created (Phase 4) |
| `app/memory/mcp_adapter.py` | `docs/explanations/mcp_adapter.md` | ✅ **Just created** |
| `scripts/generate_data.py` | (none) | ⚠️ No explanation created (generator script, not core interface) |
| `app/main.py` | (none) | ⚠️ Stub file, 2 lines; explanation not required per the minimal setup |
| `app/products.py` | (none) | ⚠️ Product data module; explanation not created |
| `app/chatbot.py` | (none) | ⚠️ Currently empty/stub; no explanation needed until implemented |

The AGENTS.md requirement states: "Create a separate explanation file for each **implemented source file**." The three core memory interface files (`base.py`, `cli_adapter.py`, `mcp_adapter.py`) are now all documented. The data and stub files are lower priority and were not created in earlier phases; if they are materially modified in a future phase, explanations should be added then.