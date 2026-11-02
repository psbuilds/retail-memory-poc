"""Persistent MCP client adapter for the Cognee memory server."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from concurrent.futures import Future
from typing import Any

from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from app.memory.base import (
    MemoryAdapter,
    MemoryAdapterError,
    MemoryConfigurationError,
    MemoryOperationError,
)

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = "http://127.0.0.1:8001/mcp"
DEFAULT_QUERY_TYPE = "CHUNKS"
DEFAULT_TOP_K = 10
DEFAULT_TIMEOUT_SECONDS = 180.0


class _PersistentMcpSession:
    """Own an async MCP transport/session on a private event-loop thread."""

    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="mcp-session", daemon=True)
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._commands: asyncio.Queue[Any] | None = None
        self._stopped: Future[None] | None = None
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            self.close()
            raise self._startup_error

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.create_task(self._serve())
        self._loop.run_forever()
        self._loop.close()

    async def _serve(self) -> None:
        try:
            stack = contextlib.AsyncExitStack()
            await stack.__aenter__()
            read_stream, write_stream, *_ = await stack.enter_async_context(
                streamable_http_client(self.url)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            missing = {"remember", "recall"} - names
            if missing:
                raise RuntimeError(f"MCP server is missing required tools: {sorted(missing)}")
            self._stack = stack
            self._session = session
            self._commands = asyncio.Queue()
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                command = await self._commands.get()
                if command is None:
                    break
                name, arguments, result = command
                try:
                    value = await self._session.call_tool(name, arguments)
                except BaseException as exc:
                    result.set_exception(exc)
                else:
                    result.set_result(value)
        finally:
            await stack.aclose()
            self._loop.call_soon(self._loop.stop)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None or self._commands is None:
            raise RuntimeError("MCP session is not initialized")
        result: Future[Any] = Future()
        self._loop.call_soon_threadsafe(self._commands.put_nowait, (name, arguments, result))
        return result.result(timeout=self.timeout_seconds)

    def _submit(self, coroutine: Any) -> Any:
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=self.timeout_seconds)

    def close(self) -> None:
        if not self._thread.is_alive():
            return
        if self._commands is not None:
            self._loop.call_soon_threadsafe(self._commands.put_nowait, None)
        else:
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=self.timeout_seconds)


class McpMemoryAdapter(MemoryAdapter):
    """Use one MCP protocol session for all memory operations on this adapter."""

    integration = "mcp"

    def __init__(
        self,
        *,
        mcp_url: str | None = None,
        recall_query_type: str | None = None,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.mcp_url = (mcp_url or os.getenv("COGNEE_MCP_URL") or DEFAULT_MCP_URL).rstrip("/")
        self.recall_query_type = recall_query_type or os.getenv(
            "COGNEE_RECALL_QUERY_TYPE", DEFAULT_QUERY_TYPE
        )
        self.top_k = top_k or DEFAULT_TOP_K
        self.timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
        self._connection: _PersistentMcpSession | None = None
        self._closed = False

    def _get_connection(self) -> _PersistentMcpSession:
        if self._closed:
            raise MemoryConfigurationError(
                "MCP adapter is closed", integration=self.integration, operation="configure"
            )
        if self._connection is None:
            try:
                self._connection = _PersistentMcpSession(self.mcp_url, self.timeout_seconds)
            except asyncio.CancelledError as exc:
                raise MemoryOperationError(
                    f"MCP session initialization was cancelled: {exc}",
                    integration=self.integration,
                    operation="initialize",
                ) from exc
            except Exception as exc:
                raise MemoryOperationError(
                    f"MCP session initialization failed: {exc}",
                    integration=self.integration,
                    operation="initialize",
                ) from exc
        return self._connection

    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        result = self._call(
            "remember",
            {
                "data": "\n".join(facts),
                "dataset_name": dataset,
                "background": False,
                "self_improvement": True,
            },
            customer_id,
        )
        return len(_text_from_result(result))

    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        result = self._call(
            "recall",
            {
                "query": query,
                "datasets": dataset,
                "search_type": self.recall_query_type,
                "top_k": self.top_k,
            },
            customer_id,
        )
        return _fragments_from_result(result)

    def _call(self, name: str, arguments: dict[str, Any], customer_id: str) -> Any:
        try:
            return self._get_connection().call_tool(name, arguments)
        except MemoryAdapterError:
            raise
        except Exception as exc:
            raise MemoryOperationError(
                f"MCP {name} tool call failed: {exc}",
                integration=self.integration,
                operation=name,
                customer_id=customer_id,
            ) from exc

    def close(self) -> None:
        connection, self._connection = self._connection, None
        self._closed = True
        if connection is not None:
            connection.close()


def _text_from_result(result: Any) -> str:
    return "\n".join(
        content.text
        for content in getattr(result, "content", ())
        if isinstance(content, types.TextContent) and content.text
    )


def _fragments_from_result(result: Any) -> tuple[str, ...]:
    fragments: list[str] = []
    for line in _text_from_result(result).splitlines():
        text = line.strip()
        if text and not _is_system_marker(text):
            fragments.append(text)
    return tuple(dict.fromkeys(fragments))


def _is_system_marker(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith(("memory is still", "cognee", "knowledge graph"))
