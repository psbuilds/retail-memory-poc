"""Unit tests for the CLI adapter. ``subprocess.run`` is mocked throughout."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from app.memory.base import MemoryConfigurationError, MemoryOperationError
from app.memory.cli_adapter import (
    CliMemoryAdapter,
    fragments_from_cli_output,
)

RECALL_STDOUT = """Recalling: 'recommend headphones' (type: CHUNKS) in datasets ['retail_customer_001']
[
  {
    "kind": "chunk",
    "source": "graph",
    "text": "Prefers affordable wireless headphones.",
    "dataset_name": "retail_customer_001"
  },
  {
    "kind": "chunk",
    "source": "graph",
    "text": "Has a budget below ₹3000.",
    "dataset_name": "retail_customer_001"
  }
]
"""


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def adapter() -> CliMemoryAdapter:
    return CliMemoryAdapter(api_url="http://127.0.0.1:8000", cli_path="/fake/cognee-cli")


def _run_with(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        calls.append(args)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("app.memory.cli_adapter.subprocess.run", fake_run)
    return calls


def test_remember_invokes_api_delegated_cli(adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _run_with(monkeypatch, _completed([], stdout="Remembering 2 item(s)...\nSuccess!\n"))

    result = adapter.remember("customer_001", ["Prefers wireless.", "Budget below ₹3000."])

    assert calls == [
        [
            "/fake/cognee-cli",
            "--api-url",
            "http://127.0.0.1:8000",
            "remember",
            "Prefers wireless.",
            "Budget below ₹3000.",
            "--dataset-name",
            "retail_customer_001",
        ]
    ]
    assert result.integration == "cli"
    assert result.dataset == "retail_customer_001"
    assert result.fact_count == 2
    assert result.response_chars == len("Remembering 2 item(s)...\nSuccess!\n")


def test_recall_pins_dataset_and_query_type(adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _run_with(monkeypatch, _completed([], stdout=RECALL_STDOUT))

    result = adapter.recall("customer_001", "recommend headphones")

    assert calls == [
        [
            "/fake/cognee-cli",
            "--api-url",
            "http://127.0.0.1:8000",
            "recall",
            "recommend headphones",
            "--datasets",
            "retail_customer_001",
            "--query-type",
            "CHUNKS",
            "--top-k",
            "10",
            "--output-format",
            "json",
        ]
    ]
    assert result.fragments == (
        "Prefers affordable wireless headphones.",
        "Has a budget below ₹3000.",
    )
    assert result.result_count == 2
    assert "₹3000" in result.text


def test_recall_uses_configured_query_type_and_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = CliMemoryAdapter(
        api_url="http://127.0.0.1:8000",
        cli_path="/fake/cognee-cli",
        recall_query_type="CHUNKS_LEXICAL",
        top_k=3,
    )
    calls = _run_with(monkeypatch, _completed([], stdout="[]"))
    adapter.recall("customer_001", "q")
    assert "CHUNKS_LEXICAL" in calls[0]
    assert calls[0][calls[0].index("--top-k") + 1] == "3"


def test_nonzero_exit_raises_operation_error_with_cli_detail(
    adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_with(
        monkeypatch,
        _completed(
            [],
            returncode=1,
            stderr="Error: Could not reach the Cognee API at http://127.0.0.1:8000\n",
        ),
    )

    with pytest.raises(MemoryOperationError) as excinfo:
        adapter.recall("customer_001", "recommend headphones")

    error = excinfo.value
    assert error.integration == "cli"
    assert error.operation == "recall"
    assert error.customer_id == "customer_001"
    assert "Could not reach the Cognee API" in str(error)
    assert "exited 1" in str(error)


def test_timeout_raises_operation_error(adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_with(monkeypatch, subprocess.TimeoutExpired(cmd="cognee-cli", timeout=180.0))

    with pytest.raises(MemoryOperationError) as excinfo:
        adapter.remember("customer_001", ["Prefers wireless."])
    assert "timed out" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)


def test_missing_cli_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.memory.cli_adapter.shutil.which", lambda _: None)
    monkeypatch.setattr(
        "app.memory.cli_adapter.Path.is_file", lambda self: False, raising=True
    )
    with pytest.raises(MemoryConfigurationError):
        CliMemoryAdapter(api_url="http://127.0.0.1:8000")


def test_unparseable_output_raises_operation_error(
    adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_with(monkeypatch, _completed([], stdout="No results found for your query.\n"))
    with pytest.raises(MemoryOperationError):
        adapter.recall("customer_001", "recommend headphones")


def test_invalid_customer_id_is_rejected_before_running_cli(
    adapter: CliMemoryAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _run_with(monkeypatch, _completed([], stdout=RECALL_STDOUT))
    with pytest.raises(ValueError):
        adapter.recall("", "recommend headphones")
    assert calls == []


# -- stdout parsing -------------------------------------------------------


def test_preamble_brackets_do_not_break_parsing() -> None:
    # The preamble contains "[...]" before the payload; naive index('[') fails.
    assert fragments_from_cli_output(RECALL_STDOUT) == (
        "Prefers affordable wireless headphones.",
        "Has a budget below ₹3000.",
    )


def test_system_markers_are_not_counted_as_memory() -> None:
    stdout = """Recalling: 'x' (type: CHUNKS) in datasets ['d']
[
  {"source": "system", "status": "warming_up", "text": "Memory is still warming up."},
  {"source": "graph", "text": "Prefers wireless."}
]
"""
    assert fragments_from_cli_output(stdout) == ("Prefers wireless.",)


def test_empty_result_list_yields_no_fragments() -> None:
    assert fragments_from_cli_output("Recalling: 'x' in datasets []\n[]\n") == ()


def test_plain_string_entries_are_supported() -> None:
    assert fragments_from_cli_output('["Prefers wireless.", "Budget below 3000."]') == (
        "Prefers wireless.",
        "Budget below 3000.",
    )
