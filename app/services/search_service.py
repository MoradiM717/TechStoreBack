"""
Elasticsearch-backed product search.

Index mapping is intentionally denormalized (brand name, category name, price,
flattened specs) so the storefront can filter/facet in a single query instead
of joining across Postgres tables at read time.

Postgres remains the source of truth. Every admin create/update/delete pushes a
fresh document here; nothing is ever read back out of ES and written to Postgres.

Elasticsearch is the only read model for search. There is deliberately no
Postgres fallback: a second query path meant two implementations of "what does
this query return", and they diverged. If ES is unreachable, search raises
`SearchUnavailableError` and the storefront surfaces a 503 rather than quietly
answering from a weaker engine.
"""
import logging
from typing import Any, Optional

from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ApiError, TransportError
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.category import Category
from app.models.product import Product

logger = logging.getLogger(__name__)


class SearchUnavailableError(RuntimeError):
    """Elasticsearch could not answer. Routers map this to a 503."""

es_client = Elasticsearch(settings.elasticsearch_url, request_timeout=3, max_retries=1)

INDEX_NAME = settings.products_index

SORT_OPTIONS = ("relevance", "price_asc", "price_desc", "name_asc", "newest")

INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "name": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "slug": {"type": "keyword"},
            # keyword for exact facet terms, `.text` sub-field for free-text matching
            "brand": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "description": {"type": "text"},
            "category_slug": {"type": "keyword"},
            "category_name": {"type": "keyword", "fields": {"text": {"type": "text"}}},
            "category_path": {"type": "keyword"},  # e.g. ["servers", "gpu-servers"]
            "specs": {"type": "object", "enabled": True},
            "min_price": {"type": "scaled_float", "scaling_factor": 100},
            "max_price": {"type": "scaled_float", "scaling_factor": 100},
            "stock_quantity": {"type": "integer"},
            "has_price": {"type": "boolean"},
            "currency": {"type": "keyword"},
            "conditions": {"type": "keyword"},
            "in_stock": {"type": "boolean"},
            "is_published": {"type": "boolean"},
            "created_at": {"type": "date"},
            "thumbnail_url": {"type": "keyword", "index": False},
        }
    }
}


# --------------------------------------------------------------------------
# Indexing
# --------------------------------------------------------------------------

def ensure_index() -> None:
    if not es_client.indices.exists(index=INDEX_NAME):
        es_client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)


def _category_path(category: Optional[Category]) -> list[str]:
    """Walk up the parent chain so a search for `servers` also matches `gpu-servers`."""
    path: list[str] = []
    seen: set[int] = set()
    node = category
    while node is not None and node.id not in seen:
        seen.add(node.id)
        path.append(node.slug)
        node = node.parent
    return list(reversed(path))


def product_to_document(product: Product) -> dict:
    # A variant with no price is sold on request, which is the norm for this
    # catalogue. Those SKUs index with a null price and `has_price` false, so the
    # storefront can tell "free" apart from "ask us".
    priced = [v.price for v in product.variants if v.price is not None]
    stock = sum(v.stock_quantity for v in product.variants)
    images = sorted(product.images, key=lambda i: (i.sort_order, i.id))
    thumbnail = images[0].url if images else None

    return {
        "name": product.name,
        "slug": product.slug,
        "brand": product.brand,
        "description": product.description or "",
        "category_slug": product.category.slug if product.category else None,
        "category_name": product.category.name if product.category else None,
        "category_path": _category_path(product.category),
        "specs": product.specs or {},
        "min_price": float(min(priced)) if priced else None,
        "max_price": float(max(priced)) if priced else None,
        "has_price": bool(priced),
        "currency": next((v.currency for v in product.variants if v.currency), "IRR"),
        "conditions": sorted({v.condition for v in product.variants if v.condition}),
        "stock_quantity": stock,
        "in_stock": stock > 0,
        "is_published": product.is_published,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "thumbnail_url": thumbnail,
    }


def index_product(product: Product) -> bool:
    """
    Call this after create/update in the admin panel to keep ES in sync with Postgres.

    Returns False if the write failed. Indexing never blocks the admin write: Postgres
    has already committed, and the caller surfaces a soft warning instead of a 500.
    """
    try:
        ensure_index()
        es_client.index(index=INDEX_NAME, id=product.id, document=product_to_document(product))
        return True
    except (ApiError, TransportError, OSError) as exc:
        logger.warning("Elasticsearch index failed for product %s: %s", product.id, exc)
        return False


def remove_product(product_id: int) -> bool:
    try:
        es_client.delete(index=INDEX_NAME, id=product_id, ignore=[404])
        return True
    except (ApiError, TransportError, OSError) as exc:
        logger.warning("Elasticsearch delete failed for product %s: %s", product_id, exc)
        return False


def reindex_all(db: Session, recreate: bool = True) -> int:
    """
    Rebuild the whole index from Postgres. Used by the seed script and ops.

    `recreate` drops the index first, which is what you want after editing
    INDEX_MAPPING — Elasticsearch won't change the type of an existing field.
    """
    if recreate and es_client.indices.exists(index=INDEX_NAME):
        es_client.indices.delete(index=INDEX_NAME)
    ensure_index()
    products = (
        db.query(Product)
        .options(
            joinedload(Product.variants),
            joinedload(Product.images),
            joinedload(Product.category),
        )
        .all()
    )
    count = 0
    for product in products:
        if product.is_published:
            if index_product(product):
                count += 1
        else:
            remove_product(product.id)
    es_client.indices.refresh(index=INDEX_NAME)
    return count


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _es_sort(sort: str) -> list[Any]:
    return {
        "relevance": ["_score", {"name.keyword": "asc"}],
        "price_asc": [{"min_price": {"order": "asc", "missing": "_last"}}],
        "price_desc": [{"min_price": {"order": "desc", "missing": "_last"}}],
        "name_asc": [{"name.keyword": "asc"}],
        "newest": [{"created_at": {"order": "desc", "missing": "_last"}}],
    }.get(sort, ["_score", {"name.keyword": "asc"}])


def _spec_term(key: str, value: Any) -> dict:
    # Dynamically-mapped strings land as text + a `.keyword` sub-field; exact
    # matching has to target the sub-field or it silently returns nothing.
    field = f"specs.{key}" if isinstance(value, (int, float, bool)) else f"specs.{key}.keyword"
    return {"term": {field: value}}


def search_products(
    db: Session,
    query: str | None = None,
    category_slug: str | None = None,
    brand: str | None = None,
    brands: list[str] | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock_only: bool = False,
    specs_filter: dict | None = None,
    sort: str = "relevance",
    page: int = 1,
    page_size: int = 24,
) -> dict:
    """Returns a normalized result envelope. Raises SearchUnavailableError if ES is down."""
    selected_brands = [b for b in (brands or []) if b] or ([brand] if brand else [])
    try:
        kwargs = dict(
            query=query,
            category_slug=category_slug,
            brands=selected_brands,
            min_price=min_price,
            max_price=max_price,
            in_stock_only=in_stock_only,
            specs_filter=specs_filter,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        result = _search_products_es(**kwargs)
        if result["total"] == 0 and query:
            # Nothing matched exactly — try again allowing typos rather than
            # showing an empty page for a one-character slip.
            relaxed_result = _search_products_es(**kwargs, relaxed=True)
            if relaxed_result["total"] > 0:
                return relaxed_result
        return result
    except (ApiError, TransportError, OSError) as exc:
        logger.error("Elasticsearch search failed: %s", exc)
        raise SearchUnavailableError(str(exc)) from exc


SEARCH_FIELDS = ["name^3", "brand.text^2", "category_name.text", "description"]


def _text_query(query: str, relaxed: bool) -> dict:
    """
    Strict pass: every term has to appear somewhere across name/brand/category/
    description (`cross_fields` + `and`), so "amd cpu" matches but
    "quantum flux capacitor" matches nothing.

    Relaxed pass: only used when the strict pass found nothing. Drops to per-field
    OR matching with fuzziness so a typo'd part number still lands somewhere, and
    the caller flags the result as approximate.
    """
    if relaxed:
        # Every term still has to match something, but each one may match a
        # different field and may be a near-miss. "poweredg" finds the PowerEdge;
        # "quantum flux capacitor" still finds nothing.
        terms = [t for t in query.split() if t]
        return {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": term,
                            "fields": SEARCH_FIELDS,
                            "fuzziness": "AUTO",
                        }
                    }
                    for term in terms
                ],
                "minimum_should_match": len(terms) or 1,
            }
        }
    return {
        "multi_match": {
            "query": query,
            "fields": SEARCH_FIELDS,
            "type": "cross_fields",
            "operator": "and",
        }
    }


def _search_products_es(
    query: str | None,
    category_slug: str | None,
    brands: list[str],
    min_price: float | None,
    max_price: float | None,
    in_stock_only: bool,
    specs_filter: dict | None,
    sort: str,
    page: int,
    page_size: int,
    relaxed: bool = False,
) -> dict:
    must: list[dict] = [{"term": {"is_published": True}}]

    if query:
        must.append(_text_query(query, relaxed=relaxed))
    if category_slug:
        # category_path contains the whole ancestor chain, so a parent slug
        # matches everything beneath it.
        must.append({"term": {"category_path": category_slug}})
    if brands:
        must.append({"terms": {"brand": brands}})
    if in_stock_only:
        must.append({"term": {"in_stock": True}})
    if min_price is not None or max_price is not None:
        price_range: dict[str, float] = {}
        if min_price is not None:
            price_range["gte"] = min_price
        if max_price is not None:
            price_range["lte"] = max_price
        must.append({"range": {"min_price": price_range}})
    for key, value in (specs_filter or {}).items():
        must.append(_spec_term(key, value))

    body = {
        "query": {"bool": {"must": must}},
        "from": max(page - 1, 0) * page_size,
        "size": page_size,
        "sort": _es_sort(sort),
        "track_total_hits": True,
        "aggs": {
            # Facet counts are computed with the *other* filters applied, which is
            # what shoppers expect: narrowing by category updates the brand counts.
            "brands": {"terms": {"field": "brand", "size": 50}},
            "categories": {"terms": {"field": "category_slug", "size": 50}},
            "price_bounds": {"stats": {"field": "min_price"}},
            "conditions": {"terms": {"field": "conditions", "size": 10}},
        },
    }
    results = es_client.search(index=INDEX_NAME, body=body)
    aggs = results.get("aggregations", {})
    price_stats = aggs.get("price_bounds", {})

    return {
        "total": results["hits"]["total"]["value"],
        "page": page,
        "page_size": page_size,
        "items": [{"id": int(h["_id"]), **h["_source"]} for h in results["hits"]["hits"]],
        "facets": {
            "brands": aggs.get("brands", {}).get("buckets", []),
            "categories": aggs.get("categories", {}).get("buckets", []),
            "conditions": aggs.get("conditions", {}).get("buckets", []),
            "price": {
                "min": price_stats.get("min"),
                "max": price_stats.get("max"),
            },
        },
        "backend": "elasticsearch",
        # True when the strict pass found nothing and these are approximate matches.
        "relaxed": relaxed,
    }


def suggest_products(db: Session, query: str, limit: int = 6) -> list[dict]:
    """Search-as-you-type suggestions for the header search box."""
    if not query.strip():
        return []
    result = search_products(db, query=query, page=1, page_size=limit)
    return [
        {
            "id": item["id"],
            "name": item.get("name"),
            "slug": item.get("slug"),
            "brand": item.get("brand"),
            "min_price": item.get("min_price"),
            "thumbnail_url": item.get("thumbnail_url"),
        }
        for item in result["items"]
    ]
