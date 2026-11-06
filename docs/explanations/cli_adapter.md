# CLI Adapter (cli_adapter.py)

## File Purpose
This adapter represents the "direct/CLI integration" path. It stores and retrieves memory by invoking `cognee-cli` commands against the central Cognee API. This adapter and the MCP adapter both read and write the same Cognee backend — the difference is the invocation mechanism (CLI commands vs. MCP protocol). Everything CLI-specific lives here: the two commands, their argument order, and the parsing of the CLI's human-oriented stdout. The rest of the application sees only `MemoryAdapter`.

## Classes and Data Structures

### `CliMemoryAdapter(MemoryAdapter)`
- **integration = "cli"**: Class variable identifying this adapter to the framework, benchmark, and error reporting.
- **Constructor parameters**:
  - `api_url` (optional, from `COGNEE_API_URL` env var or default `http://127.0.0.1:8000`): The URL of the Cognee API server that `cognee-cli` will connect to.
  - `cli_path` (optional, from `COGNEE_CLI_PATH` env var or auto-discovered): Path to the `cognee-cli` executable.
  - `recall_query_type` (optional, from `COGNEE_RECALL_QUERY_TYPE` env var or default `"CHUNKS"`): Retrieval mode used for recall. Pinned to `"CHUNKS"` so both integrations issue the same retrieval and measured work is comparable.
  - `top_k` (optional, from env or default `10`): Number of top results to return.
  - `timeout_seconds` (optional, from env or default `180.0`): Subprocess timeout for CLI commands.

### Key internal methods

#### `_remember(customer_id, facts, dataset)`
- **Inputs**: Customer ID, facts tuple, dataset name.
- **Behavior**: Constructs `cognee-cli remember` command with facts and dataset name, runs it via subprocess, and returns the `stdout` character count as acknowledgement length.
- **Why it exists**: This is the adapter's core responsibility — getting facts into the Cognee backend via the CLI. The command format is:
  ```
  cognee-cli --api-url <url> remember <fact1> <fact2> ... --dataset-name <dataset>
  ```

#### `_recall(customer_id, query, dataset)`
- **Inputs**: Customer ID, query string, dataset name.
- **Behavior**: Constructs `cognee-cli recall` command with query, datasets, query type, top_k, and output format, runs it via subprocess, and extracts memory fragments from the JSON output.
- **Why it exists**: This is the adapter's core responsibility — retrieving memory from the Cognee backend via the CLI. The command format is:
  ```
  cognee-cli --api-url <url> recall <query> --datasets <dataset> --query-type CHUNKS --top-k 10 --output-format json
  ```
- **Fragment parsing** (`fragments_from_cli_output`): The CLI prints a human-readable preamble before the JSON array. The parser locates the JSON array by trying decoding from each `[` bracket, drops "system" entries (Cognee markers like "memory still warming up"), and returns remaining entries as fragments.

#### `_base_args()`
- **Returns**: `[cli_path, "--api-url", api_url]`
- **Why it exists**: Base arguments for all CLI commands. The `--api-url` delegates `cognee-cli` commands to the running Cognee API server over HTTP, so this path and the MCP path read and write the same backend.

#### `_run(args, operation, customer_id)`
- **Inputs**: Command arguments, operation name (remember/recall), customer ID.
- **Behavior**: Runs the subprocess with timeout, checks return code, and raises `MemoryOperationError` on failure with integration/operation/customer context.
- **Why it exists**: Centralized subprocess plumbing so both remember and recall use the same error handling, timeout, and working directory (`PROJECT_ROOT`).

#### `_failure_detail(completed)`
- **Why it exists**: Picks the most useful error line from CLI stdout/stderr on failure.

#### `fragments_from_cli_output(stdout)`
- **Why it exists**: Extracts memory text from `cognee-cli recall --output-format json` stdout. Handles the preamble before the JSON array and drops system markers.

#### `_entry_text(entry)`, `_extract_json_list(stdout)`
- **Why they exist**: JSON parsing utilities for extracting the ranked result list from the CLI's stdout output.

## Architecture and Design Reasoning
The CLI adapter encapsulates all knowledge of the `cognee-cli` command-line tool. The rest of the application (agent, benchmark, factory) only knows about `MemoryAdapter`. This is critical for the POC because:

1. **Fair comparison**: Both adapters present the same interface. The benchmark can measure `remember` and `recall` operations without knowing whether they're being routed through CLI commands or MCP protocol.

2. **Protocol isolation**: All `cognee-cli`-specific logic (argument order, stdout parsing, error format) is contained in this file. The MCP adapter contains the analogous logic for the MCP protocol.

3. **Configuration via environment**: The adapter reads `COGNEE_API_URL`, `COGNEE_CLI_PATH`, `COGNEE_RECALL_QUERY_TYPE` from environment variables, making it easy to switch configurations without code changes. The `MEMORY_MODE` environment variable flows through the factory to select this adapter or the MCP one.

4. **Latency measurement**: The base class wrapper (`MemoryAdapter.remember/recall`) measures wall-clock time around the `_remember`/`_recall` call. The adapter captures execution time at its boundary (subprocess start/end), so the logged latency includes CLI startup overhead, API communication, and graph build time.

5. **Data isolation**: The `DATA_ROOT_DIRECTORY` and `SYSTEM_ROOT_DIRECTORY` environment variables control where the Cognee API stores data. The CLI adapter connects to the API on the configured `api_url`, and the API respects the data directory settings to isolate data between CLI and MCP environments.