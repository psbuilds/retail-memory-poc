# MCP Adapter (mcp_adapter.py)

## File Purpose
This adapter represents the "MCP integration" path. It connects to the configured Cognee MCP endpoint using the MCP Streamable HTTP protocol and communicates with the central Cognee backend through the MCP server. The adapter is an integration layer, not a new memory system — it delegates all memory logic to the central Cognee API. Everything MCP-specific lives here: the session management, tool calling, and protocol detail. The rest of the application sees only `MemoryAdapter`.

## Classes and Data Structures

### `McpMemoryAdapter(MemoryAdapter)`
- **integration = "mcp"**: Class variable identifying this adapter to the framework, benchmark, and error reporting.
- **Constructor parameters**:
  - `mcp_url` (optional, from `COGNEE_MCP_URL` env var or default `http://127.0.0.1:8001/mcp`): The Streamable HTTP endpoint of the Cognee MCP server.
  - `recall_query_type` (optional, from `COGNEE_RECALL_QUERY_TYPE` env var or default `"CHUNKS"`): Retrieval mode used for recall. Must match the CLI adapter's setting for fair comparison.
  - `top_k` (optional, from env or default `10`): Number of top results to return.
  - `timeout_seconds` (optional, from env or default `180.0`): Timeout for MCP tool calls.

### Internal class: `_PersistentMcpSession`
- **Responsibility**: Owns an async MCP transport/session on a private event-loop thread. This is needed because the MCP protocol uses Streamable HTTP, which requires a persistent session per adapter instance.
- **Threading**: Runs on a daemon thread with its own `asyncio` event loop. The `_run()` method starts the loop and creates the initial MCP connection, validating that the server has the required `"remember"` and `"recall"` tools.
- **Why it exists**: The MCP protocol is stateful and session-based. A persistent session per adapter avoids the overhead of re-establishing the connection on every remember/recall call, which would confound latency measurements.

### Key internal methods

#### `_remember(customer_id, facts, dataset)`
- **Inputs**: Customer ID, facts tuple, dataset name.
- **Behavior**: Calls the MCP `"remember"` tool with `{data: "\n".join(facts), dataset_name: dataset, background: False, self_improvement: True}`. Returns the character count of the response text.
- **Why it exists**: This is the adapter's core responsibility — storing facts via the MCP protocol. The `data` field joins facts with newlines, and `self_improvement: True` enables Cognee's automatic graph improvement.

#### `_recall(customer_id, query, dataset)`
- **Inputs**: Customer ID, query string, dataset name.
- **Behavior**: Calls the MCP `"recall"` tool with `{query: query, datasets: dataset, search_type: recall_query_type, top_k: top_k}`. Returns a tuple of fragment strings.
- **Why it exists**: This is the adapter's core responsibility — retrieving memory via the MCP protocol. The `search_type` is pinned to `CHUNKS` so both integrations issue the same retrieval and the measured work is comparable.

#### `_call(name, arguments, customer_id)`
- **Inputs**: Tool name ("remember" or "recall"), arguments dict, customer ID.
- **Behavior**: Gets the persistent MCP connection and calls the tool via `call_tool()`. Wraps any non-`MemoryAdapterError` exceptions in `MemoryOperationError` with integration/operation/customer context.
- **Why it exists**: Centralized tool calling so both remember and recall go through the same connection and error-handling path.

#### `_get_connection()`
- **Why it exists**: Lazily creates and caches the `_PersistentMcpSession` on first use. Subsequent calls return the cached session.

#### `close()`
- **Why it exists**: Closes the MCP session and marks the adapter as closed. Prevents further tool calls.

#### `_text_from_result(result)`
- **Why it exists**: Extracts `TextContent` from the MCP tool result and joins multiple text contents with newlines.

#### `_fragments_from_result(result)`
- **Why it exists**: Extracts fragments from the MCP recall result and drops "system markers" (strings starting with "memory is still", "cognee", or "knowledge graph"). These are Cognee internal markers, not customer memory.

#### `_is_system_marker(text)`
- **Why it exists**: Checks if a recall fragment is a Cognee system marker rather than customer memory. This prevents system markers from being silently counted as retrieved memory.

## Architecture and Design Reasoning
The MCP adapter encapsulates all knowledge of the MCP Streamable HTTP protocol. Key design decisions:

1. **Persistent session**: The `_PersistentMcpSession` runs on a separate thread with its own event loop. This avoids the overhead of re-establishing the HTTP connection on every operation, which would make the MCP adapter appear artificially fast in benchmarks. The session is created lazily and cached.

2. **Protocol isolation**: All MCP-specific logic (tool names, argument format, session management, marker filtering) is contained in this file. The CLI adapter contains the analogous logic for `cognee-cli` commands.

3. **Configuration via environment**: The adapter reads `COGNEE_MCP_URL`, `COGNEE_RECALL_QUERY_TYPE` from environment variables. The `MEMORY_MODE` environment variable flows through the factory to select this adapter or the CLI one.

4. **Same backend**: The adapter connects to the Cognee MCP endpoint (which forwards to the central Cognee API). The `mcp_url` defaults to `http://127.0.0.1:8001/mcp`, but in this POC it connects to the same Cognee API backend as the CLI adapter (via the MCP server running on the same or different port). Data isolation between CLI and MCP is achieved through the `DATA_ROOT_DIRECTORY` and `SYSTEM_ROOT_DIRECTORY` environment variables configured at the API level.

5. **Latency measurement**: Like the CLI adapter, the base class wrapper measures wall-clock time around the `_remember`/`_recall` call. The adapter captures execution time at its boundary (MCP tool call). The MCP protocol is significantly more efficient than shelling out to `cognee-cli`, as evidenced by the much lower latencies (recall: ~68ms vs ~3579ms for CLI).

6. **Error handling**: The `_call()` method wraps exceptions with `integration="mcp"` context, so benchmark failure reporting can distinguish between CLI and MCP errors without parsing the underlying protocol error format.