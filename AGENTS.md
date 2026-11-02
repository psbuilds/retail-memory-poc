# AGENTS.md — Retail Memory Integration POC

## 1. Project Overview

This project is a small proof-of-concept that compares two ways of integrating the same AI memory system into a retail AI agent:

1. **Direct/CLI integration** with Cognee
2. **MCP integration** with Cognee

The central research question is:

> How do CLI/direct integration and MCP integration compare when connecting an AI application to the same memory system, both from a software developer's perspective and from the perspective of a real-world AI agent that accesses memory during user interactions?

This project evaluates the two integration approaches from two complementary perspectives.

### A. Developer and Engineering Perspective

Analyse the practical trade-offs involved in building, operating, debugging, and maintaining each integration, including:

- Implementation complexity
- Number of components and dependencies
- Configuration and infrastructure requirements
- Ease of debugging
- Error handling and error visibility
- Maintainability
- Extensibility

### B. Real-World AI Agent Perspective

Evaluate how each integration behaves when a retail AI agent actively accesses memory during realistic user interactions.

Analyse:

- Memory operation latency
- End-to-end agent response latency
- Reliability and failure rate
- Relevance of retrieved memory
- Whether retrieved memory improves personalized recommendations
- Additional overhead introduced by the integration layer

This is a focused engineering POC, not a production e-commerce platform.

Keep the implementation simple, reproducible, measurable, and easy to understand.

The goal is to isolate and understand the trade-offs introduced by the integration mechanism while keeping the underlying memory engine and application workflow as consistent as possible.

---

# 2. Core Architecture

The project uses one central Cognee memory backend.

```text
                         ┌─────────────────────┐
                         │    Cognee API       │
                         │  localhost:8000     │
                         │                     │
                         │ Central Memory      │
                         └──────────▲──────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
             HTTP/API delegation              HTTP/API delegation
                    │                               │
          ┌─────────┴─────────┐           ┌─────────┴─────────┐
          │   Cognee CLI      │           │ Cognee MCP Server  │
          │                   │           │ localhost:8001/mcp │
          └─────────▲─────────┘           └─────────▲─────────┘
                    │                               │
             CLI Adapter                      MCP Adapter
                    │                               │
                    └───────────────┬───────────────┘
                                    │
                              Retail AI Agent
```

The two integration paths must use the same:

- Cognee version where possible
- Central Cognee API/backend
- Memory data
- Customer queries
- Product catalog
- Recommendation logic
- Benchmark scenarios

The primary experimental variable is the **integration mechanism**, not the underlying memory engine or application logic.

---

# 3. Project Goals

The POC should demonstrate:

- Storing customer preferences as memory.
- Retrieving relevant customer memories.
- Using retrieved memory to improve retail product recommendations.
- Switching between CLI and MCP memory implementations without changing agent logic.
- Measuring and comparing both integration methods.

The POC must evaluate the integration at two levels.

### Level 1: Integration-Level Evaluation

Evaluate the direct interaction with the memory system:

- Remember operation latency
- Recall operation latency
- Success/failure rate
- Error behavior
- Integration overhead

### Level 2: End-to-End Agent Evaluation

Evaluate realistic retail interactions where an agent:

1. Receives a user query.
2. Identifies the customer.
3. Retrieves relevant customer memory.
4. Uses the retrieved preferences.
5. Filters or ranks products.
6. Generates a personalized recommendation.

Measure the complete workflow rather than only isolated memory calls.

The project should also document qualitative developer trade-offs, including:

- Implementation complexity
- Configuration complexity
- Dependencies and infrastructure
- Debugging experience
- Error visibility
- Maintainability
- Extensibility

The POC should remain small and focused.

Do NOT add:

- Authentication or user management.
- A frontend unless explicitly requested.
- External databases.
- Order-history systems.
- Product reviews.
- Web scraping.
- Image generation.
- Complex multi-agent orchestration.
- Production deployment infrastructure.

---

# 4. Current Project Structure

Maintain this structure unless there is a strong reason to change it:

```text
retail-memory-poc/
├── AGENTS.md
│
├── app/
│   ├── chatbot.py
│   ├── main.py
│   ├── products.py
│   │
│   └── memory/
│       ├── base.py
│       ├── cli_adapter.py
│       ├── mcp_adapter.py
│       └── factory.py
│
├── data/
│   ├── products.json
│   └── scenarios.json
│
├── results/
│   ├── comparison.csv
│   └── raw/
│
├── scripts/
│   ├── benchmark.py
│   ├── generate_data.py
│   └── seed_memory.py
│
├── tests/
│   ├── test_chatbot.py
│   └── test_memory.py
│
├── pyproject.toml
└── uv.lock
```

A small documentation directory may be added if needed:

```text
docs/
└── evaluation_plan.md
```

Do not mix benchmark scripts, application logic, and temporary debugging code.

Temporary smoke tests should not modify production POC behavior or benchmark data.

---

# 5. Development Order

Follow this order unless explicitly instructed otherwise:

```text
PHASE 0 → Verify Cognee infrastructure

PHASE 1 → Define evaluation criteria and benchmark requirements
          ├── Developer/engineering evaluation criteria
          ├── Integration-level metrics
          └── End-to-end agent metrics

PHASE 2 → Define and generate deterministic synthetic retail data
          ├── Products
          ├── Customers
          ├── Customer memory facts
          └── Agent interaction scenarios

PHASE 3 → Build the common memory interface

PHASE 4 → Implement the CLI Cognee adapter

PHASE 5 → Implement the MCP Cognee adapter

PHASE 6 → Test both adapters independently

PHASE 7 → Build product catalog and recommendation logic

PHASE 8 → Build the retail AI agent/chatbot

PHASE 9 → Run two-level evaluation
          ├── Integration-level benchmark
          └── End-to-end agent benchmark

PHASE 10 → Analyse results from both perspectives
           ├── Performance and reliability
           ├── Agent-level impact
           └── Developer/engineering trade-offs
```

Do not skip directly to building the chatbot before the memory adapters are independently tested.

---

# 6. Synthetic Data Requirements

Keep the initial dataset intentionally small.

## Products

Generate approximately 15–20 products.

Each product should contain fields similar to:

```json
{
  "id": "p001",
  "name": "Wireless Earbuds",
  "category": "headphones",
  "price": 2499,
  "features": [
    "wireless",
    "lightweight",
    "travel-friendly"
  ]
}
```

The product catalog should contain enough variation in price and features to allow customer preferences to influence recommendations.

## Customers

Generate approximately 5 synthetic customers.

Each customer should have approximately 3–5 memory facts.

Example:

```text
Customer ID: customer_001

- Prefers affordable wireless headphones.
- Has a budget below ₹3,000.
- Uses headphones mainly while travelling.
- Dislikes bulky over-ear designs.
```

Customer preferences should include a mix of:

- Budget preferences
- Product or feature preferences
- Usage context
- Negative preferences
- Multiple interacting preferences

The data should be deterministic and reproducible.

Do not randomly regenerate benchmark data on every benchmark run.

---

# 7. Benchmark Scenario Requirements

Create approximately 10 scenarios.

Scenarios should represent realistic interactions where memory can affect the agent's recommendation.

Every scenario must define:

- Scenario ID
- Customer ID
- User query
- Expected memory information
- Expected recommendation behavior

Example:

```json
{
  "id": "scenario_01",
  "customer_id": "customer_001",
  "query": "Can you recommend headphones for me?",
  "expected_memory": [
    "affordable",
    "wireless",
    "budget below 3000"
  ],
  "expected_behavior": "Recommend products matching the customer's budget and wireless preference."
}
```

Include different scenario types where appropriate:

- Budget-based recommendations
- Feature-based recommendations
- Negative preferences
- Multiple preferences
- Ambiguous queries where memory provides useful context

The same scenarios must be used for both CLI and MCP benchmark runs.

Never manually modify a query for one adapter only.

---

# 8. Memory Interface

The retail AI agent must depend on a common abstraction rather than directly depending on Cognee CLI or MCP.

Define a minimal interface in:

```text
app/memory/base.py
```

The interface should support the minimum operations needed for this POC:

```python
remember(customer_id, facts)
recall(customer_id, query)
```

Use clear return types and predictable error behavior.

The agent must never contain CLI commands or MCP protocol code.

All integration-specific logic belongs inside the corresponding adapter.

The common interface should make it possible to switch integration methods without changing the recommendation or agent workflow.

---

# 9. CLI Adapter Rules

The CLI adapter must:

- Use Cognee CLI as the integration mechanism.
- Delegate operations to the central Cognee API when configured.
- Avoid directly manipulating Cognee databases.
- Capture execution time at the adapter boundary.
- Return results in the common format expected by the memory interface.

Do not use the CLI adapter to access a separate memory backend.

The CLI implementation should remain focused on representing a realistic direct integration path for an application.

---

# 10. MCP Adapter Rules

The MCP adapter must:

- Connect to the configured Cognee MCP endpoint.
- Use MCP tools rather than direct Cognee CLI commands.
- Communicate with the central Cognee backend through the MCP server.
- Return results matching the common memory interface.
- Capture execution time at the adapter boundary.

Do not duplicate Cognee memory logic inside the MCP adapter.

The MCP adapter is an integration layer, not a new memory system.

The implementation should represent a realistic agent-oriented MCP integration.

---

# 11. Adapter Switching

The agent should be able to switch between memory implementations using configuration or a factory.

Example conceptual flow:

```text
MEMORY_MODE=cli
        ↓
CLI Adapter

MEMORY_MODE=mcp
        ↓
MCP Adapter
```

The rest of the application should not need modification when switching modes.

The recommendation logic and agent workflow must remain the same regardless of the selected adapter.

---

# 12. Evaluation and Benchmark Rules

The evaluation is a core part of the project.

The project must evaluate the two integration approaches at two levels.

## Level 1: Integration-Level Benchmark

This benchmark measures direct memory operations.

For every measured run:

1. Use the same customer memory.
2. Use the same query.
3. Run the same number of iterations.
4. Separate warm-up runs from measured runs.
5. Record raw results before calculating summaries.

Measure at least:

- Integration method (`cli` or `mcp`)
- Scenario ID
- Operation (`remember` or `recall`)
- Latency in milliseconds
- Success/failure
- Error message, if applicable
- Result count or response availability

Do not silently discard failed runs.

## Level 2: End-to-End Agent Benchmark

This benchmark measures a realistic agent workflow:

```text
User Query
    ↓
Identify Customer
    ↓
Retrieve Customer Memory
    ↓
Apply Retrieved Preferences
    ↓
Rank or Filter Products
    ↓
Generate Personalized Recommendation
    ↓
Return Response
```

For each scenario, measure:

- End-to-end response latency
- Memory retrieval success
- Relevance of retrieved memory
- Whether the recommendation matches customer preferences
- Overall scenario success/failure

Where possible, keep recommendation evaluation deterministic using the expected behavior defined in each scenario.

The same product catalog, customer memory, recommendation logic, customer ID, and user query must be used for both CLI and MCP runs.

Only the memory integration mechanism should change.

## Developer and Engineering Evaluation

In addition to runtime benchmarks, document qualitative engineering differences using consistent criteria:

| Criterion | CLI/Direct | MCP |
|---|---|---|
| Components required | | |
| Dependencies | | |
| Configuration complexity | | |
| Implementation complexity | | |
| Debugging experience | | |
| Error visibility | | |
| Maintainability | | |
| Extensibility | | |

Do not assign arbitrary scores.

Base observations on the actual implementation, setup, and testing experience.

---

# 13. Fair Comparison Rules

The benchmark should compare the integration mechanisms fairly.

Do not:

- Use different datasets.
- Use different customer memories.
- Use different queries.
- Use different Cognee backends.
- Use different recommendation logic.
- Include one-time setup costs in only one method.
- Hide failed operations.

Clearly distinguish:

- Cold-start/setup time
- Warm-up time
- Individual memory operation latency
- End-to-end agent response latency

If the two methods cannot be made fully identical due to protocol differences, document the difference instead of hiding it.

The goal is to understand the trade-offs of each integration mechanism, not to artificially make one approach appear better.

---

# 14. Error Handling and Logging

Keep error handling simple and visible.

For adapter operations:

- Raise meaningful exceptions.
- Preserve the original error where possible.
- Log the integration method and operation.
- Do not silently return fake successful results.

Benchmark failures must be recorded as failures.

For end-to-end scenarios, distinguish between:

- Memory retrieval failure
- Memory retrieval success but irrelevant result
- Recommendation logic failure
- Complete scenario success

This distinction is important when analysing real-world agent behavior.

---

# 15. Testing Requirements

Before integrating an adapter with the retail agent, test it independently.

Tests should cover:

## Memory Interface

- Store/remember operation.
- Recall operation.
- Invalid customer ID or dataset handling.
- Error propagation.
- Consistent return formats between adapters.

## Retail Agent

- Correct adapter selection.
- Memory retrieval before recommendations.
- Recommendation behavior using retrieved preferences.
- Recommendation behavior when memory is unavailable or irrelevant.

Avoid tests that depend unnecessarily on external services.

Use mocks for unit tests where appropriate and separate integration tests from unit tests.

---

# 16. Code Quality Rules

Prefer:

- Small functions.
- Clear names.
- Type hints.
- Minimal dependencies.
- Simple data structures.
- Explicit configuration.

Avoid:

- Unnecessary abstractions.
- Premature optimization.
- Large frameworks.
- Complex multi-agent orchestration.
- Hidden global state.

Before adding a dependency, ask whether Python's standard library or an existing dependency is sufficient.

The purpose of this POC is to compare integration approaches, so do not introduce unnecessary application complexity that could distort the comparison.

---

# 17. Working With This Repository

Before making changes:

1. Inspect the existing relevant files.
2. Explain briefly what will be changed.
3. Make the smallest change required.
4. Run relevant tests or validation.
5. Report what changed and how it was verified.

Do not rewrite unrelated files.

Do not change project architecture without explaining why.

When making implementation decisions that affect the CLI vs MCP comparison, explicitly explain how the decision preserves fairness between the two approaches.

---

# 18. Definition of Done

The POC is complete when:

## Infrastructure

- A single Cognee backend is running.
- CLI integration can store and retrieve memory.
- MCP integration can store and retrieve memory.

## Application

- Both adapters implement the same memory interface.
- The retail AI agent works with either adapter.
- Customer memory is retrieved and used during product recommendations.

## Fair Evaluation

- The same Cognee backend is used by both methods.
- The same synthetic customer data is used.
- The same product catalog is used.
- The same scenarios and queries are used.
- The same recommendation logic is used.

## Analysis

The project produces results at two levels:

### 1. Integration-Level Results

- Memory operation latency
- Reliability
- Error behavior
- Integration overhead

### 2. End-to-End Agent Results

- Agent response latency
- Memory retrieval success
- Memory relevance
- Recommendation quality
- Scenario success/failure

The final analysis also documents developer and engineering trade-offs, including:

- Implementation complexity
- Infrastructure requirements
- Dependencies
- Configuration
- Debugging
- Error visibility
- Maintainability
- Extensibility

Make sure to ask me and add any further benchmarks that is useful for this specific usecase and can highlight a potentially real pain point and difference factor.

The goal is not to prove that CLI or MCP is universally better.

The goal is to provide a small, reproducible experiment showing the practical trade-offs between CLI/direct integration and MCP integration from both a **developer perspective** and a **real-world AI agent perspective**.

## Code Explanation and Learning Documentation

This project is also a learning and research project. The implementation must be understandable by the developer, not treated as generated code that is accepted without explanation.

### Documentation location

Code explanations must be stored in:

`docs/explanations/`

Create a separate Markdown file for each materially implemented source file.

For example:

- `app/memory/base.py` → `docs/explanations/base.md`
- `app/memory/cli_adapter.py` → `docs/explanations/cli_adapter.md`
- `app/memory/mcp_adapter.py` → `docs/explanations/mcp_adapter.md`

### For every implementation phase

When a phase creates or materially modifies non-test implementation code:

1. Identify all relevant non-test source files created or materially modified during the phase.

2. Create or update the corresponding explanation file in `docs/explanations/`.

3. Each explanation must include:

   #### File Purpose
   - What the file does.
   - Why it is needed in the project architecture.
   - Which components interact with it.

   #### Classes and Data Structures
   For each important class or data structure:
   - Its responsibility.
   - Why it exists.
   - Its relationship to other components.

   #### Functions and Methods
   For each important function or method:
   - Inputs and parameters.
   - Step-by-step behavior.
   - Return value.
   - Why it is needed.

   Only document concepts that are actually relevant to the file.

   #### Execution Flow
   Explain how data or requests move through the file and into other components.

   #### Architecture and Design Reasoning
   Explain why the implementation is structured this way and how it supports the CLI-vs-MCP comparison.

4. Do not create explanations for test files unless explicitly requested.

5. Keep explanations tied to the actual implementation. Do not provide generic textbook explanations without connecting them to the project's code.

6. When a later phase changes an existing source file, update its existing explanation file rather than creating duplicate or contradictory documentation.

7. At the end of each implementation phase, record the code explanation files created or updated in `docs/project_progress.md`.

The goal is for the documentation to allow the developer to understand, explain, debug, and modify every important part of the implementation independently.