# Common Memory Interface (base.py)

## File Purpose
This module defines the minimal memory interface that both the CLI and MCP adapters must implement. The retail AI agent and benchmark depend on this interface only — never on Cognee CLI commands or MCP protocol details. This abstraction enables switching between integration methods without changing the agent logic.

## Classes and Data Structures

### `MemoryAdapterError`
- **Responsibility**: Base error class for any failure raised by a memory adapter.
- **Why it exists**: Carries the integration and operation that failed so error visibility can be compared across CLI and MCP without parsing log lines. The original exception is always chained (`raise ... from exc`), so `__cause__` keeps the backend's own message.
- **Relationship**: Subclassed by `MemoryConfigurationError` and `MemoryOperationError`.

### `MemoryConfigurationError`
- **Responsibility**: The adapter cannot be constructed from the current configuration.
- **Why it exists**: Used when, for example, `cognee-cli` is not found or the MCP URL is invalid.

### `MemoryOperationError`
- **Responsibility**: A remember/recall call failed while talking to the memory backend.
- **Why it exists**: Wraps adapter failures with integration context so they can be counted and categorized in benchmarks.

### `RememberResult` (dataclass)
- **Fields**: `integration`, `customer_id`, `dataset`, `fact_count`, `response_chars`, `latency_ms`
- **Why it exists**: Normalized outcome of storing customer facts. Both adapters return `RememberResult` instances with identical field names, so latency and count data are comparable across integration methods.

### `RecallResult` (dataclass)
- **Fields**: `integration`, `customer_id`, `dataset`, `query`, `fragments`, `latency_ms`
- **Properties**: `text` (joined fragments), `result_count` (number of fragments), `response_chars` (character count of joined text)
- **Why it exists**: Normalized outcome of a memory lookup. The `fragments` tuple keeps backend items separate, while `result_count` can detect a successful-but-empty recall. The `text` property is the joined context the agent actually receives.

### `dataset_for(customer_id)`
- **Inputs**: `customer_id` (str)
- **Behavior**: Returns `f"{DATASET_PREFIX}{customer_id}"` where `DATASET_PREFIX = "retail_"`.
- **Why it exists**: Single point of dataset naming so both adapters use the same scope derivation. If the CLI and MCP adapters computed the scope themselves, they could read different memory while looking correct.

### `_validate_customer_id(customer_id)`
- **Why it exists**: Ensures customer IDs contain only letters, digits, underscores, or hyphens.

### `_validate_facts(facts)`
- **Why it exists**: Ensures facts are a sequence of non-empty strings.

### `_validate_query(query)`
- **Why it exists**: Ensures the query is a non-empty string.

### `MemoryAdapter` (abstract base class)
- **Responsibility**: Defines the shared interface for both CLI and MCP integrations.
- **Key methods**:
  - `remember(customer_id, facts)`: Public wrapper that validates input, calls `_remember`, measures latency, and returns a `RememberResult`.
  - `recall(customer_id, query)`: Public wrapper that validates input, calls `_recall`, measures latency, and returns a `RecallResult`.
  - `_remember(customer_id, facts, dataset)`: Abstract method implemented by each adapter.
  - `_recall(customer_id, query, dataset)`: Abstract method implemented by each adapter.
  - `close()`: Releases integration resources.
- **Timing**: Both adapters get latency from the same wrapper in the base class. The measured region starts at the public call (outside the adapter) and ends after the integration work.
- **Result normalization**: Recalled fragments are joined and counted here, so `result_count` and `response_chars` are comparable across integrations.

### `_log_failure(operation, customer_id, exc)`
- **Why it exists**: Logs warnings with integration name, operation, customer ID, error type, and error message.

## Functions and Methods

The module provides the plumbing that makes both integrations measurable on identical terms:
- Dataset naming via `dataset_for()`
- Input validation via `_validate_customer_id()`, `_validate_facts()`, `_validate_query()`
- Timing via `perf_counter()` in the base class
- Error wrapping via `MemoryOperationError`
- Logging via `_log_failure()`

## Architecture and Design Reasoning
This interface is the linchpin of the POC. Without a common abstraction, switching between CLI and MCP would require changing agent code, which would confound the comparison. By centralizing validation, timing, result normalization, and error formatting in one module, both adapters can be evaluated independently while producing identical-shaped output. The design ensures that the only variable between the two integration methods is the underlying mechanism (CLI commands vs. MCP protocol), not the interface or measurement framework.