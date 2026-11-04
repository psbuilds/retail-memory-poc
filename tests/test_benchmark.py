from app.memory.base import MemoryAdapter
from scripts import benchmark
from scripts.benchmark import (
    Observation,
    NoMemoryAdapter,
    evaluate_recommendations,
    percentile,
    run_memory_recall,
    summarize_observations,
    contamination_report,
    namespace_customer_id,
    prepare_workload,
    write_observations,
)


class FakeBenchmarkAdapter(MemoryAdapter):
    integration = "fake"

    def __init__(self, fail_recall=False, fail_remember=False):
        self.fail_recall = fail_recall
        self.fail_remember = fail_remember
        self.closed = False

    def _remember(self, customer_id, facts, dataset):
        if self.fail_remember:
            raise ConnectionError("seed unavailable")
        return len(" ".join(facts))

    def _recall(self, customer_id, query, dataset):
        if self.fail_recall:
            raise ConnectionError("recall unavailable")
        return ("Prefers affordable wireless headphones.", "Budget below ₹3000.")

    def close(self):
        self.closed = True


def test_percentiles_and_empty_samples():
    assert percentile([], 95) is None
    assert percentile([10], 95) == 10
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_observation_summary_preserves_failures():
    items = [
        Observation("r", "t", "cli", "memory", "recall", latency_ms=10),
        Observation("r", "t", "cli", "memory", "recall", latency_ms=20, status="failure", error_type="X"),
    ]
    summary = summarize_observations(items)
    assert summary["total_operations"] == 2
    assert summary["successful_operations"] == 1
    assert summary["failed_operations"] == 1
    assert summary["success_rate"] == 0.5


def test_recommendation_evaluation():
    scenario = {"expected_product_ids": ["p001", "p004"], "excluded_product_ids": ["p002"]}
    result = evaluate_recommendations(["p001", "p002"], scenario)
    assert result == {"recommendation_match": 0.5, "expected_product_coverage": 0.5, "excluded_product_violations": 1}


def test_observation_schema_is_serializable():
    assert "adapter_latency_ms" in Observation.__dataclass_fields__


def test_recall_group_seeds_once_and_scores_relevance(monkeypatch):
    created = []

    def factory(_name):
        adapter = FakeBenchmarkAdapter()
        created.append(adapter)
        return lambda: adapter

    monkeypatch.setattr(benchmark, "adapter_factory", factory)
    customers = [{"customer_id": "customer_001", "facts": ["Prefers wireless headphones."]}]
    scenarios = [{"id": "scenario_01", "customer_id": "customer_001", "query": "headphones", "expected_memory": ["wireless", "absent"]}]
    observations = run_memory_recall("run", "cli", customers, scenarios, repetitions=2, warmups=1)

    assert len(observations) == 2
    assert all(item.experiment_group == "memory_recall" for item in observations)
    assert all(item.relevance_score == 0.5 for item in observations)
    assert observations[0].cold_or_warm == "warm"
    assert created[0].closed is True


def test_failed_recall_is_recorded(monkeypatch):
    monkeypatch.setattr(benchmark, "adapter_factory", lambda _name: lambda: FakeBenchmarkAdapter(fail_recall=True))
    customers = [{"customer_id": "customer_001", "facts": ["fact"]}]
    scenarios = [{"id": "scenario_01", "customer_id": "customer_001", "query": "headphones", "expected_memory": ["wireless"]}]
    observations = run_memory_recall("run", "mcp", customers, scenarios, repetitions=1)

    assert len(observations) == 1
    assert observations[0].status == "failure"
    assert observations[0].error_type == "MemoryOperationError"


def test_recall_seed_failure_is_recorded_without_measuring_recall(monkeypatch):
    monkeypatch.setattr(benchmark, "adapter_factory", lambda _name: lambda: FakeBenchmarkAdapter(fail_remember=True))
    customers = [{"customer_id": "customer_001", "facts": ["fact"]}]
    scenarios = [{"id": "scenario_01", "customer_id": "customer_001", "query": "headphones", "expected_memory": ["wireless"]}]
    observations = run_memory_recall("run", "cli", customers, scenarios, repetitions=1)

    assert len(observations) == 1
    assert observations[0].operation == "seed"
    assert observations[0].status == "setup_failure"


def test_no_memory_adapter_never_returns_memory():
    adapter = NoMemoryAdapter()
    assert adapter.recall("customer_001", "headphones").result_count == 0
    assert adapter.remember("customer_001", ["fact"]).fact_count == 1


def test_raw_run_directory_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark, "RAW_ROOT", tmp_path)
    item = Observation("run", "t", "cli", "memory_recall", "recall")
    write_observations("run", [item])
    try:
        write_observations("run", [item])
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing raw run directory was overwritten")


def test_run_scoped_namespaces_are_stable_and_adapter_specific():
    first = namespace_customer_id("run_a", "cli", "customer_001")
    assert first == namespace_customer_id("run_a", "cli", "customer_001")
    assert first != namespace_customer_id("run_b", "cli", "customer_001")
    assert first != namespace_customer_id("run_a", "mcp", "customer_001")


def test_prepare_workload_preserves_facts_and_scopes_scenarios():
    customers = [{"customer_id": "customer_001", "facts": ["Prefers wireless."]}]
    scenarios = [{"id": "s1", "customer_id": "customer_001", "query": "headphones"}]
    scoped_customers, scoped_scenarios = prepare_workload("run_a", "cli", customers, scenarios)
    assert scoped_customers[0]["customer_id"] == "phase10_run_a_cli_customer_001"
    assert "Prefers wireless." in scoped_customers[0]["facts"][0]
    assert scoped_scenarios[0]["customer_id"] == scoped_customers[0]["customer_id"]


def test_contamination_report_distinguishes_unexpected_provenance():
    clean = contamination_report(["[benchmark_namespace=run_a customer=phase10_run_a_cli_customer_001] fact"], "run_a", "phase10_run_a_cli_customer_001", [], [])
    dirty = contamination_report(["[benchmark_namespace=run_old customer=phase10_run_old_cli_customer_001] stale"], "run_a", "phase10_run_a_cli_customer_001", [], [])
    irrelevant = contamination_report(["unmarked unrelated memory"], "run_a", "phase10_run_a_cli_customer_001", [], [])
    assert clean["contamination_detected"] is False
    assert dirty["contamination_detected"] is True
    assert dirty["contamination_count"] == 2
    assert irrelevant["contamination_detected"] is False
