"""Common memory interface for the Retail Memory POC.

The agent and the benchmark depend on this module only — never on Cognee CLI
commands or MCP protocol details. Each integration implements :class:`MemoryAdapter`
and nothing else changes when the mode is switched.

Three things are deliberately owned by this module rather than by the adapters,
because duplicating them would let the two paths drift apart and invalidate the
comparison:

1. **Dataset naming** — ``dataset_for`` is the single place the memory scope is
   derived from a customer id. If the CLI and MCP adapters computed the scope
   themselves, they could read different memory while looking correct.
2. **Timing** — both adapters get latency from the same wrapper, so the number
   means the same thing on both sides. The measured region starts at the public
   call, which is *outside* the adapter, and ends after the integration work.
3. **Result normalisation** — recalled fragments are joined and counted here, so
   ``result_count`` and ``response_chars`` are comparable across integrations.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import ClassVar

logger = logging.getLogger(__name__)

DATASET_PREFIX = "retail_"

_CUSTOMER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

#: Separator used when the recalled fragments are flattened into agent context.
FRAGMENT_SEPARATOR = "\n"


class MemoryAdapterError(RuntimeError):
    """Base error for any failure raised by a memory adapter.

    Carries the integration and operation that failed so error visibility can be
    compared without parsing log lines. The original exception is always chained
    (``raise ... from exc``), so ``__cause__`` keeps the backend's own message.
    """

    def __init__(
        self,
        message: str,
        *,
        integration: str = "unknown",
        operation: str = "unknown",
        customer_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.integration = integration
        self.operation = operation
        self.customer_id = customer_id

    def __str__(self) -> str:
        details = [f"integration={self.integration}", f"operation={self.operation}"]
        if self.customer_id is not None:
            details.append(f"customer_id={self.customer_id}")
        return f"{super().__str__()} [{' '.join(details)}]"


class MemoryConfigurationError(MemoryAdapterError):
    """The adapter cannot be constructed from the current configuration."""


class MemoryOperationError(MemoryAdapterError):
    """A remember/recall call failed while talking to the memory backend."""


@dataclass(frozen=True, slots=True)
class RememberResult:
    """Outcome of storing one customer's facts."""

    integration: str
    customer_id: str
    dataset: str
    fact_count: int
    response_chars: int
    latency_ms: float


@dataclass(frozen=True, slots=True)
class RecallResult:
    """Outcome of a single memory lookup.

    ``fragments`` keeps the backend's items separate so ``result_count`` can
    detect a successful-but-empty recall; ``text`` is the joined context the
    agent actually receives.
    """

    integration: str
    customer_id: str
    dataset: str
    query: str
    fragments: tuple[str, ...]
    latency_ms: float

    @property
    def text(self) -> str:
        return FRAGMENT_SEPARATOR.join(self.fragments)

    @property
    def result_count(self) -> int:
        return len(self.fragments)

    @property
    def response_chars(self) -> int:
        return len(self.text)


def dataset_for(customer_id: str) -> str:
    """Derive the memory scope for a customer.

    Both adapters must use this function; see the module docstring.
    """
    return f"{DATASET_PREFIX}{customer_id}"


def _validate_customer_id(customer_id: str) -> str:
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("customer_id must be a non-empty string")
    customer_id = customer_id.strip()
    if not _CUSTOMER_ID_PATTERN.match(customer_id):
        raise ValueError(
            f"customer_id {customer_id!r} must contain only letters, digits, "
            "underscores, or hyphens"
        )
    return customer_id


def _validate_facts(facts: Sequence[str]) -> tuple[str, ...]:
    if isinstance(facts, str):
        raise ValueError("facts must be a sequence of strings, not a single string")
    materialised = tuple(facts)
    cleaned = tuple(
        fact.strip() for fact in materialised if isinstance(fact, str) and fact.strip()
    )
    if not cleaned:
        raise ValueError("facts must contain at least one non-empty string")
    if len(cleaned) != len(materialised):
        raise ValueError("facts must not contain empty or non-string entries")
    return cleaned


def _validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    return query.strip()


class MemoryAdapter(ABC):
    """Minimal memory interface shared by the CLI and MCP integrations.

    Subclasses implement :meth:`_remember` and :meth:`_recall` only. Argument
    validation, dataset naming, timing, logging, and error wrapping happen here
    so both integrations are measured on identical terms.
    """

    integration: ClassVar[str] = "unknown"

    def remember(self, customer_id: str, facts: Sequence[str]) -> RememberResult:
        """Store ``facts`` as memory for ``customer_id``."""
        customer_id = _validate_customer_id(customer_id)
        cleaned = _validate_facts(facts)
        dataset = dataset_for(customer_id)

        started = perf_counter()
        try:
            response_chars = self._remember(customer_id, cleaned, dataset)
        except MemoryAdapterError:
            raise
        except Exception as exc:
            self._log_failure("remember", customer_id, exc)
            raise MemoryOperationError(
                f"{self.integration} remember failed: {exc}",
                integration=self.integration,
                operation="remember",
                customer_id=customer_id,
            ) from exc
        latency_ms = (perf_counter() - started) * 1000

        logger.debug(
            "%s remember customer_id=%s facts=%d latency_ms=%.2f",
            self.integration,
            customer_id,
            len(cleaned),
            latency_ms,
        )
        return RememberResult(
            integration=self.integration,
            customer_id=customer_id,
            dataset=dataset,
            fact_count=len(cleaned),
            response_chars=max(0, int(response_chars)),
            latency_ms=latency_ms,
        )

    def recall(self, customer_id: str, query: str) -> RecallResult:
        """Return memory fragments relevant to ``query`` for ``customer_id``."""
        customer_id = _validate_customer_id(customer_id)
        query = _validate_query(query)
        dataset = dataset_for(customer_id)

        started = perf_counter()
        try:
            fragments = self._recall(customer_id, query, dataset)
        except MemoryAdapterError:
            raise
        except Exception as exc:
            self._log_failure("recall", customer_id, exc)
            raise MemoryOperationError(
                f"{self.integration} recall failed: {exc}",
                integration=self.integration,
                operation="recall",
                customer_id=customer_id,
            ) from exc
        latency_ms = (perf_counter() - started) * 1000

        cleaned = tuple(fragment.strip() for fragment in fragments if fragment and fragment.strip())
        logger.debug(
            "%s recall customer_id=%s results=%d latency_ms=%.2f",
            self.integration,
            customer_id,
            len(cleaned),
            latency_ms,
        )
        return RecallResult(
            integration=self.integration,
            customer_id=customer_id,
            dataset=dataset,
            query=query,
            fragments=cleaned,
            latency_ms=latency_ms,
        )

    def close(self) -> None:
        """Release integration resources. No-op unless the adapter holds a session."""

    def __enter__(self) -> MemoryAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @abstractmethod
    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        """Store ``facts`` in ``dataset``; return the acknowledgement length in chars."""

    @abstractmethod
    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        """Return raw context fragments for ``query`` from ``dataset``."""

    def _log_failure(self, operation: str, customer_id: str, exc: Exception) -> None:
        logger.warning(
            "%s %s failed customer_id=%s error_type=%s error=%s",
            self.integration,
            operation,
            customer_id,
            type(exc).__name__,
            exc,
        )
