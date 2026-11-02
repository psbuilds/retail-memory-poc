"""Generate the deterministic synthetic retail dataset for the POC.

The three JSON files under ``data/`` are the single source of input for both the
CLI and the MCP integration. They are generated from the literals below, with no
randomness, so any run produces byte-identical output.

Usage:
    python scripts/generate_data.py           # write the data files
    python scripts/generate_data.py --check   # verify committed files are current
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRODUCTS_PATH = DATA_DIR / "products.json"
CUSTOMERS_PATH = DATA_DIR / "customers.json"
SCENARIOS_PATH = DATA_DIR / "scenarios.json"

SCENARIO_TYPES = {"budget", "feature", "negative", "mixed", "ambiguous"}

# All prices are in INR.
PRODUCTS: list[dict[str, Any]] = [
    {
        "id": "p001",
        "name": "Wireless Earbuds",
        "category": "headphones",
        "price": 2499,
        "features": ["wireless", "lightweight", "travel-friendly", "in-ear"],
    },
    {
        "id": "p002",
        "name": "Over-Ear Studio Headphones",
        "category": "headphones",
        "price": 8999,
        "features": ["over-ear", "noise-cancelling", "wired", "bulky"],
    },
    {
        "id": "p003",
        "name": "Budget Wired Earphones",
        "category": "headphones",
        "price": 799,
        "features": ["wired", "in-ear", "lightweight"],
    },
    {
        "id": "p004",
        "name": "Bluetooth Neckband",
        "category": "headphones",
        "price": 1999,
        "features": ["wireless", "neckband", "long-battery", "travel-friendly"],
    },
    {
        "id": "p005",
        "name": "Wireless Over-Ear ANC Headphones",
        "category": "headphones",
        "price": 7499,
        "features": ["wireless", "over-ear", "noise-cancelling", "call-friendly"],
    },
    {
        "id": "p006",
        "name": "Premium ANC Over-Ear Headset",
        "category": "headphones",
        "price": 11999,
        "features": ["wireless", "over-ear", "noise-cancelling", "call-friendly"],
    },
    {
        "id": "p007",
        "name": "Wired In-Ear Monitor",
        "category": "headphones",
        "price": 1499,
        "features": ["wired", "in-ear", "studio"],
    },
    {
        "id": "p008",
        "name": "Running Shoes",
        "category": "footwear",
        "price": 3499,
        "features": ["lightweight", "breathable", "running"],
    },
    {
        "id": "p009",
        "name": "Trail Hiking Shoes",
        "category": "footwear",
        "price": 5499,
        "features": ["durable", "waterproof", "hiking", "heavy"],
    },
    {
        "id": "p010",
        "name": "Everyday Sneakers",
        "category": "footwear",
        "price": 2199,
        "features": ["lightweight", "breathable", "casual"],
    },
    {
        "id": "p011",
        "name": "Heavy Waterproof Boots",
        "category": "footwear",
        "price": 6999,
        "features": ["waterproof", "hiking", "heavy", "bulky"],
    },
    {
        "id": "p012",
        "name": "Cushioned Walking Shoes",
        "category": "footwear",
        "price": 2999,
        "features": ["cushioned", "lightweight", "casual"],
    },
    {
        "id": "p013",
        "name": "Cotton Casual T-Shirt",
        "category": "apparel",
        "price": 899,
        "features": ["cotton", "casual", "regular-fit"],
    },
    {
        "id": "p014",
        "name": "Merino Travel Hoodie",
        "category": "apparel",
        "price": 4299,
        "features": ["merino", "travel-friendly", "warm", "lightweight", "packable"],
    },
    {
        "id": "p015",
        "name": "Synthetic Gym T-Shirt",
        "category": "apparel",
        "price": 749,
        "features": ["synthetic", "quick-dry", "athletic"],
    },
    {
        "id": "p016",
        "name": "Merino Base Layer",
        "category": "apparel",
        "price": 3899,
        "features": ["merino", "warm", "packable", "travel-friendly"],
    },
    {
        "id": "p017",
        "name": "Bulky Puffer Jacket",
        "category": "apparel",
        "price": 6499,
        "features": ["bulky", "waterproof", "warm", "heavy"],
    },
    {
        "id": "p018",
        "name": "Packable Travel Backpack",
        "category": "bags",
        "price": 2799,
        "features": ["packable", "lightweight", "travel-friendly"],
    },
    {
        "id": "p019",
        "name": "Leather Work Tote",
        "category": "bags",
        "price": 5599,
        "features": ["leather", "heavy", "bulky", "professional"],
    },
]

CUSTOMERS: list[dict[str, Any]] = [
    {
        "customer_id": "customer_001",
        "facts": [
            "Prefers affordable wireless headphones.",
            "Has a budget below ₹3000.",
            "Uses headphones mainly while travelling.",
            "Dislikes bulky over-ear designs.",
        ],
    },
    {
        "customer_id": "customer_002",
        "facts": [
            "Runs five times a week.",
            "Needs lightweight and breathable footwear.",
            "Budget is around ₹4000.",
            "Prefers to avoid heavy waterproof hiking shoes.",
        ],
    },
    {
        "customer_id": "customer_003",
        "facts": [
            "Buys warm layers for work travel.",
            "Prefers merino wool over synthetic fabrics.",
            "Wants packable clothing that fits in hand luggage.",
            "Budget is up to ₹5000.",
            "Avoids bulky waterproof gear.",
        ],
    },
    {
        "customer_id": "customer_004",
        "facts": [
            "Works from home and joins client calls all day.",
            "Prefers over-ear headphones with noise cancellation.",
            "Wants a wireless headset for calls.",
            "Budget is up to ₹8000.",
            "Dislikes in-ear designs.",
        ],
    },
    {
        "customer_id": "customer_005",
        "facts": [
            "Is a student shopping on a tight budget.",
            "Prefers plain cotton t-shirts.",
            "Wants casual clothing under ₹1200.",
            "Avoids synthetic fabrics.",
            "Does not want heavyweight clothing.",
        ],
    },
]

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "scenario_01",
        "customer_id": "customer_001",
        "query": "Can you recommend headphones for me?",
        "scenario_type": "budget",
        "expected_memory": ["affordable", "wireless", "3000"],
        "expected_product_ids": ["p001", "p004"],
        "excluded_product_ids": ["p002"],
        "expected_behavior": "Recommend wireless headphones below ₹3000 and avoid bulky over-ear models.",
    },
    {
        "id": "scenario_02",
        "customer_id": "customer_002",
        "query": "I need shoes for my morning runs.",
        "scenario_type": "feature",
        "expected_memory": ["lightweight", "breathable", "runs"],
        "expected_product_ids": ["p008"],
        "excluded_product_ids": ["p009", "p011"],
        "expected_behavior": "Recommend lightweight breathable running shoes, not heavy waterproof hiking shoes.",
    },
    {
        "id": "scenario_03",
        "customer_id": "customer_001",
        "query": "I need something for my daily commute.",
        "scenario_type": "ambiguous",
        "expected_memory": ["travelling", "wireless", "bulky"],
        "expected_product_ids": ["p001", "p004"],
        "excluded_product_ids": ["p002"],
        "expected_behavior": "Use travel context and the dislike of bulky designs to pick travel-friendly wireless headphones.",
    },
    {
        "id": "scenario_04",
        "customer_id": "customer_003",
        "query": "What should I pack for a work trip?",
        "scenario_type": "mixed",
        "expected_memory": ["merino", "packable", "travel", "5000"],
        "expected_product_ids": ["p014", "p016"],
        "excluded_product_ids": ["p017"],
        "expected_behavior": "Recommend packable merino layers within budget and avoid bulky waterproof gear.",
    },
    {
        "id": "scenario_05",
        "customer_id": "customer_002",
        "query": "Do you have any hiking shoes?",
        "scenario_type": "negative",
        "expected_memory": ["lightweight", "heavy", "waterproof"],
        "expected_product_ids": ["p010"],
        "excluded_product_ids": ["p009", "p011"],
        "expected_behavior": "Acknowledge the hiking request but steer away from heavy waterproof shoes given the lightweight preference.",
    },
    {
        "id": "scenario_06",
        "customer_id": "customer_004",
        "query": "I need headphones for my client calls at home.",
        "scenario_type": "feature",
        "expected_memory": ["over-ear", "noise", "calls"],
        "expected_product_ids": ["p005"],
        "excluded_product_ids": ["p002", "p006", "p007"],
        "expected_behavior": "Recommend wireless over-ear noise-cancelling headphones within ₹8000 and avoid in-ear designs.",
    },
    {
        "id": "scenario_07",
        "customer_id": "customer_004",
        "query": "Something that works for long work-from-home days.",
        "scenario_type": "mixed",
        "expected_memory": ["wireless", "budget", "8000", "in-ear"],
        "expected_product_ids": ["p005"],
        "excluded_product_ids": ["p006", "p007"],
        "expected_behavior": "Pick the wireless over-ear headset within the ₹8000 budget and avoid in-ear products.",
    },
    {
        "id": "scenario_08",
        "customer_id": "customer_005",
        "query": "I need something casual to wear to class.",
        "scenario_type": "budget",
        "expected_memory": ["cotton", "casual", "1200"],
        "expected_product_ids": ["p013"],
        "excluded_product_ids": ["p015", "p017"],
        "expected_behavior": "Recommend the cotton t-shirt under ₹1200 and avoid synthetic or heavyweight clothing.",
    },
    {
        "id": "scenario_09",
        "customer_id": "customer_005",
        "query": "Can you add a t-shirt to my order?",
        "scenario_type": "budget",
        "expected_memory": ["cotton", "synthetic", "1200"],
        "expected_product_ids": ["p013"],
        "excluded_product_ids": ["p015"],
        "expected_behavior": "Choose the cotton t-shirt over the synthetic one and stay within the ₹1200 budget.",
    },
    {
        "id": "scenario_10",
        "customer_id": "customer_003",
        "query": "I am going somewhere cold next week.",
        "scenario_type": "ambiguous",
        "expected_memory": ["warm", "merino", "packable"],
        "expected_product_ids": ["p014", "p016"],
        "excluded_product_ids": ["p017"],
        "expected_behavior": "Recommend warm packable merino layers rather than the bulky waterproof jacket.",
    },
]


def validate(
    products: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
) -> list[str]:
    """Return a list of problems; empty means the dataset is consistent."""
    errors: list[str] = []

    product_ids = [p["id"] for p in products]
    if len(product_ids) != len(set(product_ids)):
        errors.append("duplicate product ids")
    for product in products:
        if not product["features"]:
            errors.append(f"{product['id']}: no features")
        if product["price"] <= 0:
            errors.append(f"{product['id']}: non-positive price")

    customer_ids = [c["customer_id"] for c in customers]
    if len(customer_ids) != len(set(customer_ids)):
        errors.append("duplicate customer ids")
    for customer in customers:
        if not 3 <= len(customer["facts"]) <= 5:
            errors.append(f"{customer['customer_id']}: expected 3-5 facts")

    known_products = set(product_ids)
    facts_by_customer = {c["customer_id"]: " ".join(c["facts"]).lower() for c in customers}

    for scenario in scenarios:
        sid = scenario["id"]
        customer_id = scenario["customer_id"]
        if customer_id not in facts_by_customer:
            errors.append(f"{sid}: unknown customer {customer_id}")
            continue
        if scenario["scenario_type"] not in SCENARIO_TYPES:
            errors.append(f"{sid}: unknown scenario_type {scenario['scenario_type']}")

        expected = scenario["expected_product_ids"]
        excluded = scenario["excluded_product_ids"]
        if not expected:
            errors.append(f"{sid}: no expected products")
        for pid in expected + excluded:
            if pid not in known_products:
                errors.append(f"{sid}: unknown product {pid}")
        if set(expected) & set(excluded):
            errors.append(f"{sid}: product both expected and excluded")

        if not scenario["expected_memory"]:
            errors.append(f"{sid}: no expected_memory terms")
        for term in scenario["expected_memory"]:
            # relevance_score relies on substring matching against recalled text,
            # which is derived from the customer's facts.
            if term.lower() not in facts_by_customer[customer_id]:
                errors.append(f"{sid}: memory term {term!r} not grounded in {customer_id} facts")

    scenario_ids = [s["id"] for s in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("duplicate scenario ids")

    return errors


def serialize(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def build() -> dict[Path, str]:
    return {
        PRODUCTS_PATH: serialize(PRODUCTS),
        CUSTOMERS_PATH: serialize(CUSTOMERS),
        SCENARIOS_PATH: serialize(SCENARIOS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed data files match this generator instead of writing",
    )
    args = parser.parse_args(argv)

    errors = validate(PRODUCTS, CUSTOMERS, SCENARIOS)
    if errors:
        for error in errors:
            print(f"validation error: {error}", file=sys.stderr)
        return 1

    files = build()
    if args.check:
        stale = [str(path) for path, content in files.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
        if stale:
            for path in stale:
                print(f"out of date: {path}", file=sys.stderr)
            return 1
        print("data files are up to date")
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
