"""Deterministic retail chatbot application layer."""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Sequence

from app.memory.base import MemoryAdapter, MemoryAdapterError, RecallResult, RememberResult
from app.products import Preferences, Product, recommend_products

_CATEGORY_TERMS = {
    "headphones": ("headphone", "headphones", "earbuds", "earphones", "headset", "neckband"),
    "footwear": ("shoe", "shoes", "sneaker", "sneakers", "boots", "footwear"),
    "apparel": ("shirt", "t-shirt", "hoodie", "jacket", "clothing", "clothes", "layer"),
    "bags": ("bag", "backpack", "tote"),
}
_FEATURES = (
    "wireless", "over-ear", "in-ear", "lightweight", "breathable", "running",
    "noise-cancelling", "call-friendly", "merino", "packable", "warm", "cotton",
    "synthetic", "bulky", "heavy", "waterproof", "leather", "travel-friendly",
    "casual", "studio", "long-battery", "premium",
)
_CATEGORY_FEATURES = {
    "headphones": {"wireless", "over-ear", "in-ear", "noise-cancelling", "call-friendly", "long-battery", "studio", "travel-friendly", "lightweight", "bulky"},
    "footwear": {"lightweight", "breathable", "running", "waterproof", "heavy", "bulky", "casual", "cushioned"},
    "apparel": {"merino", "travel-friendly", "warm", "lightweight", "packable", "cotton", "synthetic", "bulky", "heavy", "waterproof", "casual"},
    "bags": {"packable", "lightweight", "travel-friendly", "leather", "heavy", "bulky", "professional"},
}
_BRANDS = ("SoundGo", "Auralite", "MetroBeat", "Bassline", "EchoPeak", "Northstar", "VelvetSound", "StridePro", "TrailPeak", "CottonWay", "WoolTrail", "ActiveFlex", "CarryLite")
_CORRECTIONS = ("but", "actually", "instead", "rather", "however")


@dataclass(frozen=True, slots=True)
class PreferenceMention:
    """An internal, position-aware interpretation of one preference phrase."""

    attribute: str
    value: Any
    start: int
    end: int
    polarity: str
    strength: str


_FEATURE_ALIASES = {
    "noise cancellation": "noise-cancelling",
    "noise cancelling": "noise-cancelling",
    "active noise cancellation": "noise-cancelling",
    "bluetooth": "wireless",
}


@dataclass(frozen=True, slots=True)
class RememberResponse:
    customer_id: str
    facts_count: int
    latency_ms: float
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    customer_id: str
    query: str
    retrieved_memories: tuple[str, ...]
    extracted_query_preferences: dict[str, Any]
    extracted_memory_preferences: dict[str, Any]
    final_preferences: dict[str, Any]
    recommended_products: tuple[Product, ...]
    recommended_product_ids: tuple[str, ...]
    recall_latency_ms: float
    preference_extraction_latency_ms: float
    recommendation_latency_ms: float
    total_latency_ms: float
    status: str
    error: str | None = None


class RetailChatbot:
    """Use an injected MemoryAdapter and shared product engine."""

    def __init__(self, memory_adapter: MemoryAdapter, product_catalog: Sequence[Product], top_k: int = 3) -> None:
        if not isinstance(memory_adapter, MemoryAdapter):
            raise TypeError("memory_adapter must implement MemoryAdapter")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 0:
            raise ValueError("top_k must be a non-negative integer")
        self.memory_adapter = memory_adapter
        self.product_catalog = tuple(product_catalog)
        self.top_k = top_k

    def remember(self, customer_id: str, facts: Sequence[str]) -> RememberResponse:
        started = perf_counter()
        try:
            result = self.memory_adapter.remember(customer_id, facts)
        except (ValueError, MemoryAdapterError) as exc:
            return RememberResponse(customer_id, 0, (perf_counter() - started) * 1000, "invalid_input" if isinstance(exc, ValueError) else "memory_failure", str(exc))
        return RememberResponse(result.customer_id, result.fact_count, result.latency_ms, "success")

    def respond(self, customer_id: str, query: str) -> ChatResponse:
        total_started = perf_counter()
        try:
            recall = self.memory_adapter.recall(customer_id, query)
        except (ValueError, MemoryAdapterError) as exc:
            clean_customer = customer_id if isinstance(customer_id, str) else ""
            clean_query = query if isinstance(query, str) else ""
            return _empty_response(clean_customer, clean_query, total_started, "invalid_input" if isinstance(exc, ValueError) else "memory_failure", str(exc))
        extraction_started = perf_counter()
        query_preferences = extract_preferences(query)
        relevant_memories = relevant_memory_fragments(recall.fragments, query_preferences)
        memory_preferences = extract_preferences("\n".join(relevant_memories))
        final_preferences = merge_preferences(memory_preferences, query_preferences)
        extraction_latency = (perf_counter() - extraction_started) * 1000
        recommendation_started = perf_counter()
        try:
            products = tuple(recommend_products(self.product_catalog, final_preferences, limit=self.top_k))
        except (ValueError, TypeError) as exc:
            return _empty_response(recall.customer_id, recall.query, total_started, "recommendation_failure", str(exc), recall.latency_ms, extraction_latency)
        recommendation_latency = (perf_counter() - recommendation_started) * 1000
        return ChatResponse(
            recall.customer_id, recall.query, tuple(relevant_memories), query_preferences,
            memory_preferences, final_preferences, products,
            tuple(product.product_id for product in products), recall.latency_ms,
            extraction_latency, recommendation_latency, (perf_counter() - total_started) * 1000,
            "success" if products else "no_recommendations",
        )


def extract_preferences(text: str) -> dict[str, Any]:
    """Extract supported hard preferences with local, position-aware polarity."""
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    lowered = text.casefold()
    result: dict[str, Any] = {}
    mentions: list[PreferenceMention] = []
    for category, terms in _CATEGORY_TERMS.items():
        for term in terms:
            match = re.search(rf"\b{re.escape(term)}\b", lowered)
            if match:
                mentions.append(PreferenceMention("category", category, match.start(), match.end(), "positive", "hard"))
    budget = re.search(r"(?:under|below|within|up to|max(?:imum)?|budget(?: is)?(?: up to)?)\s*[₹$]?\s*([\d,]+)", lowered)
    if budget:
        mentions.append(PreferenceMention("max_budget", int(budget.group(1).replace(",", "")), budget.start(), budget.end(), "positive", "hard"))
    for match in re.finditer(r"\b(wireless|wired|bluetooth)\b", lowered):
        alias = match.group(1)
        value = alias != "wired"
        polarity, strength = _polarity(lowered, match.start(), match.end())
        if polarity == "negative": value = not value
        if polarity != "neutral" and strength != "soft":
            mentions.append(PreferenceMention("wireless", value, match.start(), match.end(), polarity, strength))
    for match in re.finditer(r"\b(anc|active noise cancellation|noise cancellation|noise cancelling)\b", lowered):
        polarity, strength = _polarity(lowered, match.start(), match.end())
        if polarity != "neutral" and strength != "soft":
            mentions.append(PreferenceMention("noise_cancellation", polarity != "negative", match.start(), match.end(), polarity, strength))
    for brand in _BRANDS:
        if brand.casefold() in lowered:
            mentions.append(PreferenceMention("preferred_brand", brand, lowered.index(brand.casefold()), lowered.index(brand.casefold()) + len(brand), "positive", "hard"))

    for feature in _FEATURES:
        for match in re.finditer(rf"\b{re.escape(feature)}\b", lowered):
            polarity, strength = _polarity(lowered, match.start(), match.end())
            if polarity != "neutral" or strength == "soft":
                mentions.append(PreferenceMention("feature", feature, match.start(), match.end(), polarity, strength))
    if re.search(r"\balthough\b.*\b(?:can do without|without it)\b", lowered):
        mentions = [mention for mention in mentions if mention.attribute != "noise_cancellation"]
    mentions.sort(key=lambda mention: (mention.end, mention.start))
    for mention in mentions:
        if mention.attribute == "category": result["category"] = mention.value
        elif mention.attribute == "max_budget": result["max_budget"] = mention.value
        elif mention.attribute == "preferred_brand": result["preferred_brand"] = mention.value
        elif mention.attribute == "wireless": result["wireless"] = mention.value
        elif mention.attribute == "noise_cancellation": result["noise_cancellation"] = mention.value
        elif mention.attribute == "feature":
            required = list(result.get("required_features", ()))
            excluded = list(result.get("excluded_features", ()))
            if mention.polarity == "neutral":
                required = [value for value in required if value != mention.value]
                excluded = [value for value in excluded if value != mention.value]
            elif mention.polarity == "negative":
                required = [value for value in required if value != mention.value]
                if mention.value not in excluded: excluded.append(mention.value)
            else:
                excluded = [value for value in excluded if value != mention.value]
                if mention.value not in required: required.append(mention.value)
            if required: result["required_features"] = tuple(required)
            else: result.pop("required_features", None)
            if excluded: result["excluded_features"] = tuple(excluded)
            else: result.pop("excluded_features", None)
    if result.get("wireless") is True:
        result["excluded_features"] = tuple(value for value in result.get("excluded_features", ()) if value != "wireless")
    elif result.get("wireless") is False:
        result["required_features"] = tuple(value for value in result.get("required_features", ()) if value != "wireless")
        result["excluded_features"] = tuple(dict.fromkeys((*result.get("excluded_features", ()), "wireless")))
    if not result.get("required_features"): result.pop("required_features", None)
    if not result.get("excluded_features"): result.pop("excluded_features", None)
    return result


def _polarity(text: str, start: int, end: int) -> tuple[str, str]:
    """Interpret the short clause immediately before a mention."""
    clause_start = max(text.rfind(marker, 0, start) for marker in (".", ",", ";", " but ", " actually ", " instead ", " however ")) + 1
    prefix = text[clause_start:start].strip()
    clause_end = len(text)
    for marker in (".", ",", ";", " but ", " actually ", " instead ", " however ", " although "):
        position = text.find(marker, end)
        if position >= 0: clause_end = min(clause_end, position)
    suffix = text[end:clause_end].strip()
    context = f"{prefix} {suffix}".strip()
    if re.search(r"\b(?:don't|dont)\s+dislike\b", context):
        return "positive", "soft"
    if re.search(r"\b(?:acceptable|optional|don't mind|not important|not a priority|fine|fine with|can do without|without it)\b", context):
        return "neutral", "soft"
    if re.search(r"\b(?:not|avoid|avoids|without|unnecessary|not looking for|not want|do not want|don't want|dont want|would rather avoid|not need|don't need|dont need|dislike|dislikes|not a priority)\b", context):
        return "negative", "hard"
    if re.search(r"\b(?:actually|instead|rather)\b", prefix):
        return "positive", "hard"
    if re.search(r"\b(?:must have|require|requires|need|needs|want|wants|with|prefer|preferred)\b", context):
        return "positive", "hard" if re.search(r"\b(?:must|required|need|needs|want|wants|with)\b", context) else "soft"
    return "positive", "hard"


def relevant_memory_fragments(fragments: Sequence[str], query_preferences: Mapping[str, Any]) -> tuple[str, ...]:
    category = query_preferences.get("category")
    if not category:
        signals = {candidate for candidate, terms in _CATEGORY_TERMS.items() if any(term in " ".join(fragments).casefold() for term in terms)}
        if len(signals) != 1:
            return tuple(fragment for fragment in fragments if not _category_signal(fragment))
        category = signals.pop()
    terms = _CATEGORY_TERMS[category]
    return tuple(fragment for fragment in fragments if any(term in fragment.casefold() for term in terms) or not _category_signal(fragment))


def _category_signal(fragment: str) -> bool:
    lowered = fragment.casefold()
    return any(term in lowered for terms in _CATEGORY_TERMS.values() for term in terms)


def merge_preferences(memory: Mapping[str, Any], query: Mapping[str, Any]) -> dict[str, Any]:
    """Merge memory first, then query so explicit current constraints win."""
    merged = dict(memory)
    for key, value in query.items():
        if key in {"required_features", "excluded_features"}:
            merged[key] = tuple(dict.fromkeys((*merged.get(key, ()), *value)))
        else:
            merged[key] = value
    required = set(merged.get("required_features", ()))
    excluded = set(merged.get("excluded_features", ()))
    query_required = set(query.get("required_features", ()))
    query_excluded = set(query.get("excluded_features", ()))
    excluded -= query_required
    required -= query_excluded
    if required: merged["required_features"] = tuple(sorted(required))
    else: merged.pop("required_features", None)
    if excluded: merged["excluded_features"] = tuple(sorted(excluded))
    else: merged.pop("excluded_features", None)
    category = merged.get("category")
    if category:
        allowed = _CATEGORY_FEATURES.get(category, set())
        for key in ("required_features", "excluded_features"):
            if key in merged:
                memory_values = set(merged[key]) - set(query.get(key, ()))
                values = tuple(value for value in memory_values if value in allowed)
                values = tuple(dict.fromkeys((*values, *query.get(key, ()))))
                if values: merged[key] = values
                else: merged.pop(key)
        if "wireless" not in query and category != "headphones":
            merged.pop("wireless", None)
        if "noise_cancellation" not in query and category != "headphones":
            merged.pop("noise_cancellation", None)
    return merged


def _empty_response(customer_id: str, query: str, started: float, status: str, error: str, recall_latency: float = 0.0, extraction_latency: float = 0.0) -> ChatResponse:
    return ChatResponse(customer_id, query, (), {}, {}, {}, (), (), recall_latency, extraction_latency, 0.0, (perf_counter() - started) * 1000, status, error)
