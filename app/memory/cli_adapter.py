"""Direct/CLI integration with the central Cognee API.

This adapter represents the "an application shells out to the Cognee CLI" path.
Verified against Cognee 1.6.6 (``cognee-cli``), where ``--api-url`` delegates the
``remember`` and ``recall`` commands to a running Cognee API server over HTTP, so
this path and the MCP path read and write the same backend.

Everything CLI-specific lives here: the two commands, their argument order, and
the parsing of the CLI's human-oriented stdout. The rest of the application sees
only :class:`app.memory.base.MemoryAdapter`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.memory.base import MemoryAdapter, MemoryConfigurationError, MemoryOperationError

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:8000"

#: Retrieval mode used for recall.
#:
#: Pinned rather than left to the server's rule-based auto-router so both
#: integrations issue the same retrieval and so the measured work is retrieval
#: itself. ``CHUNKS`` is a deterministic, non-generative retriever: it returns
#: stored memory verbatim, which is what relevance scoring needs, and it does not
#: depend on an LLM being configured. The MCP adapter must pin the same mode.
DEFAULT_QUERY_TYPE = "CHUNKS"

DEFAULT_TOP_K = 10
DEFAULT_TIMEOUT_SECONDS = 180.0

#: Repository root. The CLI loads ``.env`` from the process working directory, so
#: invoking it from anywhere else would change backend settings (notably
#: ``ENABLE_BACKEND_ACCESS_CONTROL``) and silently alter behaviour.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CliMemoryAdapter(MemoryAdapter):
    """Store and retrieve memory by invoking ``cognee-cli`` against the API."""

    integration = "cli"

    def __init__(
        self,
        *,
        api_url: str | None = None,
        cli_path: str | None = None,
        recall_query_type: str | None = None,
        top_k: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_url = (api_url or os.getenv("COGNEE_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.recall_query_type = (
            recall_query_type
            or os.getenv("COGNEE_RECALL_QUERY_TYPE")
            or DEFAULT_QUERY_TYPE
        )
        self.top_k = top_k or DEFAULT_TOP_K
        self.timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS

        resolved_cli = cli_path or os.getenv("COGNEE_CLI_PATH") or self._discover_cli()
        if not resolved_cli:
            raise MemoryConfigurationError(
                "cognee-cli not found; install the cognee package or set COGNEE_CLI_PATH",
                integration=self.integration,
                operation="configure",
            )
        self.cli_path = resolved_cli

    @staticmethod
    def _discover_cli() -> str | None:
        """Find ``cognee-cli`` on PATH or beside the running interpreter."""
        found = shutil.which("cognee-cli")
        if found:
            return found
        # uv/virtualenv installs put the script next to python but not always on PATH.
        candidate = Path(sys.executable).with_name("cognee-cli")
        return str(candidate) if candidate.is_file() else None

    # -- integration hooks -------------------------------------------------

    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        """Add every fact to ``dataset`` and build the graph, via the CLI."""
        args = [
            *self._base_args(),
            "remember",
            *facts,
            "--dataset-name",
            dataset,
        ]
        completed = self._run(args, operation="remember", customer_id=customer_id)
        return len(completed.stdout)

    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        """Return stored memory matching ``query`` from ``dataset``, via the CLI."""
        args = [
            *self._base_args(),
            "recall",
            query,
            "--datasets",
            dataset,
            "--query-type",
            self.recall_query_type,
            "--top-k",
            str(self.top_k),
            "--output-format",
            "json",
        ]
        completed = self._run(args, operation="recall", customer_id=customer_id)
        return fragments_from_cli_output(completed.stdout)

    # -- process plumbing --------------------------------------------------

    def _base_args(self) -> list[str]:
        return [self.cli_path, "--api-url", self.api_url]

    def _run(
        self,
        args: list[str],
        *,
        operation: str,
        customer_id: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=PROJECT_ROOT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise MemoryOperationError(
                f"cognee-cli {operation} timed out after {self.timeout_seconds:g}s "
                f"(api_url={self.api_url})",
                integration=self.integration,
                operation=operation,
                customer_id=customer_id,
            ) from exc
        except OSError as exc:
            raise MemoryOperationError(
                f"cognee-cli {operation} could not start: {exc}",
                integration=self.integration,
                operation=operation,
                customer_id=customer_id,
            ) from exc

        if completed.returncode != 0:
            raise MemoryOperationError(
                f"cognee-cli {operation} exited {completed.returncode}: "
                f"{_failure_detail(completed)}",
                integration=self.integration,
                operation=operation,
                customer_id=customer_id,
            )
        return completed


def _failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """Pick the most useful line the CLI printed on failure."""
    for stream in (completed.stderr, completed.stdout):
        lines = [line.strip() for line in (stream or "").splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return "no error output"


def fragments_from_cli_output(stdout: str) -> tuple[str, ...]:
    """Extract memory text from ``cognee-cli recall --output-format json`` stdout.

    The CLI prints a human-readable preamble before the JSON array, and that
    preamble can itself contain ``[`` (it echoes the dataset list), so the array
    is located by decoding from each candidate bracket rather than by taking the
    first one. Entries are returned in the order the server ranked them.

    ``system`` entries are Cognee markers ("memory still warming up", build
    failures), not customer memory. They are dropped from the fragments and
    logged, so a marker-only response reads as an empty recall instead of
    silently counting as retrieved memory.
    """
    results = _extract_json_list(stdout)
    fragments: list[str] = []
    for entry in results:
        if isinstance(entry, dict) and entry.get("source") == "system":
            logger.warning("cognee-cli recall returned a system marker: %s", entry.get("text"))
            continue
        text = _entry_text(entry)
        if text:
            fragments.append(text)
    return tuple(fragments)


def _entry_text(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        for key in ("text", "content"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _extract_json_list(stdout: str) -> list[Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(stdout):
        if character != "[":
            continue
        try:
            parsed, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    raise ValueError("cognee-cli recall produced no JSON result list")
