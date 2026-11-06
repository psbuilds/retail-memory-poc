# Documentation Index - Cognee CLI vs MCP Retail Memory POC

This directory contains comprehensive technical documentation for the retail memory proof-of-concept (POC) comparing Cognee CLI integration versus Cognee MCP integration.

## Reading Order

Start with `00_project_overview.md` for the big picture, then proceed through the sequence. Each document builds on the previous ones.

```
00 - Project Overview and Architecture
01 - Common Memory Interface
02 - CLI Adapter
03 - MCP Adapter
04 - Adapter Factory and Configuration
05 - Cognee Memory Engine and Data Flow
06 - Retail Data and Synthetic Data Generation
07 - Retail Chatbot and Recommendation Logic
08 - Benchmark Methodology and Metrics
09 - Benchmark Results and Analysis
10 - Testing and Validation
11 - Running the Entire POC and Reproducing Results
12 - Architectural Decisions and Trade-offs
13 - Limitations and Future Scope
14 - Glossary
```

## Document Summary

| Doc | What it teaches |
|-----|-----------------|
| `00_project_overview.md` | Complete POC architecture, system design, how CLI vs MCP differ, information flow, benchmark evaluation |
| `01_memory_interface.md` | The common `MemoryAdapter` abstraction, validation, timing, result normalization, factory usage |
| `02_cli_adapter.md` | How `cognee-cli` subprocess calls work, command construction, output parsing, error handling |
| `03_mcp_adapter.md` | MCP Streamable HTTP protocol, persistent session management, tool invocation, marker filtering |
| `04_adapter_factory_and_configuration.md` | `MEMORY_MODE` env var, API URL configuration, data directory isolation |
| `05_cognee_memory_engine.md` | Cognee's role, what the project adds vs Cognee core, `CHUNKS` query type, dataset scoping |
| `06_retail_data_and_scenarios.md` | Product catalogue, 5 customers with 4 facts each, 10 retail scenarios, synthetic data rationale |
| `07_chatbot_and_recommendation_flow.md` | Chatbot execution flow, preference extraction, recommendation logic, end-to-end interaction |
| `08_benchmark_methodology.md` | How the benchmark measures latency, success rates, fragment counts, timing boundaries |
| `09_results_analysis.md` | Actual benchmark results: CLI ~6789ms remember vs MCP ~1445ms, recall ~3579ms vs ~68ms |
| `10_testing_and_validation.md` | Test suite structure, what each test proves |
| `11_execution_and_reproduction.md` | Step-by-step guide to reproduce the benchmark from scratch |
| `12_architectural_decisions.md` | Key design decisions: CHUNKS pinning, subprocess from root, persistent MCP session, dataset scoping |
| `13_limitations_and_future_scope.md` | Current limitations and proposed future improvements |
| `14_glossary.md` | Key term definitions in context of this POC |

## Quick-Reference Benchmark Commands

```bash
# From project root, with Cognee API running on port 8000:
# CLI benchmark (integration-level writes)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/cli/data SYSTEM_ROOT_DIRECTORY=results/cli/system MEMORY_MODE=cli .venv/bin/python -m scripts.benchmark --adapter cli --group writes --repetitions 3

# CLI benchmark (recall)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/cli/data SYSTEM_ROOT_DIRECTORY=results/cli/system MEMORY_MODE=cli .venv/bin/python -m scripts.benchmark --adapter cli --group recall --repetitions 3

# CLI benchmark (E2E)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/cli/data SYSTEM_ROOT_DIRECTORY=results/cli/system MEMORY_MODE=cli .venv/bin/python -m scripts.benchmark --adapter cli --group e2e --repetitions 1

# MCP benchmark (integration-level writes)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/mcp/data SYSTEM_ROOT_DIRECTORY=results/mcp/system MEMORY_MODE=mcp .venv/bin/python -m scripts.benchmark --adapter mcp --group writes --repetitions 3

# MCP benchmark (recall)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/mcp/data SYSTEM_ROOT_DIRECTORY=results/mcp/system MEMORY_MODE=mcp .venv/bin/python -m scripts.benchmark --adapter mcp --group recall --repetitions 3

# MCP benchmark (E2E)
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/mcp/data SYSTEM_ROOT_DIRECTORY=results/mcp/system MEMORY_MODE=mcp .venv/bin/python -m scripts.benchmark --adapter mcp --group e2e --repetitions 1

# Run all tests
COGNEE_API_URL=http://127.0.0.1:8000 DATA_ROOT_DIRECTORY=results/cli/data SYSTEM_ROOT_DIRECTORY=results/cli/system MEMORY_MODE=cli .venv/bin/python -m pytest tests/ -v
```

## Verification

After reading all documents in sequence, you should be able to:

1. Explain the complete POC architecture and every component's responsibility
2. Trace how a user query flows from the chatbot through memory retrieval to product recommendations
3. Explain why MCP is ~50x faster than CLI for memory recall operations
4. Run the benchmark suite from a fresh terminal using the commands above
5. Understand the fair comparison design choices (same backend, same data, same queries)
6. Identify the current limitations and proposed future improvements
7. Fix or extend the POC with confidence about how changes affect the CLI vs MCP comparison