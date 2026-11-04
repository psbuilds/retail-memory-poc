"""Reproducible Phase 9 benchmark harness.

This module defines workload execution and metric calculation only. Full
experiments are intentionally not run at import or test time; use the CLI in a
later Phase 10 run.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import statistics
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chatbot import RetailChatbot
from app.memory.base import MemoryAdapter, MemoryAdapterError, RecallResult
from app.memory.cli_adapter import CliMemoryAdapter
from app.memory.mcp_adapter import McpMemoryAdapter
from app.products import Product, load_catalog

RAW_ROOT = ROOT / "results" / "raw"
SUMMARY_ROOT = ROOT / "results" / "summaries"
DEFAULT_REPETITIONS = 10
DEFAULT_WARMUPS = 3


@dataclass(frozen=True, slots=True)
class Observation:
    run_id: str
    timestamp_utc: str
    adapter: str
    experiment_group: str
    operation: str
    scenario_id: str = ""
    customer_id: str = ""
    repetition: int = 0
    execution_order: int = 0
    cold_or_warm: str = "warm"
    latency_ms: float = 0.0
    adapter_latency_ms: float | None = None
    status: str = "success"
    returned_count: int = 0
    relevance_score: float | None = None
    recommendation_match: float | None = None
    expected_product_coverage: float | None = None
    excluded_product_violations: int | None = None
    error_type: str = ""
    error_message: str = ""
    response_json: str = ""
    contamination_detected: bool = False
    contamination_count: int = 0
    expected_namespace: str = ""
    contamination_details: str = ""


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    """Return linear-interpolated percentile, or None for no observations."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_latencies(values: Sequence[float]) -> dict[str, float | int | None]:
    """Calculate documented latency metrics without inventing missing samples."""
    if not values:
        return {"runs": 0, "mean_ms": None, "median_ms": None, "stdev_ms": None, "min_ms": None, "max_ms": None, "p95_ms": None}
    return {
        "runs": len(values),
        "mean_ms": statistics.mean(values),
        "median_ms": statistics.median(values),
        "stdev_ms": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min_ms": min(values),
        "max_ms": max(values),
        "p95_ms": percentile(values, 95),
    }


def summarize_observations(observations: Sequence[Observation]) -> dict[str, Any]:
    successes = [item for item in observations if item.status == "success"]
    latencies = [item.latency_ms for item in successes]
    summary = summarize_latencies(latencies)
    summary.update({
        "total_operations": len(observations),
        "successful_operations": len(successes),
        "failed_operations": len(observations) - len(successes),
        "success_rate": len(successes) / len(observations) if observations else None,
        "failure_rate": (len(observations) - len(successes)) / len(observations) if observations else None,
    })
    return summary


class NoMemoryAdapter(MemoryAdapter):
    """Control adapter that never contributes memory and never calls Cognee."""

    integration = "no_memory"

    def _remember(self, customer_id: str, facts: tuple[str, ...], dataset: str) -> int:
        return 0

    def _recall(self, customer_id: str, query: str, dataset: str) -> tuple[str, ...]:
        return ()


def adapter_factory(name: str) -> Callable[[], MemoryAdapter]:
    if name == "cli":
        return CliMemoryAdapter
    if name == "mcp":
        return McpMemoryAdapter
    if name == "no_memory":
        return NoMemoryAdapter
    raise ValueError(f"unknown adapter: {name}")


def relevance_score(fragments: Sequence[str], expected_terms: Sequence[str]) -> float:
    if not expected_terms:
        return 0.0
    text = "\n".join(fragments).casefold()
    return sum(term.casefold() in text for term in expected_terms) / len(expected_terms)


def evaluate_recommendations(product_ids: Sequence[str], scenario: dict[str, Any]) -> dict[str, Any]:
    expected = set(scenario["expected_product_ids"])
    excluded = set(scenario["excluded_product_ids"])
    recommended = set(product_ids)
    return {
        "recommendation_match": len(expected & recommended) / len(expected) if expected else 0.0,
        "expected_product_coverage": len(expected & recommended) / len(expected) if expected else 0.0,
        "excluded_product_violations": len(excluded & recommended),
    }


def namespace_customer_id(run_id: str, adapter_name: str, customer_id: str) -> str:
    """Create a stable, run- and adapter-scoped synthetic customer ID."""
    return f"phase10_{run_id}_{adapter_name}_{customer_id}"


def prepare_workload(run_id: str, adapter_name: str, customers: Sequence[dict[str, Any]], scenarios: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Copy committed workload data into one run-scoped namespace.

    Provenance markers are synthetic and preserve the original fact text, making
    cross-run/customer leakage observable without filtering returned fragments.
    """
    id_map = {customer["customer_id"]: namespace_customer_id(run_id, adapter_name, customer["customer_id"]) for customer in customers}
    scoped_customers = [{
        **customer,
        "customer_id": id_map[customer["customer_id"]],
        "source_customer_id": customer["customer_id"],
        "facts": [f"[benchmark_namespace={run_id} customer={id_map[customer['customer_id']]}] {fact}" for fact in customer["facts"]],
    } for customer in customers]
    scoped_scenarios = [{**scenario, "customer_id": id_map[scenario["customer_id"]]} for scenario in scenarios]
    return scoped_customers, scoped_scenarios


def contamination_report(fragments: Sequence[str], expected_namespace: str, expected_customer_id: str, known_namespaces: Sequence[str], known_customer_ids: Sequence[str]) -> dict[str, Any]:
    """Report provenance markers without removing or rewriting recall fragments."""
    text = "\n".join(fragments)
    observed_namespaces = re.findall(r"benchmark_namespace=([A-Za-z0-9_-]+)", text)
    observed_customers = re.findall(r"customer=(phase10_[A-Za-z0-9_-]+)", text)
    markers = [f"benchmark_namespace={name}" for name in dict.fromkeys(observed_namespaces) if name != expected_namespace]
    customer_markers = [f"customer={customer}" for customer in dict.fromkeys(observed_customers) if customer != expected_customer_id]
    unexpected = markers + customer_markers
    return {
        "contamination_detected": bool(unexpected),
        "contamination_count": len(unexpected),
        "expected_namespace": expected_namespace,
        "contamination_details": "; ".join(unexpected),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed(run_id: str, adapter: str, group: str, operation: str, repetition: int, order: int, started: float, exc: Exception, **fields: Any) -> Observation:
    return Observation(run_id, _now(), adapter, group, operation, repetition=repetition, execution_order=order, latency_ms=(perf_counter() - started) * 1000, status="failure", error_type=type(exc).__name__, error_message=str(exc), **fields)


def run_memory_writes(run_id: str, adapter_name: str, customers: Sequence[dict[str, Any]], repetitions: int, warmups: int = 0) -> list[Observation]:
    observations: list[Observation] = []
    adapter = adapter_factory(adapter_name)()
    try:
        for _ in range(warmups):
            for customer in customers:
                adapter.remember(customer["customer_id"], customer["facts"])
        for repetition in range(repetitions):
            for order, customer in enumerate(customers):
                started = perf_counter()
                try:
                    result = adapter.remember(customer["customer_id"], customer["facts"])
                    observations.append(Observation(run_id, _now(), adapter_name, "memory_write", "remember", customer_id=customer["customer_id"], repetition=repetition, execution_order=order, cold_or_warm="cold" if repetition == 0 and order == 0 else "warm", latency_ms=(perf_counter() - started) * 1000, adapter_latency_ms=result.latency_ms, returned_count=result.fact_count))
                except Exception as exc:
                    observations.append(_failed(run_id, adapter_name, "memory_write", "remember", repetition, order, started, exc, customer_id=customer["customer_id"]))
    finally:
        adapter.close()
    return observations


def run_memory_recall(run_id: str, adapter_name: str, customers: Sequence[dict[str, Any]], scenarios: Sequence[dict[str, Any]], repetitions: int, warmups: int = 0) -> list[Observation]:
    """Measure recall independently after one identical, unmeasured seed phase."""
    facts_by_customer = {customer["customer_id"]: customer["facts"] for customer in customers}
    known_namespaces = [run_id]
    known_customers = [customer["customer_id"] for customer in customers]
    observations: list[Observation] = []
    seed_failures: set[str] = set()
    adapter = adapter_factory(adapter_name)()
    try:
        for customer in customers:
            try:
                adapter.remember(customer["customer_id"], facts_by_customer[customer["customer_id"]])
            except Exception as exc:
                seed_failures.add(customer["customer_id"])
                for order, scenario in enumerate(scenarios):
                    if scenario["customer_id"] != customer["customer_id"]:
                        continue
                    observations.append(Observation(
                        run_id, _now(), adapter_name, "memory_recall", "seed",
                        scenario_id=scenario["id"], customer_id=scenario["customer_id"],
                        execution_order=order, status="setup_failure",
                        error_type=type(exc).__name__, error_message=str(exc),
                    ))
                continue
        for _ in range(warmups):
            for scenario in scenarios:
                if scenario["customer_id"] in seed_failures:
                    continue
                adapter.recall(scenario["customer_id"], scenario["query"])
        for repetition in range(repetitions):
            for order, scenario in enumerate(scenarios):
                if scenario["customer_id"] in seed_failures:
                    continue
                started = perf_counter()
                try:
                    result = adapter.recall(scenario["customer_id"], scenario["query"])
                    observations.append(Observation(
                        run_id, _now(), adapter_name, "memory_recall", "recall",
                        scenario_id=scenario["id"], customer_id=scenario["customer_id"],
                        repetition=repetition, execution_order=order,
                        cold_or_warm="cold" if warmups == 0 and repetition == 0 and order == 0 else "warm",
                        latency_ms=(perf_counter() - started) * 1000,
                        adapter_latency_ms=result.latency_ms, returned_count=result.result_count,
                        relevance_score=relevance_score(result.fragments, scenario["expected_memory"]),
                        **contamination_report(result.fragments, run_id, scenario["customer_id"], known_namespaces, known_customers),
                    ))
                except Exception as exc:
                    observations.append(_failed(
                        run_id, adapter_name, "memory_recall", "recall", repetition,
                        order, started, exc, scenario_id=scenario["id"],
                        customer_id=scenario["customer_id"],
                    ))
    finally:
        adapter.close()
    return observations


def run_scenarios(run_id: str, adapter_name: str, customers: Sequence[dict[str, Any]], scenarios: Sequence[dict[str, Any]], catalog: Sequence[Product], repetitions: int, no_memory: bool = False, warmups: int = 0) -> list[Observation]:
    facts_by_customer = {customer["customer_id"]: customer["facts"] for customer in customers}
    observations: list[Observation] = []
    adapter = adapter_factory(adapter_name)()
    chatbot = RetailChatbot(adapter, catalog, top_k=3)
    known_namespaces = [run_id]
    known_customers = [customer["customer_id"] for customer in customers]
    try:
        for _ in range(warmups):
            for scenario in scenarios:
                if not no_memory:
                    seed = chatbot.remember(scenario["customer_id"], facts_by_customer[scenario["customer_id"]])
                    if seed.status != "success":
                        raise RuntimeError(f"warmup seed failed: {seed.error}")
                chatbot.respond(scenario["customer_id"], scenario["query"])
        for repetition in range(repetitions):
            for order, scenario in enumerate(scenarios):
                if not no_memory:
                    seed = chatbot.remember(scenario["customer_id"], facts_by_customer[scenario["customer_id"]])
                    if seed.status != "success":
                        observations.append(Observation(run_id, _now(), adapter_name, "e2e", "seed", scenario_id=scenario["id"], customer_id=scenario["customer_id"], repetition=repetition, execution_order=order, status="setup_failure", error_message=seed.error or "memory seed failed"))
                        continue
                started = perf_counter()
                try:
                    response = chatbot.respond(scenario["customer_id"], scenario["query"])
                    evaluation = evaluate_recommendations(response.recommended_product_ids, scenario)
                    status = response.status
                    observations.append(Observation(run_id, _now(), adapter_name, "e2e_no_memory" if no_memory else "e2e", "respond", scenario_id=scenario["id"], customer_id=scenario["customer_id"], repetition=repetition, execution_order=order, cold_or_warm="cold" if repetition == 0 and order == 0 else "warm", latency_ms=(perf_counter() - started) * 1000, adapter_latency_ms=response.recall_latency_ms, status=status, returned_count=len(response.recommended_product_ids), relevance_score=relevance_score(response.retrieved_memories, scenario["expected_memory"]), response_json=json.dumps(asdict(response), default=str), **contamination_report(response.retrieved_memories, run_id, scenario["customer_id"], known_namespaces, known_customers), **evaluation))
                except Exception as exc:
                    observations.append(_failed(run_id, adapter_name, "e2e_no_memory" if no_memory else "e2e", "respond", repetition, order, started, exc, scenario_id=scenario["id"], customer_id=scenario["customer_id"]))
    finally:
        adapter.close()
    return observations


def write_observations(run_id: str, observations: Sequence[Observation]) -> Path:
    output = RAW_ROOT / run_id
    output.mkdir(parents=True, exist_ok=False)
    path = output / "observations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Observation.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(item) for item in observations)
    return path


def write_summary(run_id: str, observations: Sequence[Observation]) -> Path:
    output = SUMMARY_ROOT / run_id
    output.mkdir(parents=True, exist_ok=False)
    grouped: dict[tuple[str, str], list[Observation]] = {}
    for observation in observations:
        grouped.setdefault((observation.adapter, observation.experiment_group), []).append(observation)
    metrics = {f"{adapter}:{group}": summarize_observations(items) for (adapter, group), items in grouped.items()}
    path = output / "metrics.json"
    path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return path


def load_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    customers = json.loads((ROOT / "data" / "customers.json").read_text(encoding="utf-8"))
    scenarios = json.loads((ROOT / "data" / "scenarios.json").read_text(encoding="utf-8"))
    return customers, scenarios


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or smoke-test the Retail Memory benchmark harness")
    parser.add_argument("--group", choices=("writes", "recall", "e2e", "no-memory", "all"), default="all")
    parser.add_argument("--adapter", choices=("cli", "mcp", "no_memory", "all"), default="all")
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--smoke", action="store_true", help="Run one deterministic no-network control workload")
    args = parser.parse_args()
    if args.repetitions < 1 or args.warmups < 0:
        parser.error("--repetitions must be positive and --warmups cannot be negative")
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    customers, scenarios = load_data()
    catalog = load_catalog()
    if args.smoke or args.group == "no-memory":
        adapters = ["no_memory"]
    else:
        adapters = [args.adapter] if args.adapter != "all" else ["cli", "mcp"]
    observations: list[Observation] = []
    for adapter_name in adapters:
        scoped_customers, scoped_scenarios = prepare_workload(run_id, adapter_name, customers, scenarios)
        if args.group in ("writes", "all") and adapter_name != "no_memory":
            observations.extend(run_memory_writes(run_id, adapter_name, scoped_customers[:1] if args.smoke else scoped_customers, 1 if args.smoke else args.repetitions, 0 if args.smoke else args.warmups))
        if args.group in ("recall", "all") and adapter_name != "no_memory":
            observations.extend(run_memory_recall(run_id, adapter_name, scoped_customers, scoped_scenarios[:1] if args.smoke else scoped_scenarios, 1 if args.smoke else args.repetitions, 0 if args.smoke else args.warmups))
        if args.group in ("e2e", "all"):
            observations.extend(run_scenarios(run_id, adapter_name, scoped_customers, scoped_scenarios[:1] if args.smoke else scoped_scenarios, catalog, 1 if args.smoke else args.repetitions, no_memory=adapter_name == "no_memory", warmups=0 if args.smoke else args.warmups))
        if args.group == "no-memory":
            observations.extend(run_scenarios(run_id, "no_memory", scoped_customers, scoped_scenarios[:1] if args.smoke else scoped_scenarios, catalog, 1 if args.smoke else args.repetitions, no_memory=True, warmups=0 if args.smoke else args.warmups))
    path = write_observations(run_id, observations)
    summary_path = write_summary(run_id, observations)
    print(f"wrote {len(observations)} observations to {path}")
    print(f"wrote summary metrics to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
