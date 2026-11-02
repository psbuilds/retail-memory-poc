"""Unit tests for the genuine, persistent MCP adapter."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from mcp import types

from app.memory.base import MemoryOperationError
from app.memory.mcp_adapter import McpMemoryAdapter


def _result(*texts):
    return SimpleNamespace(
        content=[types.TextContent(type="text", text=text) for text in texts]
    )


class FakeConnection:
    def __init__(self, url, timeout):
        self.url = url
        self.timeout = timeout
        self.calls = []
        self.closed = False

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "remember":
            return _result("Stored permanently in knowledge graph.")
        return _result("Prefers affordable wireless headphones.", "Memory is still warming up.")

    def close(self):
        self.closed = True


def test_endpoint_is_explicit_and_session_is_reused():
    with patch("app.memory.mcp_adapter._PersistentMcpSession", FakeConnection):
        adapter = McpMemoryAdapter(mcp_url="http://localhost:8001/mcp")
        adapter.remember("customer_001", ("Fact A", "Fact B"))
        first = adapter._connection
        result = adapter.recall("customer_001", "headphones")
        adapter.recall("customer_001", "travel")

        assert adapter.mcp_url == "http://localhost:8001/mcp"
        assert adapter._connection is first
        assert [call[0] for call in first.calls] == ["remember", "recall", "recall"]
        assert first.calls[0][1]["data"] == "Fact A\nFact B"
        assert first.calls[1][1] == {
            "query": "headphones",
            "datasets": "retail_customer_001",
            "search_type": "CHUNKS",
            "top_k": 10,
        }
        assert result.fragments == ("Prefers affordable wireless headphones.",)
        adapter.close()
        assert first.closed


def test_environment_endpoint_is_used(monkeypatch):
    monkeypatch.setenv("COGNEE_MCP_URL", "http://example.test/mcp")
    assert McpMemoryAdapter().mcp_url == "http://example.test/mcp"


def test_context_manager_closes_persistent_session():
    with patch("app.memory.mcp_adapter._PersistentMcpSession", FakeConnection):
        with McpMemoryAdapter() as adapter:
            adapter.recall("customer_001", "query")
            connection = adapter._connection
        assert connection.closed


def test_tool_errors_are_wrapped_and_chained():
    connection = MagicMock()
    cause = RuntimeError("server rejected tool call")
    connection.call_tool.side_effect = cause
    with patch("app.memory.mcp_adapter._PersistentMcpSession", return_value=connection):
        with pytest.raises(MemoryOperationError) as excinfo:
            McpMemoryAdapter().recall("customer_001", "query")

    assert excinfo.value.integration == "mcp"
    assert excinfo.value.operation == "recall"
    assert excinfo.value.customer_id == "customer_001"
    assert excinfo.value.__cause__ is cause


def test_session_initialization_failure_is_wrapped():
    cause = RuntimeError("handshake failed")
    with patch(
        "app.memory.mcp_adapter._PersistentMcpSession", side_effect=cause
    ), pytest.raises(MemoryOperationError) as excinfo:
        McpMemoryAdapter().recall("customer_001", "query")

    assert excinfo.value.operation == "initialize"
    assert excinfo.value.__cause__ is cause
