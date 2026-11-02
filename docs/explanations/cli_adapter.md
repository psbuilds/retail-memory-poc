# `app/memory/cli_adapter.py` — CLI integration with the Cognee API

## 1. File Purpose

This adapter implements the `MemoryAdapter` contract using the `cognee-cli`
binary in API-delegation mode. It does not reimplement any memory logic — that
lives in the central Cognee API backend — and it does not create a second engine.
Both this path and the forthcoming MCP path read and write the same backend.

**Why it exists.** The project must compare two integration mechanisms side by
side. The CLI path is the "shell out to CLI" approach; the MCP path will be
"call an HTTP endpoint". If each one wrote its own logic, the diff between them
would be unmanageably large and would include boilerplate that has nothing to do
with the integration being measured. By keeping one contract (the base class) and
one hook set (`_remember`, `_recall`), each integration contributes only the
lines that make its mechanism different.

## 2. Classes and Data Structures

### `CliMemoryAdapter(MemoryAdapter)` — integration-specific adapter

This class is the only component in this file. Its `integration = "cli"` is a
class variable that every result and error carries. It overrides only the two
protected hooks:

| Method | What it does |
|---|---|
| `_remember(customer_id, facts, dataset)` | Shells out to `cognee-cli --api-url <url> remember <facts> --dataset-name <dataset>` and returns the length of stdout |
| `_recall(customer_id, query, dataset)` | Shells out to `cognee-cli --api-url <url> recall <query> --datasets <dataset> --query-type <type> --top-k <k> --output-format json` and returns a tuple of raw text fragments |

Everything else (validation, dataset naming via `dataset_for`, timing via
`perf_counter`, error wrapping) is inherited from `MemoryAdapter`. This keeps the
adapter itself tiny: only process invocation, argument construction, stdout
parsing, and a couple of failure translations.

## 3. Functions and Methods

### Constructor

```python
def __init__(self, *, api_url=None, cli_path=None, recall_query_type=None, top_k=None, timeout_seconds=None)
```

- `api_url` — falls back to `COGNEE_API_URL` env var → `http://127.0.0.1:8000`.
- `cli_path` — falls back to `shutil.which("cognee-cli")` or a binary next to the
  Python interpreter.
- `recall_query_type` — pinned to `"CHUNKS"` by default; can be overridden
  via `COGNEE_RECALL_QUERY_TYPE`.
- `top_k` — 10 by default.
- `timeout_seconds` — 180 by default.

If neither `cli_path` resolution succeeds, a `MemoryConfigurationError` is raised.
The constructor's role is *configuration*; no process is started, no network
request is made.

### `_remember` — write customer facts

Steps:

1. Build the CLI argument list:
   ```python
   args = [cli_path, "--api-url", api_url, "remember", *facts, "--dataset-name", dataset]
   ```
   The CLI expects `--api-url` first, then the subcommand, then positional data
   arguments, then `--dataset-name`.

2. Call `self._run(args, operation="remember", customer_id=customer_id)`.

3. The `_run` method (see below) executes the subprocess, raises on non-zero exit
   or on timeout, and returns the `CompletedProcess`.

4. `return len(completed.stdout)` — the hook returns the acknowledgement length
   in chars.

**Argument order matters.** The Cognee CLI's `remember` subcommand takes
positional text arguments and a `--dataset-name`. The `--api-url` global must
precede the subcommand. The dataset name is the scope (`retail_<id>`); without
it the API stores everything in the default `main_dataset`, which would make
every customer's memory collide.

### `_recall` — read customer memory

Steps:

1. Build the CLI argument list:
   ```python
   args = [
       cli_path, "--api-url", api_url,
       "recall", query,
       "--datasets", dataset,
       "--query-type", self.recall_query_type,
       "--top-k", str(self.top_k),
       "--output-format", "json",
   ]
   ```

2. Call `self._run(args, operation="recall", customer_id=customer_id)`.

3. `return fragments_from_cli_output(completed.stdout)` — the hook returns
   *raw* fragments; the base class will later strip empties and join them into
   `text`.

**Why `--query-type CHUNKS`?** The auto-router could pick different retrieval
types for different queries; that would make the benchmark compare retrieval
settings, not integrations. The CHUNKS retriever returns stored memory
verbatim, it does not call an LLM, and it supports deterministic relevance
scoring.

### `_run(args, *, operation, customer_id)` — subprocess glue

```python
def _run(self, args, *, operation, customer_id) -> subprocess.CompletedProcess[str]:
```

Steps:

1. Try `subprocess.run(args, capture_output=True, text=True, timeout=self.timeout_seconds,
   cwd=PROJECT_ROOT, check=False)`.

2. Catch `subprocess.TimeoutExpired` and raise a `MemoryOperationError` with
   `"timed out"` in the message; chain the `TimeoutExpired` as `__cause__`.

3. Catch `OSError` (e.g. binary not on PATH, permission denied) and raise a
   `MemoryOperationError` too, chaining the original error.

4. If `completed.returncode != 0`, raise a `MemoryOperationError` whose message
   includes the return code and the CLI's own last error line (`_failure_detail`).

5. Otherwise, return the `CompletedProcess` unchanged.

`PROJECT_ROOT` is the repository root (the adapter's `__file__` resolved to its
parent's parent). It is passed as `cwd` so that `cognee-cli` loads `.env` from
the correct place — `ENABLE_BACKEND_ACCESS_CONTROL=false` is read from there,
which determines whether the backend enforces per-dataset isolation. Starting the
process from a different directory would change that env var silently and
invalidate the comparison.

### `_failure_detail(completed) -> str`

Utility that scans the CLI's stdout and stderr for the most useful error line.
The CLI sometimes emits a human-friendly preamble before the JSON payload (or
before the exit message), and that preamble itself can contain `[` or other
bracket characters. The function looks at both streams and returns the last
non-empty stripped line. It is deliberately simple — no attempt to parse
structured error objects from the CLI's output.

### `fragments_from_cli_output(stdout) -> tuple[str, ...]`

This is the parsing logic that turns the CLI's JSON output into the `tuple[str, ...]`
the base class expects.

**Step 1 — locate the JSON array.** The CLI prints a human-readable preamble
before the JSON array, and that preamble can itself contain `[` characters
(echoing the dataset list). So the function iterates over every `[` in the
stdout string and attempts `json.JSONDecoder().raw_decode(stdout[idx:])`. The
first decode that produces a list is the payload. Naïve `stdout.index("[")`
would break on that preamble.

**Step 2 — extract each entry's text.** For each dict in the list:

- If `"source" == "system"` (Cognee marker like "memory still warming up"),
  log a warning and skip it.
- Otherwise, read the `"text"` field (or `"content"` as fallback).
- Strip it; if the result is non-empty, keep it.

**Step 3 — drop system markers.** The base contract says such entries are not
customer memory. Dropping them here means a marker-only response gives
`result_count == 0` rather than silently counting the wrong text.

**Step 4 — return a tuple.** `tuple()` makes the fragments immutable and
ordered; the order reflects the server's ranking.

### `_validate_customer_id`, `_validate_facts`, `_validate_query`

These three validation helpers are reused by both adapters through the base
class. They exist in `base.py`, not here, so the CLI adapter's code stays
focused on integration plumbing. The CLI adapter never calls them directly —
`MemoryAdapter.remember` and `MemoryAdapter.recall` do — but they are part of
the adapter's interface contract: an empty fact list or a blank query would
otherwise produce confusing backend results.

### `_base_args(self) -> list[str]`

Returns `[self.cli_path, "--api-url", self.api_url]`. Used by both `_remember`
and `_recall` to avoid duplicating the first two CLI arguments.

## 4. Execution Flow

### Remember flow

```
agent → adapter.remember(customer_id, facts)
  → base.validate_customer_id / validate_facts       (ValueError on bad input)
  → dataset_for(customer_id) → "retail_customer_id"
  → CliMemoryAdapter._remember(...)
      → _base_args() → [cli, --api-url, url]
      → args = [cli, --api-url, url, "remember", fact1, fact2, "--dataset-name", "retail_customer_id"]
      → _run(args, operation="remember", customer_id)
          → subprocess.run(..., capture_output=True, text=True, cwd=PROJECT_ROOT)
          → if returncode != 0 → MemoryOperationError from _failure_detail
          → if timeout → MemoryOperationError chaining TimeoutExpired
          → return CompletedProcess
      → return len(completed.stdout)
  → base.construct RememberResult(integration="cli", fact_count=len(cleaned), ...)
```

### Recall flow

```
agent → adapter.recall(customer_id, query)
  → base.validate_customer_id / validate_query
  → dataset_for(customer_id) → "retail_customer_id"
  → CliMemoryAdapter._recall(...)
      → _base_args()
      → args = [cli, --api-url, url, "recall", query, "--datasets", "retail_customer_id",
                "--query-type", "CHUNKS", "--top-k", "10", "--output-format", "json"]
      → _run(args, operation="recall", customer_id)
          → subprocess.run(...)
          → if nonzero exit → MemoryOperationError with last error line
          → if timeout → MemoryOperationError chaining TimeoutExpired
          → return CompletedProcess
      → fragments_from_cli_output(stdout)
          → locate JSON array at each "["
          → drop entries with source == "system"
          → extract each entry's text, strip, keep non-empty
          → tuple(...)
  → base.construct RecallResult(integration="cli", query=query, fragments=..., ...)
```

### Failure flow (real example against dead port)

```
cli.remember("customer_001", ["x"])
  → _run(..., operation="remember", customer_id)
      → subprocess.run(..., timeout=180)
          → connection refused → OSError("...
          → raise MemoryOperationError(...) from OSError
      → error.integration == "cli"
      → error.operation == "remember"
      → error.customer_id == "customer_001"
      → error.__cause__ is OSError (full traceback preserved)
```

## 5. Architecture and Design Reasoning

**Why subprocess.run rather than os.system or os.exec.** `subprocess.run` with
`capture_output=True` returns a `CompletedProcess` that carries `stdout`, `stderr`,
and `returncode` — exactly the information needed for the adapter to decide what
happened. It also supports `timeout`, which `os.system` does not. The CLI binary
is invoked as a subprocess because the integration's whole point is that the
application shells out to CLI; anything that runs inside the Python process would
be the MCP path's concern, not this one's.

**Why `--api-url` rather than starting a local Cognee process.** The benchmark
goal is to compare *two ways of reaching the same backend*. Starting a local
process would create a second engine (the MCP path talks to a server, not a local
file), breaking the "identical backend" requirement. The `--api-url` flag tells
the CLI to forward every command over HTTP to a *single* running API server; both
paths talk to the same process, and the comparison is about the transport
difference (subprocess vs HTTP tool), not about different engines.

**Why `cwd=PROJECT_ROOT`.** The CLI loads `.env` from the current directory.
If the benchmark were started from a different directory, `ENABLE_BACKEND_ACCESS_CONTROL`
could be different, and the two paths could silently drift into different modes
(access control on vs off). Passed as the subprocess's `cwd`, it guarantees the
same env regardless of where the experiment runner lives. It also means the CLI
process sees the same `COGNEE_API_URL` that the adapter was given, so there is
no hidden configuration.

**Why `--query-type CHUNKS` is pinned.** Without pinning, the CLI's auto-router
picks a search type per query. In an environment without an LLM configured,
completion types (`HYBRID_COMPLETION`, `GRAPH_COMPLETION`) return a prompt
template rather than stored facts. That makes `relevance_score` (which needs to
match terms against recalled text) unreliable and non-deterministic across
queries. Pinned `CHUNKS` returns each fact as a plain string, which is what
both `relevance_score` and `recommendation_match` need. Both integrations must
use the same pinned type; Phase 5 will confirm the MCP adapter does too.

**Why `fragments_from_cli_output` does not just do `json.loads`.** The CLI's
stdout begins with a preamble that echoes the dataset list, e.g.
`Recalling: 'x' (type: CHUNKS) in datasets ['retail_customer_001']`. That
preamble also contains `[` characters. A naive `json.loads` on the whole string
fails. The function instead walks every `[` and attempts a raw decode; the first
successful decode that yields a list is the payload. It then drops system
markers and extracts texts. This same parsing is the only place in the CLI
adapter that knows the CLI's stdout format — the rest of the system (base class,
benchmark, agent) works only with the `RecallResult` object and knows nothing
about JSON or subprocesses.

**Why `system` markers are excluded in the adapter, not downstream.** If the
base class excluded them downstream, the adapter could not tell the benchmark
whether a recall found *nothing useful* or *found only markers*. The base class
might want to report a zero-fragment result as `result_count == 0`, but only if
the adapter already stripped markers. Making the adapter responsible for it
ensures that both paths apply the same semantic filter (system markers are not
customer memory), keeping the CSV's `result_count` column comparable.

**Fairness summary — CLI adapter choices**

| Decision | Effect on the comparison |
|---|---|
| `subprocess.run` with `cwd=PROJECT_ROOT` | Same env, same backend; only transport (CLI process spawn) differs |
| `--api-url` to a single shared API | Both paths read/write the exact same memory store |
| `--query-type CHUNKS` pinned | Retrieval is identical on both sides; relevance scoring works deterministically |
| `cwd=PROJECT_ROOT` prevents hidden env drift | Neither path accidentally gets a different `ENABLE_BACKEND_ACCESS_CONTROL` setting |
| Fragments stay separate (tuple, no merge in base) | `result_count` and `relevance_score` meaning is identical across integrations |
| `fragments_from_cli_output` parses stdout rigorously | The base class never has to guess CLI output shape; the diff stays in the adapter |
| Error wrapping with `__cause__` preserved | Same error type (`MemoryOperationError`) and same visible attributes; original backend error is always accessible |