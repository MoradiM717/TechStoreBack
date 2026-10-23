from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.i18n import resolve_locale, translate
from app.models.product import Product, ProductVariant
from app.schemas.product import ProductOut
from app.services.search_service import (
    SORT_OPTIONS,
    SearchUnavailableError,
    search_products,
    suggest_products,
)

router = APIRouter(prefix="/api/products", tags=["storefront-products"])


def _coerce_spec_value(raw: str):
    """`?spec_cores=64` should filter numerically; `?spec_socket=SP5` as a keyword."""
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


@router.get("/search")
def search(
    request: Request,
    q: str | None = Query(None, description="Free-text search term"),
    category: str | None = None,
    brand: list[str] | None = Query(None, description="Repeatable: ?brand=Dell&brand=HPE"),
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool = False,
    sort: str = Query("relevance", description=f"One of: {', '.join(SORT_OPTIONS)}"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=96),
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    """
    Powers the faceted search bar + filter sidebar (brand, category, price range, specs)
    on the storefront, similar to Best Buy / Amazon filter UX.

    Spec filters arrive as `spec_<key>=<value>`, e.g. `?spec_gpu_count=8`.
    """
    if sort not in SORT_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort '{sort}'. Expected one of: {', '.join(SORT_OPTIONS)}.",
        )

    specs_filter = {
        key[len("spec_"):]: _coerce_spec_value(value)
        for key, value in request.query_params.items()
        if key.startswith("spec_") and value != ""
    }

    try:
        return search_products(
            db,
            query=q,
            category_slug=category,
            brands=brand,
            min_price=min_price,
            max_price=max_price,
            in_stock_only=in_stock,
            specs_filter=specs_filter or None,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except SearchUnavailableError:
        # Elasticsearch is the only read model for search; there is no second
        # path to fall back to, so say so plainly instead of returning 0 results.
        raise HTTPException(
            status_code=503,
            detail=translate(
                "Search is temporarily unavailable. Please try again in a moment.",
                resolve_locale(accept_language),
            ),
        )


@router.get("/suggest")
def suggest(
    q: str = Query("", description="Partial query from the header search box"),
    limit: int = Query(6, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Search-as-you-type dropdown in the storefront header."""
    try:
        return {"items": suggest_products(db, q, limit=limit)}
    except SearchUnavailableError:
        # A dead type-ahead must not break the header; an empty list is honest here.
        return {"items": []}


@router.get("/{slug}", response_model=ProductOut)
def get_product_by_slug(
    slug: str,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    product = (
        db.query(Product)
        .options(
            joinedload(Product.variants),
            joinedload(Product.images),
            joinedload(Product.category),
        )
        .filter(Product.slug == slug, Product.is_published.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(
            status_code=404,
            detail=translate("Product not found", resolve_locale(accept_language)),
        )
    return product


@router.get("/{slug}/related", response_model=list[ProductOut])
def get_related_products(slug: str, limit: int = Query(4, ge=1, le=12), db: Session = Depends(get_db)):
    """Other published products in the same category — shown at the foot of the PDP."""
    product = db.query(Product).filter(Product.slug == slug).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return (
        db.query(Product)
        .options(
            joinedload(Product.variants),
            joinedload(Product.images),
            joinedload(Product.category),
        )
        .filter(
            Product.category_id == product.category_id,
            Product.id != product.id,
            Product.is_published.is_(True),
        )
        .order_by(Product.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/variants/{variant_id}/stock")
def get_variant_stock(
    variant_id: int,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    """
    Live stock check. The cart calls this before checkout so a shopper finds out
    something sold out on the cart page rather than at the confirm step.
    """
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
    if not variant:
        raise HTTPException(
            status_code=404,
            detail=translate("Variant not found", resolve_locale(accept_language)),
        )
    return {
        "variant_id": variant.id,
        "sku": variant.sku,
        "price": float(variant.price),
        "stock_quantity": variant.stock_quantity,
        "in_stock": variant.stock_quantity > 0,
    }
