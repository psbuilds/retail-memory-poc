# `app/products.py` — validated catalog and deterministic recommendations

## 1. File Purpose

This module loads the original 19-product, four-category retail catalog and
applies structured recommendation preferences. It is independent of Cognee,
MCP, CLI adapters, network calls, LLMs, and chatbot orchestration.

The future chatbot will translate user queries and recalled customer facts into
the structured `Preferences` mapping. This module only validates that mapping,
filters products, and ranks the survivors.

## 2. Classes and Data Structures

### `Product`

`Product` is a frozen, slotted dataclass. It contains the stable ID, name,
category, brand, positive price, normalized feature tuple, wireless and noise
cancellation booleans, and a 0–5 rating. Immutable records prevent filtering and
sorting from changing the loaded catalog.

### `Preferences`

The `TypedDict` supports optional structured constraints:

- `category`, `max_budget`, `wireless`, `preferred_brand`
- `required_features`, `noise_cancellation`
- `excluded_features`, `excluded_product_ids`

The exclusion fields provide general negative-preference support for facts such
as “avoid bulky”, “avoid synthetic”, or “dislike in-ear”. No customer-specific
rules are hardcoded.

## 3. Functions and Methods

### `load_catalog()` and `validate_catalog()`

`load_catalog` reads `data/products.json` and validates its list-of-object shape.
`validate_catalog` validates every required field, unique IDs, non-empty text,
positive finite prices, finite ratings between 0 and 5, booleans, and non-empty
string feature lists. Errors identify the product index and invalid field.

### Filter functions

`filter_by_category`, `filter_by_max_budget`, `filter_by_brand`,
`filter_by_wireless`, and `filter_by_noise_cancellation` apply exact structured
constraints. Brand and category comparisons trim whitespace and ignore case.
`filter_by_required_features` requires every requested feature. Exclusion filters
remove products containing any excluded feature or whose ID is excluded.

`None` means unrestricted. A budget of zero is valid and normally produces no
matches; negative or non-finite budgets are rejected.

### `recommend_products(products, preferences=None, limit=3)`

The pipeline is deliberately ordered:

1. Validate preference keys and types.
2. Filter category.
3. Filter maximum budget.
4. Filter preferred brand.
5. Filter wireless and noise cancellation.
6. Require all requested features.
7. Apply excluded features and IDs.
8. Sort and apply the limit.

Hard constraints run before ranking, so a highly rated product cannot override a
budget, category, feature, or exclusion constraint. Results are ranked by rating
descending, price ascending, then product ID ascending. Empty matches return an
empty list and never receive fallback products.

## 4. Important Python Syntax and Concepts

- `@dataclass(frozen=True, slots=True)` creates compact immutable records.
- `TypedDict` documents the accepted mapping keys without adding runtime state.
- `Mapping` and `Sequence` allow callers to provide ordinary dictionaries and
  lists while keeping the engine independent of a specific application layer.
- `math.isfinite` rejects NaN and positive/negative infinity.
- List comprehensions create new filtered lists without mutating the input.
- `sorted(..., key=...)` provides a reproducible multi-criterion ranking.
- `raise ValueError(...)` and `raise TypeError(...)` fail clearly on malformed
  catalog data or preferences rather than silently guessing.

## 5. Execution Flow

```text
load_catalog()
  → read JSON
  → validate 19 records and IDs
  → tuple[Product, ...]

chatbot-generated Preferences
  → recommend_products(catalog, preferences)
  → category → budget → brand → wireless/ANC
  → required features → exclusions
  → deterministic sort → limited list[Product]
```

Natural-language parsing is intentionally deferred to Phase 8. Memory adapters
are not imported, so identical catalog and normalized preference inputs produce
identical recommendations for both CLI and MCP agent runs.

## 6. Architecture and Design Reasoning

The restored catalog preserves the original benchmark's headphones, footwear,
apparel, and bags scenarios. Category filtering is therefore part of the shared
engine instead of being hidden in chatbot code. Product data validation makes
cross-file scenario regressions visible early, while immutable records preserve
repeatability.

The recommendation engine contains no integration-specific behavior. Phase 8
can use memory to construct preferences, and Phase 9 can run the same engine
after either adapter. This keeps memory integration as the experimental variable
rather than allowing ranking or catalog logic to differ.

The current POC uses deterministic plausible brands and ratings for fields that
were absent from the original Phase 2 records. Scenario ground truth, IDs,
names, categories, prices, and feature meanings remain from the historical
catalog. The engine does not yet infer structured preferences from natural
language; that remains a future chatbot responsibility.
