"""Validated catalog and deterministic, structured product recommendations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "products.json"
RATING_MIN = 0.0
RATING_MAX = 5.0


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    name: str
    category: str
    brand: str
    price: int | float
    features: tuple[str, ...]
    wireless: bool
    noise_cancellation: bool
    rating: float


class Preferences(TypedDict, total=False):
    category: str | None
    max_budget: int | float | None
    wireless: bool | None
    preferred_brand: str | None
    required_features: Sequence[str]
    noise_cancellation: bool | None
    excluded_features: Sequence[str]
    excluded_product_ids: Sequence[str]


def _text(value: Any, field: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"product {index} field {field!r} must be non-empty text")
    return value.strip()


def _validate_record(record: Mapping[str, Any], index: int) -> Product:
    required = {"product_id", "name", "category", "brand", "price", "features", "wireless", "noise_cancellation", "rating"}
    missing = required - record.keys()
    if missing:
        raise ValueError(f"product {index} is missing required fields: {sorted(missing)}")
    price = record["price"]
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise ValueError(f"product {index} field 'price' must be a finite positive number")
    rating = record["rating"]
    if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not math.isfinite(rating) or not RATING_MIN <= rating <= RATING_MAX:
        raise ValueError(f"product {index} field 'rating' must be finite and between {RATING_MIN} and {RATING_MAX}")
    for field in ("wireless", "noise_cancellation"):
        if not isinstance(record[field], bool):
            raise ValueError(f"product {index} field {field!r} must be a boolean")
    features = record["features"]
    if not isinstance(features, list) or not features or not all(isinstance(item, str) and item.strip() for item in features):
        raise ValueError(f"product {index} field 'features' must be a non-empty list of strings")
    return Product(
        _text(record["product_id"], "product_id", index),
        _text(record["name"], "name", index),
        _text(record["category"], "category", index).casefold(),
        _text(record["brand"], "brand", index),
        price,
        tuple(item.strip() for item in features),
        record["wireless"],
        record["noise_cancellation"],
        float(rating),
    )


def validate_catalog(records: Sequence[Mapping[str, Any]]) -> tuple[Product, ...]:
    """Validate records and return immutable products without mutating inputs."""
    products = tuple(_validate_record(record, index) for index, record in enumerate(records))
    ids = [product.product_id for product in products]
    if len(ids) != len(set(ids)):
        raise ValueError("product catalog contains duplicate product_id values")
    return products


def load_catalog(path: str | Path = CATALOG_PATH) -> tuple[Product, ...]:
    """Load and validate the JSON product catalog."""
    catalog_path = Path(path)
    try:
        with catalog_path.open(encoding="utf-8") as handle:
            records = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load product catalog {catalog_path}: {exc}") from exc
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("product catalog must be a JSON list of objects")
    return validate_catalog(records)


def filter_by_category(products: Sequence[Product], category: str | None) -> list[Product]:
    normalized = _optional_text(category, "category")
    return [product for product in products if normalized is None or product.category == normalized.casefold()]


def filter_by_max_budget(products: Sequence[Product], max_budget: int | float | None) -> list[Product]:
    _validate_budget(max_budget)
    return [product for product in products if max_budget is None or product.price <= max_budget]


def filter_by_wireless(products: Sequence[Product], wireless: bool | None) -> list[Product]:
    if wireless is not None and not isinstance(wireless, bool):
        raise TypeError("wireless preference must be a boolean or None")
    return [product for product in products if wireless is None or product.wireless == wireless]


def filter_by_noise_cancellation(products: Sequence[Product], required: bool | None) -> list[Product]:
    if required is not None and not isinstance(required, bool):
        raise TypeError("noise_cancellation preference must be a boolean or None")
    return [product for product in products if required is None or product.noise_cancellation == required]


def filter_by_brand(products: Sequence[Product], brand: str | None) -> list[Product]:
    normalized = _optional_text(brand, "preferred_brand")
    return [product for product in products if normalized is None or product.brand.casefold() == normalized.casefold()]


def filter_by_required_features(products: Sequence[Product], features: Sequence[str] | None) -> list[Product]:
    required = _normalise_features(features, "required_features")
    return [product for product in products if required <= _feature_set(product)]


def filter_by_excluded_features(products: Sequence[Product], features: Sequence[str] | None) -> list[Product]:
    excluded = _normalise_features(features, "excluded_features")
    return [product for product in products if not (_feature_set(product) & excluded)]


def filter_by_excluded_product_ids(products: Sequence[Product], product_ids: Sequence[str] | None) -> list[Product]:
    excluded = _normalise_features(product_ids, "excluded_product_ids")
    return [product for product in products if product.product_id.casefold() not in excluded]


def recommend_products(products: Sequence[Product], preferences: Mapping[str, Any] | None = None, *, limit: int = 3) -> list[Product]:
    """Apply hard structured constraints, then rank by rating, price, and ID."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    prefs = {} if preferences is None else dict(preferences)
    allowed = {"category", "max_budget", "wireless", "preferred_brand", "required_features", "noise_cancellation", "excluded_features", "excluded_product_ids"}
    unknown = set(prefs) - allowed
    if unknown:
        raise ValueError(f"unknown preference fields: {sorted(unknown)}")
    selected = filter_by_category(products, prefs.get("category"))
    selected = filter_by_max_budget(selected, prefs.get("max_budget"))
    selected = filter_by_brand(selected, prefs.get("preferred_brand"))
    selected = filter_by_wireless(selected, prefs.get("wireless"))
    selected = filter_by_noise_cancellation(selected, prefs.get("noise_cancellation"))
    selected = filter_by_required_features(selected, prefs.get("required_features"))
    selected = filter_by_excluded_features(selected, prefs.get("excluded_features"))
    selected = filter_by_excluded_product_ids(selected, prefs.get("excluded_product_ids"))
    return sorted(selected, key=lambda product: (-product.rating, product.price, product.product_id))[:limit]


def _validate_budget(budget: int | float | None) -> None:
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget)):
        raise TypeError("max_budget must be a finite number or None")
    if budget is not None and budget < 0:
        raise ValueError("max_budget cannot be negative")


def _optional_text(value: str | None, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} must be a string or None")
    return value.strip() if value is not None and value.strip() else None


def _normalise_features(values: Sequence[str] | None, field: str) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str) or not isinstance(values, Sequence) or not all(isinstance(value, str) for value in values):
        raise TypeError(f"{field} must be a sequence of strings or None")
    return {value.strip().casefold() for value in values if value.strip()}


def _feature_set(product: Product) -> set[str]:
    return {feature.casefold() for feature in product.features}
