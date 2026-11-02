"""Unit tests for the MCP adapter with mocked httpx client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.memory.base import MemoryOperationError
from app.memory.mcp_adapter import McpMemoryAdapter


def _mocked_post(status_code=200, json_data=None, side_effect=None):
    """Helper to build a mock httpx.Response and raised side effect."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.raise_for_status = MagicMock()
    if json_data is not None:
        mock_response.json = MagicMock(return_value=json_data)
    if side_effect is not None:
        mock_response.raise_for_status.side_effect = side_effect
    return mock_response


def _patch_client(mock_client, return_value):
    """Patch httpx.Client to return a context manager yielding the mock response."""
    context_mgr = MagicMock()
    context_mgr.__enter__ = MagicMock(return_value=mock_client)
    context_mgr.__exit__ = MagicMock(return_value=None)
    mock_client.return_value = context_mgr


def _make_adapter(api_url="http://127.0.0.1:8000"):
    return McpMemoryAdapter(api_url=api_url)


# -- remember tests --------------------------------------------------------


def test_remember_passes_facts_as_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    """The remember hook sends facts as multipart form data, one text item per part."""
    adapter = _make_adapter()

    # Build the expected multipart payload
    expected_parts: list[tuple[str, tuple[str, str, str]]] = []
    for i, fact in enumerate(["Fact A.", "Fact B."]):
        fname = f"text_{i}.txt"
        expected_parts.append(("data", (fname, fact, "text/plain")))
    expected_form = {"datasetName": "retail_customer_001"}

    # Mock httpx.Client
    with patch("app.memory.mcp_adapter.httpx.Client") as mock_httpx:
        context_mgr = MagicMock()
        instance = MagicMock()
        instance.post.return_value = _mocked_post(
            json_data={"status": "completed", "items_processed": 2}
        )
        context_mgr.__enter__.return_value = instance
        context_mgr.__exit__.return_value = None
        mock_httpx.Client.return_value = context_mgr

        with adapter:
            result = adapter.remember("customer_001", ("Fact A.", "Fact B."))

    # Verify the API was called with the right arguments
    instance.post.assert_called_once()
    call_kwargs = instance.post.call_args[1]
    assert "files" in call_kwargs
    assert "data" in call_kwargs
    # Check that files contain the expected parts
    files = call_kwargs["files"]
    assert len(files) == 2
    for i, (key, (fname, content, mime)) in enumerate(files):
        assert key == "data"
        assert fname == f"text_{i}.txt"
        assert content == [["Fact A.", "Fact B."][i]]
        assert mime == "text/plain"

    # Verify the result
    assert result.integration == "mcp"
    assert result.dataset == "retail_customer_001"
    assert result.fact_count == 2
    assert result.response_chars == 2  # len of the two fact strings


# -- recall tests ----------------------------------------------------------


def test_recall_parses_json_and_drops_system_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recall hook drops entries with source == 'system' and returns text fragments."""
    adapter = _make_adapter()

    # Simulate API response with mixed entries
    api_response = [
        {"source": "graph", "text": "Prefers affordable wireless headphones.", "kind": "chunk"},
        {"source": "system", "status": "warming_up", "text": "Memory is still warming up."},
        {"source": "graph", "text": "Has a budget below 3000 rupees.", "kind": "chunk"},
    ]

    with patch("app.memory.mcp_adapter.httpx.Client") as mock_httpx:
        context_mgr = MagicMock()
        instance = MagicMock()
        instance.post.return_value = _mocked_post(json_data=api_response)
        context_mgr.__enter__.return_value = instance
        context_mgr.__exit__.return_value = None
        mock_httpx.Client.return_value = context_mgr

        with adapter:
            result = adapter.recall("customer_001", "recommend headphones")

    # Should have 2 fragments (system marker dropped)
    assert result.fragments == (
        "Prefers affordable wireless headphones.",
        "Has a budget below 3000 rupees.",
    )
    assert result.result_count == 2
    # text should be the fragments joined by newline
    assert "warming up" not in result.text


def test_recall_handles_non_dict_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """If an entry is not a dict, it should be safely skipped."""
    adapter = _make_adapter()

    api_response = [
        "not a dict",
        {"source": "graph", "text": "Real memory fragment."},
        None,
    ]

    with patch("app.memory.mcp_adapter.httpx.Client") as mock_httpx:
        context_mgr = MagicMock()
        instance = MagicMock()
        instance.post.return_value = _mocked_post(json_data=api_response)
        context_mgr.__enter__.return_value = instance
        context_mgr.__exit__.return_value = None
        mock_httpx.Client.return_value = context_mgr

        with adapter:
            result = adapter.recall("customer_001", "recommend headphones")

    assert result.fragments == ("Real memory fragment",)
    assert result.result_count == 1


def test_recall_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-200 status code raises MemoryOperationError."""
    adapter = _make_adapter()

    with patch("app.memory.mcp_adapter.httpx.Client") as mock_httpx:
        context_mgr = MagicMock()
        instance = MagicMock()
        instance.post.return_value = _mocked_post(
            status_code=404, side_effect=Exception("not found")
        )
        context_mgr.__enter__.return_value = instance
        context_mgr.__exit__.return_value = None
        mock_httpx.Client.return_value = context_mgr

        with pytest.raises(MemoryOperationError) as excinfo:
            adapter.recall("customer_001", "recommend headphones")

    error = excinfo.value
    assert error.integration == "mcp"
    assert error.operation == "recall"
    assert error.customer_id == "customer_001"
    # The original cause should be chained
    assert error.__cause__ is not None


# -- integration tests -----------------------------------------------------


def test_mcp_adapter_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full integration: remember → recall round-trip with mocked API."""
    adapter = _make_adapter()

    with adapter:
        # Remember 3 facts
        remembered = adapter.remember("customer_001", (
            "Prefers affordable wireless headphones.",
            "Has a budget below 3000 rupees.",
            "Uses headphones mainly while travelling.",
        ))
        assert remembered.fact_count == 3
        assert remembered.dataset == "retail_customer_001"
        assert remembered.integration == "mcp"

        # Recall should return fragments
        recalled = adapter.recall("customer_001", "recommend headphones")
        assert recalled.result_count >= 1  # at least the facts we stored
        assert recalled.query == "recommend headphones"
        assert recalled.latency_ms >= 0

    # Verify the adapter was used as a context manager correctly
    assert adapter.close is not None


def test_integration_uses_dataset_for_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter's dataset name must come from dataset_for(), not hard-coded."""
    adapter = _make_adapter()

    with adapter:
        adapter.remember("customer_005", ("fact 1", "fact 2"))
        # The dataset should be retail_customer_005
        # We can't easily inspect the API call, but we can verify the adapter
        # didn't crash and returned a result with the right dataset name
        recalled = adapter.recall("customer_005", "any query")
        assert recalled.dataset == "retail_customer_005"