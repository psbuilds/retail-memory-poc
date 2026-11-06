# Project Progress

## Phases Completed

- **PHASE 0**: Verified Cognee infrastructure - Cognee API on port 8000 confirmed running and healthy (v1.6.6)
- **PHASE 1**: Defined evaluation criteria and benchmark requirements
- **PHASE 2**: Generated deterministic synthetic retail data (5 customers, 20 products, 10 scenarios)
- **PHASE 3**: Built common memory interface in `app/memory/base.py`
- **PHASE 4**: Implemented CLI Cognee adapter (`app/memory/cli_adapter.py`)
- **PHASE 5**: Implemented MCP Cognee adapter (`app/memory/mcp_adapter.py`)
- **PHASE 6**: Tested both adapters independently - both verified working
- **PHASE 7**: Built product catalog and recommendation logic (`app/products.py`)
- **PHASE 8**: Built retail AI agent/chatbot (`app/chatbot.py`)
- **PHASE 9**: Ran two-level evaluation (integration-level and end-to-end)
- **PHASE 10**: Analysing results and documenting trade-offs

## Files Created or Modified

### Source Files
- `app/memory/factory.py` - New: Factory for creating memory adapters based on `MEMORY_MODE` env var
- `app/memory/base.py` - Existing: Common memory interface
- `app/memory/cli_adapter.py` - Existing: CLI integration adapter
- `app/memory/mcp_adapter.py` - Existing: MCP integration adapter
- `app/products.py` - Existing: Product catalog and recommendation logic
- `app/chatbot.py` - Existing: Retail AI chatbot

### Data Files
- `data/products.json` - Generated: 20 products with required fields
- `data/customers.json` - Generated: 5 customers with 4 facts each
- `data/scenarios.json` - Generated: 10 retail scenarios

### Documentation Explanations
- `docs/explanations/base.md` - New: Common memory interface explanation
- `docs/explanations/cli_adapter.md` - New: CLI adapter explanation
- `docs/explanations/mcp_adapter.md` - New: MCP adapter explanation

### Benchmark Results
- CLI integration: remember ~6789ms, recall ~3579ms
- MCP integration: remember ~1445ms, recall ~68ms
- CLI E2E: ~3582ms (recall-dominated)
- MCP E2E: ~71ms (recall-dominated)
- Data isolation: CLI uses `results/cli/data/system`, MCP uses `results/mcp/data/system`
- Same Cognee API port 8000 used by both methods

## Adapter Switching
The `MEMORY_MODE` environment variable (`cli` or `mcp`) controls which adapter the factory returns. The rest of the application (agent, benchmark) depends only on the `MemoryAdapter` interface and requires no code changes when switching modes.

## Fair Comparison Notes
- Both adapters use the same Cognee API (port 8000)
- Same synthetic data (5 customers, 20 products, 10 scenarios)
- Same recommendation logic
- Same scenarios and queries
- Data isolation via `DATA_ROOT_DIRECTORY` and `SYSTEM_ROOT_DIRECTORY` env vars
- Query type pinned to `CHUNKS` for both adapters