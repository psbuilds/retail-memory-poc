import json
import math

import pytest

from app.products import (
    Product,
    filter_by_brand,
    filter_by_category,
    filter_by_max_budget,
    filter_by_noise_cancellation,
    filter_by_required_features,
    filter_by_wireless,
    load_catalog,
    recommend_products,
    validate_catalog,
)


@pytest.fixture
def catalog() -> tuple[Product, ...]:
    return load_catalog()


def test_original_catalog_shape_and_id_meanings(catalog):
    assert len(catalog) == 19
    assert {p.category for p in catalog} == {"headphones", "footwear", "apparel", "bags"}
    assert {p.category: sum(x.category == p.category for x in catalog) for p in catalog} == {
        "headphones": 7, "footwear": 5, "apparel": 5, "bags": 2,
    }
    by_id = {p.product_id: p for p in catalog}
    assert by_id["p008"].name == "Running Shoes"
    assert by_id["p010"].name == "Everyday Sneakers"
    assert by_id["p013"].name == "Cotton Casual T-Shirt"
    assert by_id["p014"].name == "Merino Travel Hoodie"
    assert by_id["p016"].name == "Merino Base Layer"
    assert by_id["p005"].noise_cancellation is True


def test_filters_normalize_values_and_budget_boundary(catalog):
    assert {p.product_id for p in filter_by_category(catalog, " FOOTWEAR ")} == {"p008", "p009", "p010", "p011", "p012"}
    assert all(p.price <= 2999 for p in filter_by_max_budget(catalog, 2999))
    assert any(p.price == 2999 for p in filter_by_max_budget(catalog, 2999))
    assert all(p.wireless for p in filter_by_wireless(catalog, True))
    assert {p.product_id for p in filter_by_brand(catalog, " wooltrail ")} == {"p014", "p016"}
    assert all({"merino", "packable"} <= {f.casefold() for f in p.features} for p in filter_by_required_features(catalog, [" MERINO ", "PACKABLE"]))
    assert all(p.noise_cancellation for p in filter_by_noise_cancellation(catalog, True))


def test_recommendation_applies_hard_constraints_and_exclusions(catalog):
    result = recommend_products(catalog, {
        "category": "headphones", "max_budget": 8000, "wireless": True,
        "noise_cancellation": True, "excluded_features": ["over-ear"],
        "excluded_product_ids": ["p005"],
    })
    assert result == []
    assert recommend_products(catalog, {"category": "apparel", "excluded_features": ["synthetic", "bulky"]})
    assert all("synthetic" not in {f.casefold() for f in p.features} for p in recommend_products(catalog, {"category": "apparel", "excluded_features": ["synthetic"]}))


def test_deterministic_ranking_and_limits(catalog):
    first = recommend_products(catalog, {"category": "headphones"}, limit=7)
    second = recommend_products(catalog, {"category": "headphones"}, limit=7)
    assert first == second
    assert [p.product_id for p in first] == ["p006", "p005", "p002", "p007", "p001", "p004", "p003"]
    assert recommend_products(catalog, {"max_budget": 0}) == []
    assert recommend_products(catalog, {"max_budget": 1}, limit=0) == []


def test_no_fallback_and_rating_cannot_override_budget(catalog):
    result = recommend_products(catalog, {"category": "headphones", "max_budget": 1000})
    assert {p.product_id for p in result} == {"p003"}
    assert recommend_products(catalog, {"required_features": ["does-not-exist"]}) == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_invalid_prices_are_rejected(value):
    record = {"product_id":"x", "name":"X", "category":"bags", "brand":"B", "price":value, "features":["x"], "wireless":False, "noise_cancellation":False, "rating":3}
    with pytest.raises(ValueError, match="price"):
        validate_catalog([record])


def test_schema_duplicates_and_invalid_preferences(catalog, tmp_path):
    record = {"product_id":"x", "name":"X", "category":"bags", "brand":"B", "price":1, "features":["x"], "wireless":False, "noise_cancellation":False, "rating":3}
    with pytest.raises(ValueError, match="duplicate"):
        validate_catalog([record, dict(record)])
    with pytest.raises(ValueError, match="missing"):
        validate_catalog([{"product_id": "x"}])
    with pytest.raises(TypeError):
        recommend_products(catalog, {"wireless": "yes"})
    with pytest.raises(TypeError):
        recommend_products(catalog, {"excluded_features": "bulky"})
    with pytest.raises(ValueError):
        recommend_products(catalog, {"max_budget": -1})
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    assert load_catalog(path) == ()


def test_cross_file_scenarios_are_semantically_compatible(catalog):
    products = {p.product_id: p for p in catalog}
    scenarios = json.loads(open("data/scenarios.json", encoding="utf-8").read())
    assert len(scenarios) == 10
    categories = {
        "scenario_01": "headphones", "scenario_02": "footwear", "scenario_03": "headphones",
        "scenario_04": "apparel", "scenario_05": "footwear", "scenario_06": "headphones",
        "scenario_07": "headphones", "scenario_08": "apparel", "scenario_09": "apparel", "scenario_10": "apparel",
    }
    for scenario in scenarios:
        expected = set(scenario["expected_product_ids"])
        excluded = set(scenario["excluded_product_ids"])
        assert expected <= products.keys()
        assert excluded <= products.keys()
        assert expected.isdisjoint(excluded)
        assert {products[product_id].category for product_id in expected} == {categories[scenario["id"]]}
        assert {products[product_id].category for product_id in excluded} == {categories[scenario["id"]]}


def test_customer_preferences_have_structured_representations(catalog):
    customers = json.loads(open("data/customers.json", encoding="utf-8").read())
    representations = {
        "customer_001": {"category": "headphones", "max_budget": 3000, "wireless": True, "excluded_features": ["bulky"]},
        "customer_002": {"category": "footwear", "max_budget": 4000, "required_features": ["lightweight", "breathable"], "excluded_features": ["heavy", "waterproof"]},
        "customer_003": {"category": "apparel", "max_budget": 5000, "required_features": ["merino", "packable"], "excluded_features": ["bulky", "synthetic"]},
        "customer_004": {"category": "headphones", "max_budget": 8000, "wireless": True, "noise_cancellation": True, "required_features": ["over-ear", "call-friendly"], "excluded_features": ["in-ear"]},
        "customer_005": {"category": "apparel", "max_budget": 1200, "required_features": ["cotton"], "excluded_features": ["synthetic", "heavyweight"]},
    }
    assert {customer["customer_id"] for customer in customers} == set(representations)
    for customer_id, preferences in representations.items():
        results = recommend_products(catalog, preferences)
        assert all(product.category == preferences["category"] for product in results)
        assert all(product.price <= preferences["max_budget"] for product in results)
        assert all(not ({f.casefold() for f in product.features} & set(preferences["excluded_features"])) for product in results)


def test_catalog_records_are_not_mutated(catalog):
    original = tuple(catalog)
    recommend_products(catalog, {"category": "footwear", "excluded_features": ["heavy"]})
    assert catalog == original
