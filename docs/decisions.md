# Engineering decisions — Retail Memory POC

Decisions that shape the CLI-vs-MCP comparison or change the implementation
contract. The evaluation plan (`docs/evaluation_plan.md`) defines *what* is
measured; this file records *why* the implementation is shaped the way it is.

---

## D1 — Pin recall to `CHUNKS` instead of the server's auto-router (Phase 4)

**Context.** `cognee-cli recall` auto-routes an unpinned query by rule-based
classifier, falling back to `HYBRID_COMPLETION`. The environment has no LLM
configured, so completion types return a rendered prompt template rather than an
answer — and the routing decision could differ per query in ways unrelated to the
integration being tested.

**Decision.** Both integrations pin `--query-type CHUNKS` (overridable via
`COGNEE_RECALL_QUERY_TYPE`, default in `app/memory/cli_adapter.py`). `CHUNKS` is
deterministic, non-generative, returns stored memory verbatim, and is what
`relevance_score` needs.

**Fairness.** The retrieval work is then identical on both paths and independent
of LLM availability. Phase 5 must pin the same mode and the same `top_k` (10);
differing values would compare retrieval settings, not integrations.

---

## D2 — Invoke the CLI from the repository root (Phase 4)

**Context.** `cognee-cli` loads `.env` from the process working directory, and
`ENABLE_BACKEND_ACCESS_CONTROL` changes backend behaviour (and forces auth) when
absent.

**Decision.** The adapter passes `cwd=PROJECT_ROOT` to the subprocess instead of
inheriting the caller's directory.

**Fairness.** Neither path is advantaged; this only removes an accidental
dependence on where the benchmark happened to be started from.

---

## D3 — One remember call carries the whole fact list (Phase 4)

**Context.** `remember` accepts multiple positional text arguments and ingests
them into one dataset as a single document (`text_0.txt`).

**Decision.** `_remember` passes all facts to one `remember` invocation. Re-adding
identical facts is a no-op success; adding *different* facts to an existing
dataset returns `409 DocumentUpdateRequiredError`, which is surfaced as a failure
rather than worked around.

**Fairness.** The MCP tool takes the same shape (`data` + `dataset_name`), so both
paths submit one payload of the same size. The benchmark must reset the dataset
between seed runs, or re-submit the same facts, and must do so identically for
both integrations.

---

## D4 — Known limitation: dataset scoping does not isolate memory (Phase 4)

**Context.** `docs/evaluation_plan.md` §3.2 states that both adapters derive the
scope `retail_<customer_id>`, so each customer's memory is separate. That is not
what the backend does here: with `ENABLE_BACKEND_ACCESS_CONTROL=false`, every
dataset shares one vector index, and `--datasets` only labels results with the
requested dataset name. Confirmed via the CLI and via direct
`POST /api/v1/recall` with both `datasets` and `dataset_ids`; the smoke recall
returned facts stored in other datasets.

**Status.** Unresolved by design, pending a decision before Phase 9. Options:

1. **Accept and document** (current behaviour). Both integrations hit the same
   backend, so the comparison stays fair; the cost is that `relevance_score` can
   be inflated by other customers' facts, and "irrelevant memory" is harder to
   isolate.
2. **Enable backend access control.** Restores real per-dataset isolation but
   forces authentication and user management, which the project scope explicitly
   excludes.

**Not done:** client-side filtering of fragments by customer, or rewriting fact
text with a customer marker. Both would work around the backend on one layer while
the MCP path would have to copy it, adding processing that distorts the very
overhead being measured.

---

## D5 — `system` marker entries are not memory (Phase 4)

**Context.** Recall responses can include entries with `source: "system"`
("memory still warming up", build failures) alongside real memory.

**Decision.** Such entries are logged and excluded from fragments, so a
marker-only response is reported as `result_count=0` rather than as a successful
retrieval of text that is not customer memory. Phase 5 must mirror this.

## D6 — MCP uses one persistent client session (corrected Phase 5)

**Context.** The first Phase 5 implementation used direct Cognee REST calls and
created a new connection for each operation. That did not represent an MCP
integration or a realistic long-lived agent.

**Decision.** `McpMemoryAdapter` connects to `COGNEE_MCP_URL` using the MCP
SDK's streamable-HTTP transport and `ClientSession`. A private event-loop thread
owns the async context stack, performs handshake and tool discovery once, and
serves synchronous adapter calls through `run_coroutine_threadsafe`. `close()`
closes the stack and stops the loop.

**Fairness.** The adapter sends one combined fact payload and uses the same
`CHUNKS`/`top_k=10` retrieval settings as the CLI path. No REST endpoint is
called by application code.

## D7 — Cold start is separate from warm MCP operations

**Decision.** Adapter initialization/handshake/tool discovery is a cold-start
cost. Subsequent MCP calls reuse the same session and are warm operations. The
CLI retains its natural subprocess-per-operation behavior. Benchmark summaries
must not average MCP cold-start time into warm operation latency; this is an
intentional integration trade-off rather than an artificial normalization.
