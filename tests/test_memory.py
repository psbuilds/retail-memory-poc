"""Contract tests for app.memory.base — no external services required."""

from __future__ import annotations

import pytest

from app.memory.base import (
    MemoryAdapter,
    MemoryAdapterError,
    MemoryOperationError,
    RecallResult,
    RememberResult,
    dataset_for,
)


class FakeAdapter(MemoryAdapter):
    """In-memory stand-in used to pin down the shared contract."""

    integration = "fake"

    def __init__(self, fragments: tuple[str, ...] = (), fail_with: Exception | None = None) -> None:
        self.fragments = fragments
        self.fail_with = fail_with
        self.calls: list[tuple[str, str, str]] = []
        self.closed = False

    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        self.calls.append(("remember", customer_id, dataset))
        if self.fail_with is not None:
            raise self.fail_with
        return len(" ".join(facts))

    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        self.calls.append(("recall", customer_id, dataset))
        if self.fail_with is not None:
            raise self.fail_with
        return self.fragments

    def close(self) -> None:
        self.closed = True


def test_dataset_naming_is_shared() -> None:
    assert dataset_for("customer_001") == "retail_customer_001"


def test_remember_returns_common_result() -> None:
    adapter = FakeAdapter()
    result = adapter.remember("customer_001", ["Prefers wireless.", "Budget below ₹3000."])

    assert isinstance(result, RememberResult)
    assert result.integration == "fake"
    assert result.customer_id == "customer_001"
    assert result.dataset == "retail_customer_001"
    assert result.fact_count == 2
    assert result.response_chars > 0
    assert result.latency_ms >= 0
    assert adapter.calls == [("remember", "customer_001", "retail_customer_001")]


def test_recall_flattens_fragments_consistently() -> None:
    adapter = FakeAdapter(fragments=("Prefers wireless.", "Budget below ₹3000."))
    result = adapter.recall("customer_001", "recommend headphones")

    assert isinstance(result, RecallResult)
    assert result.fragments == ("Prefers wireless.", "Budget below ₹3000.")
    assert result.text == "Prefers wireless.\nBudget below ₹3000."
    assert result.result_count == 2
    assert result.response_chars == len(result.text)
    assert result.query == "recommend headphones"


def test_empty_recall_is_success_with_zero_results() -> None:
    result = FakeAdapter().recall("customer_001", "anything")
    assert result.result_count == 0
    assert result.response_chars == 0


@pytest.mark.parametrize(
    "customer_id",
    ["", "   ", "customer 001", "customer/001", None],
)
def test_invalid_customer_id_raises_value_error(customer_id: object) -> None:
    adapter = FakeAdapter()
    with pytest.raises(ValueError):
        adapter.remember(customer_id, ["some fact"])  # type: ignore[arg-type]


def test_invalid_facts_raise_value_error() -> None:
    adapter = FakeAdapter()
    with pytest.raises(ValueError):
        adapter.remember("customer_001", [])
    with pytest.raises(ValueError):
        adapter.remember("customer_001", "Prefers wireless.")
    with pytest.raises(ValueError):
        adapter.remember("customer_001", ["   "])


def test_invalid_query_raises_value_error() -> None:
    with pytest.raises(ValueError):
        FakeAdapter().recall("customer_001", "")


def test_backend_failure_is_wrapped_and_chained() -> None:
    original = ConnectionError("connection refused")
    adapter = FakeAdapter(fail_with=original)

    with pytest.raises(MemoryOperationError) as excinfo:
        adapter.recall("customer_001", "recommend headphones")

    error = excinfo.value
    assert isinstance(error, MemoryAdapterError)
    assert error.integration == "fake"
    assert error.operation == "recall"
    assert error.customer_id == "customer_001"
    assert error.__cause__ is original
    assert "connection refused" in str(error)


def test_memory_adapter_error_is_not_rewrapped() -> None:
    original = MemoryAdapterError("already specific", integration="fake", operation="recall")
    adapter = FakeAdapter(fail_with=original)

    with pytest.raises(MemoryAdapterError) as excinfo:
        adapter.recall("customer_001", "recommend headphones")
    assert excinfo.value is original


def test_context_manager_closes_adapter() -> None:
    adapter = FakeAdapter()
    with adapter as entered:
        assert entered is adapter
    assert adapter.closed is True


def test_adapter_cannot_be_instantiated_without_hooks() -> None:
    with pytest.raises(TypeError):
        MemoryAdapter()  # type: ignore[abstract]
