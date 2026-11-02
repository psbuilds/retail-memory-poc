# `app/memory/mcp_adapter.py` — persistent MCP integration with Cognee

## 1. File Purpose

This file implements the MCP side of the Retail Memory POC. It connects the
application to the Cognee MCP server using the MCP SDK, rather than invoking
Cognee REST endpoints directly:

```text
McpMemoryAdapter
  → MCP streamable HTTP client/session
  → Cognee MCP server (:8001/mcp)
  → Cognee API backend (:8000)
```

The CLI adapter remains a different integration path:

```text
CliMemoryAdapter → cognee-cli subprocess → Cognee API backend
```

Both paths implement the same `MemoryAdapter` contract from `base.py`, so the
agent and benchmark do not need separate logic for CLI and MCP. This file is
needed to measure the practical difference between a CLI subprocess lifecycle
and a long-lived MCP client session while keeping the Cognee backend, customer
data, product catalog, scenarios, queries, and recommendation logic the same.

**Components that interact with this file:**

| Component | Interaction |
|---|---|
| `app/memory/base.py` | Supplies the public interface, validation, dataset naming, timing, result types, and error hierarchy |
| `app/memory/factory.py` | Selects this adapter when MCP mode is configured |
| Retail agent / chatbot | Calls `remember()` and `recall()` without knowing MCP details |
| Benchmark scripts | Measures cold initialization separately from warm adapter operations |
| Cognee MCP server | Advertises and executes the `remember` and `recall` tools |

The MCP endpoint is configured with `COGNEE_MCP_URL`; it is never derived from a
customer ID, dataset name, or request payload.

## 2. Imports and Important Syntax

- `asyncio` provides the event loop used by the asynchronous MCP SDK.
- `threading.Thread` owns a dedicated loop without changing the synchronous
  `MemoryAdapter` interface.
- `Future` and `run_coroutine_threadsafe` bridge work between the synchronous
  caller and the loop thread.
- `AsyncExitStack` keeps the transport and `ClientSession` open for the full
  adapter lifetime and closes them in the correct order.
- `streamable_http_client` is the MCP SDK transport for the configured
  `http://127.0.0.1:8001/mcp` endpoint.
- `ClientSession` performs MCP initialization, tool discovery, and tool calls.
- `types.TextContent` identifies text returned by MCP tools.
- `raise ... from exc` preserves the original transport, protocol, or server
  exception as `MemoryOperationError.__cause__`.

## 3. Classes and Data Structures

### `_PersistentMcpSession`

This private class owns one persistent asynchronous MCP connection. It exists
because the public adapter interface is synchronous while the MCP SDK is
asynchronous, and because creating a transport/session per operation would not
represent a production-style agent.

Important state:

- `url` and `timeout_seconds` hold connection configuration.
- `_loop` and `_thread` own the event-loop thread.
- `_stack` owns the entered MCP transport/session contexts.
- `_session` is the initialized `ClientSession` reused for every tool call.
- `_commands` is the loop-local queue used to serialize tool calls through the
  task that entered the async contexts.
- `_startup_error` communicates handshake or tool-discovery failures back to the
  constructing synchronous caller.

### `McpMemoryAdapter(MemoryAdapter)`

This is the application-facing adapter. Its `integration = "mcp"` label appears
in results, logs, and `MemoryOperationError` metadata. It contains only MCP
configuration, tool argument mapping, response parsing, and lifecycle handling;
validation, dataset naming, timing, and common result construction remain in the
base class.

### Result and error types used here

The file does not redefine result dataclasses. It uses the shared types from
`base.py`:

- `RememberResult` reports fact count, acknowledgement size, and latency.
- `RecallResult` reports ordered fragments, response size, and latency.
- `MemoryConfigurationError` represents an unusable or closed adapter.
- `MemoryOperationError` represents initialization or tool-call failure.

This keeps CLI and MCP result shapes directly comparable.

## 4. Functions and Methods

### `_PersistentMcpSession.__init__(url, timeout_seconds)`

Stores configuration, creates a new event loop and daemon thread, starts the
thread, and waits for initialization to finish. If handshake or tool discovery
fails, the original exception is raised after cleanup so adapter setup cannot
silently continue with an unusable MCP server.

### `_PersistentMcpSession._run()`

Sets the new loop as the thread's current loop, schedules `_serve()`, runs the
loop, and closes it after `_serve()` requests shutdown. All async MCP context
managers therefore belong to one consistent event loop.

### `_PersistentMcpSession._serve()`

This is the lifecycle owner for the async contexts:

1. Creates and enters `AsyncExitStack`.
2. Enters `streamable_http_client(url)`.
3. Creates and enters `ClientSession(read_stream, write_stream)`.
4. Calls `session.initialize()` for the MCP handshake.
5. Calls `session.list_tools()` and verifies that `remember` and `recall` are
   actually advertised by the server.
6. Signals the constructor that initialization completed.
7. Waits for queued tool commands and awaits each `session.call_tool()`.
8. Exits the stack from the same task that entered it, then stops the loop.

The final point is important for AnyIO cancellation scopes: shutdown must occur
in the owning async task, not from a different thread or task.

### `_PersistentMcpSession.call_tool(name, arguments)`

Accepts an MCP tool name and dictionary of schema-matching arguments. It creates
a result future, places the command on the loop's queue, and waits synchronously
for the same persistent `ClientSession` to return the MCP result.

### `_PersistentMcpSession._submit(coroutine)`

Provides a general `run_coroutine_threadsafe` helper for submitting a coroutine
to the private loop and waiting up to `timeout_seconds`. The active tool-call
path uses the command queue so the session-owning task also controls async
execution and shutdown.

### `_PersistentMcpSession.close()`

Queues a shutdown sentinel, waits for the serving thread to finish, and thereby
causes `AsyncExitStack` to close the `ClientSession` and stream transport. This
is the clean MCP shutdown path; it does not create a replacement connection.

### `McpMemoryAdapter.__init__(...)`

Parameters:

- `mcp_url` — explicit endpoint override; otherwise `COGNEE_MCP_URL`, then
  `http://127.0.0.1:8001/mcp`.
- `recall_query_type` — retrieval mode, defaulting to `CHUNKS` or the shared
  `COGNEE_RECALL_QUERY_TYPE` override.
- `top_k` — maximum recall results, defaulting to `10`.
- `timeout_seconds` — connection and operation timeout, defaulting to `180`.

The constructor is lazy with respect to network work: it stores configuration
but does not connect until the first memory operation.

### `McpMemoryAdapter._get_connection()`

Returns the existing `_PersistentMcpSession`, or creates it once on the first
operation. A closed adapter raises `MemoryConfigurationError`. Initialization
failures, including the MCP SDK's `CancelledError` when a connection attempt is
aborted, are wrapped as `MemoryOperationError(operation="initialize")` with the
original cause chained.

### `McpMemoryAdapter._remember(customer_id, facts, dataset) -> int`

Builds one actual Cognee MCP `remember` call:

```python
{
    "data": "\n".join(facts),
    "dataset_name": dataset,
    "background": False,
    "self_improvement": True,
}
```

The complete fact list is one logical payload, matching the CLI fairness
decision. `dataset` was already produced by shared `dataset_for()` logic. The
method calls the persistent MCP session and returns the text acknowledgement
length expected by the base class.

### `McpMemoryAdapter._recall(customer_id, query, dataset) -> tuple[str, ...]`

Builds the actual server-schema arguments:

```python
{
    "query": query,
    "datasets": dataset,
    "search_type": self.recall_query_type,
    "top_k": self.top_k,
}
```

The configured `CHUNKS` mode and `top_k=10` match the CLI path where the server
supports them. The result is passed to `_fragments_from_result()` and returned
as an ordered tuple.

### `McpMemoryAdapter._call(name, arguments, customer_id)`

Routes one tool call through `_get_connection()`. Any transport, protocol, or
server exception becomes a `MemoryOperationError` containing integration,
operation, and customer metadata, with the original exception preserved through
exception chaining.

### `McpMemoryAdapter.close()`

Marks the adapter closed, detaches its persistent connection, and closes it if
one was initialized. Calling `close()` multiple times is safe. The inherited
`MemoryAdapter.__exit__()` calls this automatically for `with` blocks.

### `_text_from_result(result) -> str`

Collects text from MCP `TextContent` items and joins it with newlines. It is
shared by remember acknowledgement handling and recall parsing.

### `_fragments_from_result(result) -> tuple[str, ...]`

Splits MCP text into non-empty lines, removes known non-memory/system markers,
removes duplicate lines while preserving server order, and returns immutable
fragments for the common `RecallResult` contract.

### `_is_system_marker(text) -> bool`

Recognizes response lines beginning with `memory is still`, `cognee`, or
`knowledge graph`. These are protocol/backend status text rather than customer
preferences and should not inflate `result_count` or relevance scoring.

## 5. Execution Flow

### Adapter initialization and first operation

```text
agent / benchmark
  → McpMemoryAdapter(...)
      → stores COGNEE_MCP_URL and retrieval settings
      → no network connection yet
  → adapter.recall(customer_id, query)
      → base validates inputs
      → base calls dataset_for(customer_id)
      → base starts latency timing
      → _get_connection()
          → start private event-loop thread
          → open streamable HTTP MCP transport
          → create ClientSession
          → initialize MCP handshake
          → list_tools and verify remember/recall
      → call persistent session's recall tool
      → parse TextContent and filter system markers
      → base builds RecallResult and stops timing
```

The first operation includes genuine MCP cold-start work. The benchmark keeps
that cost separate from warm operation statistics.

### Warm remember and recall operations

```text
adapter.remember(...) / adapter.recall(...)
  → base validation, dataset naming, and timing
  → existing _PersistentMcpSession
      → queue command on its event loop
      → same ClientSession.call_tool(...)
      → same streamable HTTP transport
  → parse result
  → base constructs RememberResult or RecallResult
```

No new MCP transport, handshake, or `ClientSession` is created for warm calls.

### Failure flow

```text
MCP handshake or tool call raises original exception
  → _call / _get_connection catches it
  → raise MemoryOperationError(...) from original exception
      → integration == "mcp"
      → operation == "initialize", "remember", or "recall"
      → customer_id retained when applicable
      → __cause__ preserves protocol/backend detail
```

### Shutdown flow

```text
with McpMemoryAdapter() as adapter:
    adapter.recall(...)
  → MemoryAdapter.__exit__()
  → McpMemoryAdapter.close()
  → queue shutdown sentinel
  → owning async task exits AsyncExitStack
  → ClientSession and transport close
  → loop thread joins
```

## 6. Architecture and Design Reasoning

### Genuine MCP rather than direct REST

The adapter imports and uses the MCP SDK transport and `ClientSession`; it does
not import `httpx` or call `/api/v1/remember` or `/api/v1/recall`. The Cognee MCP
server is a separate layer that forwards the MCP tool request to the shared
Cognee API. This is the architecture being compared, not a REST call renamed
as MCP.

### Persistent lifecycle

A long-lived AI agent normally initializes its tool connection once and uses it
for many turns. The private event-loop thread is the smallest safe bridge that
preserves that behavior without converting the whole synchronous application
contract to async. It also makes cold handshake cost and warm tool-call cost
observable separately.

### Tool schema verification

The server's actual schemas are used: `remember` receives `data` and
`dataset_name`; `recall` receives a string `datasets`, `search_type`, and
`top_k`. `list_tools()` is performed during initialization so a misconfigured
server fails early instead of producing misleading benchmark failures later.

### Fair comparison with CLI

Both adapters share:

| Decision | Fairness effect |
|---|---|
| `MemoryAdapter` public contract | Agent and benchmark logic are identical |
| `dataset_for(customer_id)` | Both paths address the same dataset name |
| One combined fact payload | Remember semantics and payload size are equivalent |
| `CHUNKS` and `top_k=10` | Retrieval settings do not become the experimental variable |
| Shared Cognee API backend | Memory engine and stored data remain common |
| Separate cold-start metric | MCP handshake is not hidden inside warm latency |

The lifecycle itself is intentionally different: CLI pays subprocess startup on
each invocation, while MCP pays session setup once and reuses the connection.
That is a real developer and agent trade-off, not an artificial normalization.

### Production-style trade-offs

The persistent session reduces repeated MCP setup for multi-turn agents, but it
adds lifecycle ownership, an event-loop bridge, server availability
requirements, and clean-shutdown responsibility. The CLI adapter is simpler to
reason about per call but has repeated process startup and CLI parsing overhead.
These implementation differences are precisely the developer-perspective
trade-offs this POC records alongside latency, reliability, error visibility,
memory relevance, and agent-level recommendation outcomes.

### Known project limitation

The current Cognee backend configuration disables backend access control, so
dataset labels do not fully isolate vector results between customers. Both CLI
and MCP use the same backend and are affected equally; this remains a documented
fairness caveat rather than an MCP-specific workaround.
