# `app/memory/base.py` — the shared memory contract

## 1. File Purpose

This file defines *what a memory system is* for this project: two operations,
`remember` and `recall`, with fixed inputs, fixed result objects, and fixed error
types. It contains no Cognee code at all.

**Problem it solves.** The project compares two ways of reaching the same memory
backend — shelling out to `cognee-cli` versus calling an MCP tool. If the agent
called either one directly, every experiment would need its own agent, and any
difference in results could come from the agent code rather than from the
integration. Instead, both integrations are plugins behind this one contract, so
the agent and the benchmark file are written once and never mention "cli", "mcp",
"subprocess", or "protocol".

**Where it sits.** Everything above it (agent, benchmark) imports only this
module. `app/memory/cli_adapter.py` subclasses it; the future
`app/memory/mcp_adapter.py` will subclass it too; `app/memory/factory.py` will pick
one based on `MEMORY_MODE`.

**Who interacts with it**

| Component | Uses |
|---|---|
| `app/memory/cli_adapter.py` | Subclasses `MemoryAdapter`, raises `MemoryOperationError` / `MemoryConfigurationError` |
| future `mcp_adapter.py` | Same |
| future `factory.py` | Instantiates an adapter, calls `dataset_for` implicitly via the public methods |
| future `chatbot.py` / benchmark | Calls `remember` / `recall`, reads `RecallResult.text` and the latency fields |

The module docstring states the three things it deliberately owns — dataset
naming, timing, result normalisation — because those are exactly the things that
would silently drift between the two integrations.

## 2. Classes and Data Structures

### `MemoryAdapterError(RuntimeError)`

The root of every adapter failure. It exists so the benchmark can tell "the
memory layer failed" apart from "the recommendation code failed" by catching one
type, and so a failure carries enough context to be filed in the CSV without
parsing log text.

```python
def __init__(self, message, *, integration="unknown", operation="unknown", customer_id=None)
```

- `integration` — `"cli"` or `"mcp"`; becomes a column in the results.
- `operation` — `"remember"`, `"recall"`, or `"configure"`.
- `customer_id` — which customer's memory was involved (not known at
  configuration time, hence the default `None`).

`__str__` appends those attributes to the message, so a single string already
reads like `... [integration=cli operation=recall customer_id=customer_001]`. This
is the concrete evidence behind the "error visibility" row in the developer
comparison.

**Why subclass `RuntimeError`.** It is not a programming mistake like
`ValueError`; it is a runtime condition (server down, wrong URL, timeout). Code
that catches `Exception` broadly still sees it, and `pytest.raises` works as
expected.

### `MemoryConfigurationError` / `MemoryOperationError`

Two subclasses, deliberately few:

- `MemoryConfigurationError` — the adapter cannot even be built (for example no
  `cognee-cli` binary found). Raised from `__init__`.
- `MemoryOperationError` — a call was attempted and failed (exit code 1,
  timeout, unparseable output).

Splitting them lets the benchmark fail fast at setup instead of recording twenty
identical "not configured" rows as if they were latency data.

### `RememberResult` (frozen dataclass, `slots=True`)

Fields: `integration`, `customer_id`, `dataset`, `fact_count`, `response_chars`,
`latency_ms`.

It is the answer to "what happened when we stored memory", in a shape that maps
one-to-one onto the Level 1 CSV columns. `frozen=True` means the benchmark cannot
accidentally mutate a measurement after the fact; `slots=True` keeps the objects
tiny, which matters when a run produces hundreds of them.

### `RecallResult` (frozen dataclass)

Fields: `integration`, `customer_id`, `dataset`, `query`, `fragments`
(`tuple[str, ...]`), `latency_ms`, plus four properties:

| Property | Returns | Why |
|---|---|---|
| `text` | `"\n".join(fragments)` | The context the agent actually consumes |
| `result_count` | `len(fragments)` | Detects "success but nothing retrieved" |
| `response_chars` | `len(self.text)` | Payload size / token-cost proxy |

`fragments` is a tuple, not a list: results are immutable, order is meaningful
(server ranking), and `tuple` makes accidental in-place sorting impossible.

The important design choice is that **fragments stay separate**. If the adapter
only returned one big string, a recall that found nothing would be
indistinguishable from one that found text; and relevance scoring could not tell
which passage matched.

### `MemoryAdapter(ABC)` — the contract itself

`integration: ClassVar[str]` is a class-level label; subclasses set it once
(`"cli"`), and the base class stamps it onto every result and error.

Two public methods (`remember`, `recall`) and two protected abstract hooks
(`_remember`, `_recall`). That split is the whole architecture: public methods are
the *template*, hooks are the *plug-in point*. This is the Template Method
pattern.

## 3. Functions and Methods

### `dataset_for(customer_id) -> str`

Returns `f"retail_{customer_id}"`, e.g. `customer_001 → retail_customer_001`.

One line, but it is the single most fairness-relevant line in the file. If the
CLI adapter built `retail_` + id and the MCP adapter built `customer_` + id, the
two paths would read different memory while both appearing to work — the
benchmark would then be comparing two different datasets, not two integrations.
Both adapters are required to call this function rather than format the name
themselves.

### `_validate_customer_id(customer_id) -> str`

1. Rejects non-strings and blank strings.
2. Strips surrounding whitespace.
3. Rejects anything outside `[A-Za-z0-9_-]`.

Raises `ValueError`, not `MemoryAdapterError`, because a bad argument is a caller
bug, not a backend failure. The character restriction matters beyond tidiness:
the id becomes part of a dataset name in a shared backend, so an id containing a
quote or a slash could silently address a different dataset. Keeping ids to a
safe alphabet makes that impossible.

### `_validate_facts(facts) -> tuple[str, ...]`

1. Rejects a bare `str` — `remember(cid, "one fact")` would otherwise iterate
   characters and store garbage.
2. Materialises to a tuple once (so generators work).
3. Strips each fact and drops empties.
4. If nothing remains, raises `ValueError`.
5. If entries were dropped (`len(cleaned) != len(materialised)`), raises
   `ValueError` rather than silently storing fewer facts than the caller passed.

Point 5 is deliberate: a silently dropped fact would change what the benchmark
seeded, and the two integrations could then be measured against different memory.

### `_validate_query(query) -> str`

Non-empty string, stripped. Same reasoning as above — an empty query would still
"work" against the backend and return arbitrary results, which would look like a
relevance failure instead of a harness bug.

### `MemoryAdapter.remember(customer_id, facts) -> RememberResult`

Step by step:

1. Validate the id and facts, deriving `dataset`.
2. `started = perf_counter()` — the clock starts *after* validation so argument
   checking is not counted as integration latency.
3. Call `self._remember(customer_id, cleaned, dataset)` inside `try`.
4. Any exception not already a `MemoryAdapterError` is logged and re-raised as
   `MemoryOperationError(... ) from exc`.
5. `latency_ms = (perf_counter() - started) * 1000`, stopping immediately after
   the hook returns, before building the result object.
6. `logger.debug(...)` with integration, customer, fact count, latency.
7. Return the `RememberResult`, with `response_chars` clamped to `>= 0`.

Why the hook returns only an `int`: the acknowledgement size is all the base
class needs from a write. The adapter decides what that ack is (for the CLI, the
length of the CLI's stdout). `fact_count` is known already — it is
`len(cleaned)` — so the adapter does not report it.

### `MemoryAdapter.recall(customer_id, query) -> RecallResult`

Identical shape:

1. Validate id and query, derive dataset.
2. Start the clock.
3. `fragments = self._recall(customer_id, query, dataset)`.
4. Wrap unexpected exceptions the same way.
5. Stop the clock.
6. Strip empties out of the fragments — `tuple(f.strip() for f in fragments if f and f.strip())`.
7. Log, then build `RecallResult`.

Step 6 is the "result normalisation" the module docstring promises: whichever
integration produced the fragments, blank entries are removed here, once, so
`result_count` means the same thing on both sides.

### `_remember(...)` / `_recall(...)` — the abstract hooks

```python
@abstractmethod
def _remember(self, customer_id, facts, dataset) -> int
@abstractmethod
def _recall(self, customer_id, query, dataset) -> tuple[str, ...]
```

These are the only things an integration must supply. `dataset` is passed in
rather than derived, so an adapter cannot invent its own scope. Because they are
`@abstractmethod`, an incomplete adapter raises `TypeError` at construction
instead of failing mysteriously mid-benchmark — `tests/test_memory.py` pins this.

### `close()` / `__enter__` / `__exit__`

`close()` is a no-op by default. The CLI adapter needs nothing (each call is its
own process); an MCP adapter keeps a client session open and will override it.
Providing the context-manager protocol in the base class means the benchmark can
always write `with adapter:` regardless of mode.

### `_log_failure(operation, customer_id, exc)`

One `logger.warning` line with the integration, operation, customer,
`type(exc).__name__`, and message. It records *what* failed before the exception
is translated into a `MemoryOperationError` — the raw type name (e.g.
`JSONDecodeError`, `TimeoutExpired`) is exactly what makes the debugging
comparison between the two paths meaningful later.

## 4. Execution Flow

A benchmark call looks like this:

```
Benchmark / Agent
  → MemoryAdapter.recall("customer_001", "recommend headphones")
      → _validate_customer_id + _validate_query      (ValueError on bad input)
      → dataset_for("customer_001") → "retail_customer_001"
      → perf_counter() starts
      → CliMemoryAdapter._recall(...)                (integration code)
          → subprocess → cognee-cli → Cognee API
          → parse stdout → tuple of fragments
      → perf_counter() stops                         → latency_ms
      → strip/normalise fragments
      → RecallResult(integration="cli", result_count=2, ...)
  → benchmark writes latency_ms, result_count, response_chars to the raw CSV
```

Failure path:

```
_hook raises OSError
  → _log_failure(...)                                (warning, original type)
  → MemoryOperationError("cli recall failed: ...") from OSError
      → .integration/.operation/.customer_id set     (CSV error columns)
      → __cause__ keeps the OSError                  (full traceback preserved)
```

## 5. Architecture and Design Reasoning

**Why an abstract base class instead of two independent classes.** The comparison
only means something if the *only* difference is the integration. Any logic living
in both adapters — validating inputs, naming the dataset, starting the clock,
joining fragments — is a place where they can diverge by accident. Putting it in
the base class means the adapters contain nothing but Cognee calls, so the diff
between them is exactly the thing being studied.

**Why the measurement lives here.** Both adapters take their latency from the same
`perf_counter()` window in the same method. Neither can decide to start timing
earlier, exclude its own parsing, or forget the clock. That is what makes the CLI
vs MCP latency numbers apples-to-apples, including the unflattering parts (the CLI
pays for process startup; that cost is *inside* the window and is reported, not
hidden).

**Why validation raises `ValueError` and adapters raise `MemoryAdapterError`.**
Two different failure meanings: "you called me wrong" versus "the backend let us
down". The benchmark must treat them differently — a `ValueError` should stop the
run, while a `MemoryOperationError` is a legitimate measured failure that belongs
in the results as `success=false`.

**Why results are frozen dataclasses with derived properties.** Every metric in
the evaluation plan needs to be computed from the same object without re-querying
the backend: `result_count` for empty-success detection, `response_chars` for
payload cost, `text` for relevance scoring. Deriving them as properties means they
can never contradict the stored fragments.

**Fairness summary.**

| Decision | Effect on the comparison |
|---|---|
| `dataset_for` in the base | Both paths address the same memory scope, always |
| Timing in the base | Identical measurement boundary, subprocess/protocol cost included |
| Fragment normalisation in the base | `result_count` / `response_chars` mean the same on both sides |
| Adapters only implement two hooks | The code diff *is* the integration difference |
| Error taxonomy here | Failure rate and error visibility are comparable columns, not prose |
