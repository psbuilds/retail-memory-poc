from dataclasses import dataclass

import pytest

from app.chatbot import RetailChatbot, extract_preferences, merge_preferences
from app.memory.base import MemoryAdapter, MemoryOperationError, RecallResult, RememberResult
from app.products import load_catalog


class FakeAdapter(MemoryAdapter):
    integration = "fake"

    def __init__(self, fragments=(), failure=None):
        self.fragments = tuple(fragments)
        self.failure = failure
        self.remember_calls = []
        self.recall_calls = []

    def _remember(self, customer_id, facts, dataset):
        self.remember_calls.append((customer_id, facts))
        if self.failure:
            raise self.failure
        return 12

    def _recall(self, customer_id, query, dataset):
        self.recall_calls.append((customer_id, query))
        if self.failure:
            raise self.failure
        return self.fragments


def test_extraction_supports_structured_positive_and_negative_preferences():
    preferences = extract_preferences("Show wireless headphones with ANC under ₹3000, not bulky")
    assert preferences == {
        "category": "headphones", "max_budget": 3000, "wireless": True,
        "noise_cancellation": True, "required_features": ("wireless",),
        "excluded_features": ("bulky",),
    }
    assert extract_preferences("I don't want leather products") == {"excluded_features": ("leather",)}
    assert extract_preferences("I do not want wireless headphones")["wireless"] is False
    assert extract_preferences("I don't want wired headphones")["wireless"] is True
    assert extract_preferences("I don't need ANC headphones")["noise_cancellation"] is False
    assert extract_preferences("Avoid noise cancellation")["noise_cancellation"] is False
    assert extract_preferences("I would rather avoid wireless headphones")["wireless"] is False
    assert "wireless" not in extract_preferences("Wireless is not important to me")
    assert extract_preferences("I want headphones without Bluetooth")["wireless"] is False
    assert "noise_cancellation" not in extract_preferences("I don't mind having ANC")
    assert "noise_cancellation" not in extract_preferences("Noise cancellation is not a priority")
    assert "noise_cancellation" not in extract_preferences("I want ANC, although I can do without it")


def test_query_overrides_memory_and_memory_enriches_query():
    assert merge_preferences({"wireless": True, "required_features": ("lightweight",)}, {"wireless": False, "max_budget": 3000}) == {
        "wireless": False, "required_features": ("lightweight",), "max_budget": 3000,
    }
    assert "leather" not in merge_preferences({"excluded_features": ("leather",)}, {"required_features": ("leather",)}).get("excluded_features", ())
    assert "leather" not in merge_preferences({"required_features": ("leather",)}, {"excluded_features": ("leather",)}).get("required_features", ())
    assert extract_preferences("I want wireless headphones, actually make that wired")["wireless"] is False
    assert extract_preferences("Show wired headphones, but wireless is also fine").get("wireless") is False
    assert extract_preferences("Avoid leather. Actually, leather is fine.").get("excluded_features", ()) == ()
    assert extract_preferences("I want leather, not synthetic material")["required_features"] == ("leather",)


def test_remember_is_one_unchanged_adapter_call():
    adapter = FakeAdapter()
    response = RetailChatbot(adapter, load_catalog()).remember("customer_001", ("Prefers wireless.",))
    assert response.status == "success"
    assert adapter.remember_calls == [("customer_001", ("Prefers wireless.",))]
    assert response.facts_count == 1 and response.latency_ms >= 0


def test_respond_recalls_once_and_returns_recommendations():
    adapter = FakeAdapter(("Customer prefers wireless headphones.", "Budget below ₹3000."))
    response = RetailChatbot(adapter, load_catalog()).respond("customer_001", "Recommend wireless headphones")
    assert adapter.recall_calls == [("customer_001", "Recommend wireless headphones")]
    assert response.status == "success"
    assert len(response.recommended_products) == 2
    assert response.recommended_product_ids == tuple(p.product_id for p in response.recommended_products)
    assert response.total_latency_ms >= response.recall_latency_ms


def test_irrelevant_memory_does_not_constrain_category():
    adapter = FakeAdapter(("Customer prefers wireless headphones.", "Customer likes lightweight breathable footwear."))
    response = RetailChatbot(adapter, load_catalog()).respond("customer_002", "Show running shoes under 4000")
    assert response.final_preferences["category"] == "footwear"
    assert all(product.category == "footwear" for product in response.recommended_products)


def test_generic_wireless_memory_does_not_constrain_footwear():
    response = RetailChatbot(FakeAdapter(("Customer prefers wireless products.",)), load_catalog()).respond("customer_002", "Show running shoes")
    assert response.status == "success"
    assert response.recommended_product_ids == ("p008",)


def test_recommendation_failure_is_structured(monkeypatch):
    adapter = FakeAdapter()
    chatbot = RetailChatbot(adapter, load_catalog())
    monkeypatch.setattr("app.chatbot.recommend_products", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad preferences")))
    response = chatbot.respond("customer_001", "wireless headphones")
    assert response.status == "recommendation_failure"
    assert response.recommended_products == ()


def test_empty_memory_uses_query_only_and_empty_result_is_distinct():
    response = RetailChatbot(FakeAdapter(), load_catalog()).respond("customer_001", "Show headphones with merino")
    assert response.status == "no_recommendations"
    assert response.retrieved_memories == ()


def test_memory_failure_does_not_fallback_to_query_recommendations():
    error = MemoryOperationError("offline", integration="fake", operation="recall", customer_id="customer_001")
    response = RetailChatbot(FakeAdapter(failure=error), load_catalog()).respond("customer_001", "wireless headphones")
    assert response.status == "memory_failure"
    assert response.recommended_products == ()
    assert response.error == str(error)


def test_invalid_inputs_and_remember_failure():
    chatbot = RetailChatbot(FakeAdapter(), load_catalog())
    assert chatbot.respond("", "query").status == "invalid_input"
    assert chatbot.remember("customer_001", []).status == "invalid_input"
    error = MemoryOperationError("offline", integration="fake", operation="remember", customer_id="customer_001")
    assert RetailChatbot(FakeAdapter(failure=error), load_catalog()).remember("customer_001", ["fact"]).status == "memory_failure"


def test_adapter_type_is_not_used_by_chatbot():
    source = __import__("inspect").getsource(RetailChatbot)
    assert "CliMemoryAdapter" not in source
    assert "McpMemoryAdapter" not in source
    assert "scenario" not in source.lower()
