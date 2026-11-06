"""Factory for creating memory adapters based on configuration.

Supports switching between CLI and MCP integration modes without changing
the application logic. The rest of the application depends only on the
MemoryAdapter interface.
"""

from __future__ import annotations

import os
from typing import Type

from app.memory.cli_adapter import CliMemoryAdapter
from app.memory.mcp_adapter import McpMemoryAdapter
from app.memory.base import MemoryAdapter


def get_adapter() -> MemoryAdapter:
    """Create and return a memory adapter based on MEMORY_MODE environment variable.

    Supported modes:
        - "cli":  CliMemoryAdapter (uses cognee-cli to talk to Cognee API)
        - "mcp":  McpMemoryAdapter (uses MCP protocol to talk to Cognee API)

    Raises MemoryConfigurationError if the mode is unknown or the adapter
    cannot be constructed from the current configuration.
    """
    memory_mode = os.getenv("MEMORY_MODE", "cli").lower()

    if memory_mode == "cli":
        return CliMemoryAdapter()
    elif memory_mode == "mcp":
        return McpMemoryAdapter()
    else:
        msg = f"Unknown MEMORY_MODE='{memory_mode}'; supported: cli, mcp"
        raise MemoryConfigurationError(msg, integration="factory", operation="get_adapter")