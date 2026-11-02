"""MCP integration with the central Cognee API via the MCP protocol.

This adapter represents the "MCP integration" path. It communicates with the same
central Cognee API server that the CLI adapter uses, but through the MCP server's
tool interface using the ``mcp`` SDK, rather than shelling out to ``cognee-cli``
or making direct HTTP REST API calls.

Everything MCP-specific lives here: the MCP client setup, the tool calls (``remember``
and ``recall``), the response parsing, and the error handling. The rest of the
application sees only :class:`app.memory.base.MemoryAdapter`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession, types

from app.memory.base import MemoryAdapter, MemoryConfigurationError, MemoryOperationError, dataset_for

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_QUERY_TYPE = "CHUNKS"
DEFAULT_TOP_K = 10


def _run_async(coro):
    """Bridge async code into the synchronous MemoryAdapter interface.

    The MemoryAdapter ABC requires _remember and _recall to be synchronous methods,
    but the MCP SDK is async. This helper runs a coroutine in a fresh event loop.
    """
    try:
        return asyncio.get_event_loop().run_until_complete(coro)
    except RuntimeError:
        # No event loop in current thread; use anyio
        import anyio

        def _run():
            return asyncio.get_event_loop().run_until_complete(coro)

        return anyio.run(_run)


class McpMemoryAdapter(MemoryAdapter):
    """Store and retrieve memory by calling the Cognee MCP server's tools.

    The MCP server runs alongside the Cognee API and forwards tool calls (``remember``
    and ``recall``) over HTTP to the same backend as the CLI path. Both integrations
    read and write the exact same knowledge graph, so the only difference is the
    integration mechanism (MCP tool call vs CLI subprocess).

    The adapter uses the ``mcp`` SDK's ``streamable_http_client`` and ``ClientSession``
    to communicate with the MCP server via the MCP protocol. The ``api_url`` defaults
    to ``http://127.0.0.1:8000``, which is the Cognee API server. The MCP server
    typically runs on ``http://127.0.0.1:8001/mcp`` and proxies tool calls to the
    same backend.
    """

    integration = "mcp"

    def __init__(
        self,
        *,
        api_url: str | None = None,
        recall_query_type: str | None = None,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_url = (api_url or "http://127.0.0.1:8000").rstrip("/")
        self.recall_query_type = recall_query_type or DEFAULT_QUERY_TYPE
        self.top_k = top_k or DEFAULT_TOP_K
        self.timeout_seconds = timeout_seconds or 180.0

    # -- integration hooks -------------------------------------------------

    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        """Add every fact to ``dataset`` via the Cognee MCP server (MCP path).

        Each fact is sent as a ``remember`` tool call via the MCP protocol.
        The MCP server proxies the calls to the Cognee API, which stores the facts
        in the same knowledge graph that the CLI path uses.

        Returns the number of facts stored, as expected by the base class interface.
        """
        total_stored = 0
        for i, fact in enumerate(facts):
            remember_args = {
                "data": fact,
                "dataset_name": dataset,
            }
            stored = _remember_single(remember_args)
            total_stored += stored
        return total_stored

    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        """Return stored memory matching ``query`` from ``dataset`` via the MCP protocol.

        Uses ``search_type="CHUNKS"`` and ``top_k=10`` to match the CLI adapter's
        retrieval settings, ensuring fair comparison of latency and result format.
        """
        recall_args = {
            "query": query,
            "search_type": "CHUNKS",
            "top_k": 10,
            "datasets": [dataset],
        }
        return _recall_single(recall_args)


def _remember_single(arguments: dict[str, Any]) -> int:
    """Execute a single ``remember`` tool call via the MCP protocol.

    Returns the number of items stored (typically 1 per call).
    """
    api_url = arguments.get("dataset_name", "http://127.0.0.1:8001/mcp")

    async def _remember_coro() -> int:
        async with streamable_http_client(api_url) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("remember", arguments)
                # Parse TextContent responses
                items_stored = 0
                for content in result.content:
                    if isinstance(content, types.TextContent):
                        text = content.text or ""
                        if "Background process" in text:
                            # Background mode returns immediately; count as 1
                            items_stored += 1
                        elif "Stored" in text:
                            items_stored += 1
                return items_stored

    return _run_async(_remember_coro())


def _recall_single(arguments: dict[str, Any]) -> tuple[str, ...]:
    """Execute a single ``recall`` tool call via the MCP protocol.

    Returns a tuple of memory fragments (stripped, non-empty).
    """
    api_url = arguments.get("datasets", ["http://127.0.0.1:8001/mcp"])[0]

    async def _recall_coro() -> tuple[str, ...]:
        async with streamable_http_client(api_url) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("recall", arguments)
                # Parse TextContent responses, dropping system markers
                fragments: list[str] = []
                for content in result.content:
                    if isinstance(content, types.TextContent):
                        text = content.text or ""
                        # Drop system marker lines (same logic as CLI adapter)
                        # System markers typically appear as lines like:
                        # "Memory is still warming up" or Cognee build artifacts
                        for line in text.split("\n"):
                            stripped = line.strip()
                            if not stripped:
                                continue
                            # Heuristic: drop lines that look like system messages
                            # (not customer preferences)
                            if stripped.startswith("Memory is still") or stripped.startswith(
                                "Cognee"
                            ) or stripped.startswith("knowledge graph"):
                                continue
                            # Also drop empty or very short lines that are likely markers
                            if len(stripped) < 3:
                                continue
                            fragments.append(stripped)
                # Remove duplicates while preserving order
                seen = set()
                unique_fragments = []
                for f in fragments:
                    if f not in seen:
                        seen.add(f)
                        unique_fragments.append(f)
                return tuple(unique_fragments)

    return _run_async(_recall_coro())