import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.security import require_role
from app.models.category import Category
from app.models.order import OrderItem
from app.models.product import Product, ProductImage, ProductVariant
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services.search_service import index_product, reindex_all, remove_product

router = APIRouter(prefix="/api/admin/products", tags=["admin-products"])

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProductListEnvelope(BaseModel):
    total: int
    items: list[ProductOut] = []
    # True when Postgres committed but the search index didn't accept the write.
    search_index_stale: bool = False


class ProductWriteResult(ProductOut):
    search_index_warning: str | None = None


def _validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=422,
            detail="Slug must be lowercase letters, numbers and single hyphens, e.g. 'dell-poweredge-r760'.",
        )
    return slug


def _require_category(db: Session, category_id: int) -> Category:
    category = db.query(Category).filter(Category.id == category_id).first()
    if category is None:
        raise HTTPException(
            status_code=422,
            detail="That category doesn't exist. Pick one from the category list, or create it first.",
        )
    return category


def _check_sku_conflicts(db: Session, skus: list[str], product_id: int | None) -> None:
    duplicates = {sku for sku in skus if skus.count(sku) > 1}
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate SKU in this product: {', '.join(sorted(duplicates))}. Every SKU must be unique.",
        )
    query = db.query(ProductVariant).filter(ProductVariant.sku.in_(skus or [""]))
    if product_id is not None:
        query = query.filter(ProductVariant.product_id != product_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"SKU '{clash.sku}' is already used by another product.",
        )


def _sync_index(product: Product) -> str | None:
    """Push to ES (or drop from it) and report a human-readable warning if that failed."""
    ok = index_product(product) if product.is_published else remove_product(product.id)
    if ok:
        return None
    return (
        "Saved to the database, but the search index couldn't be updated — "
        "this product may not appear in storefront search until the next reindex."
    )


@router.get("", response_model=ProductListEnvelope)
def list_products(
    q: str | None = Query(None, description="Match product name, brand or slug"),
    category_id: int | None = None,
    status: str | None = Query(None, description="published | draft"),
    sort: str = Query("newest", description="newest | name_asc | name_desc | stock_asc | stock_desc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    query = db.query(Product)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(Product.name.ilike(like), Product.brand.ilike(like), Product.slug.ilike(like))
        )
    if category_id is not None:
        query = query.filter(Product.category_id == category_id)
    if status == "published":
        query = query.filter(Product.is_published.is_(True))
    elif status == "draft":
        query = query.filter(Product.is_published.is_(False))
    elif status is not None:
        raise HTTPException(status_code=422, detail="Status filter must be 'published' or 'draft'.")

    total = query.count()

    if sort in ("stock_asc", "stock_desc"):
        stock = func.coalesce(func.sum(ProductVariant.stock_quantity), 0)
        query = (
            query.outerjoin(ProductVariant, ProductVariant.product_id == Product.id)
            .group_by(Product.id)
            .order_by(stock.asc() if sort == "stock_asc" else stock.desc())
        )
    else:
        query = query.order_by(
            {
                "name_asc": Product.name.asc(),
                "name_desc": Product.name.desc(),
            }.get(sort, Product.created_at.desc())
        )

    products = (
        query.options(
            selectinload(Product.variants),
            selectinload(Product.images),
            joinedload(Product.category),
        )
        .offset(skip)
        .limit(limit)
        .all()
    )
    return ProductListEnvelope(total=total, items=products)


@router.post("/reindex")
def reindex(
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin")),
):
    """Rebuild the Elasticsearch index from Postgres — recovery for a stale index."""
    try:
        count = reindex_all(db)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
        raise HTTPException(status_code=502, detail=f"Elasticsearch is not reachable: {exc}")
    return {"indexed": count}


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    product = (
        db.query(Product)
        .options(
            selectinload(Product.variants),
            selectinload(Product.images),
            joinedload(Product.category),
        )
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("", response_model=ProductWriteResult, status_code=201)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    slug = _validate_slug(payload.slug)
    if db.query(Product).filter(Product.slug == slug).first():
        raise HTTPException(
            status_code=409,
            detail=f"A product with the slug '{slug}' already exists. Pick a different slug.",
        )
    _require_category(db, payload.category_id)
    if not payload.variants:
        raise HTTPException(
            status_code=422,
            detail="Add at least one variant — price and stock live on variants, not on the product.",
        )
    _check_sku_conflicts(db, [v.sku for v in payload.variants], product_id=None)

    product = Product(
        name=payload.name.strip(),
        slug=slug,
        brand=payload.brand.strip(),
        category_id=payload.category_id,
        description=payload.description,
        specs=payload.specs or {},
        is_published=payload.is_published,
    )
    for variant_data in payload.variants:
        data = variant_data.model_dump(exclude={"id"})
        product.variants.append(ProductVariant(**data))
    for index, image_data in enumerate(payload.images):
        product.images.append(
            ProductImage(
                url=image_data.url,
                alt_text=image_data.alt_text or payload.name,
                sort_order=image_data.sort_order or index,
            )
        )

    db.add(product)
    db.commit()
    db.refresh(product)

    warning = _sync_index(product) if product.is_published else None
    return ProductWriteResult(
        **ProductOut.model_validate(product).model_dump(), search_index_warning=warning
    )


def _sync_variants(db: Session, product: Product, incoming) -> None:
    """Treat the submitted list as the desired end state: update, create, delete."""
    _check_sku_conflicts(db, [v.sku for v in incoming], product_id=product.id)

    existing = {v.id: v for v in product.variants}
    keep_ids = {v.id for v in incoming if v.id is not None}

    removed_ids = [vid for vid in existing if vid not in keep_ids]
    if removed_ids:
        # An ordered variant can't disappear or the order history loses its line items.
        ordered = (
            db.query(OrderItem.variant_id)
            .filter(OrderItem.variant_id.in_(removed_ids))
            .distinct()
            .all()
        )
        blocked = {row.variant_id for row in ordered}
        if blocked:
            skus = ", ".join(sorted(existing[vid].sku for vid in blocked))
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Can't remove SKU(s) {skus} — they appear on existing orders. "
                    "Set the stock to 0 instead to stop selling them."
                ),
            )

    for variant_data in incoming:
        if variant_data.id is not None and variant_data.id in existing:
            variant = existing[variant_data.id]
            for field, value in variant_data.model_dump(exclude={"id"}).items():
                setattr(variant, field, value)
        else:
            product.variants.append(ProductVariant(**variant_data.model_dump(exclude={"id"})))

    for vid in removed_ids:
        product.variants.remove(existing[vid])


def _sync_images(product: Product, incoming) -> None:
    product.images.clear()
    for index, image_data in enumerate(incoming):
        product.images.append(
            ProductImage(
                url=image_data.url,
                alt_text=image_data.alt_text or product.name,
                sort_order=image_data.sort_order or index,
            )
        )


@router.patch("/{product_id}", response_model=ProductWriteResult)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    product = (
        db.query(Product)
        .options(selectinload(Product.variants), selectinload(Product.images))
        .filter(Product.id == product_id)
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    data = payload.model_dump(exclude_unset=True)
    variants = data.pop("variants", None)
    images = data.pop("images", None)

    if data.get("slug"):
        slug = _validate_slug(data["slug"])
        clash = db.query(Product).filter(Product.slug == slug, Product.id != product_id).first()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"A product with the slug '{slug}' already exists. Pick a different slug.",
            )
        data["slug"] = slug
    if data.get("category_id") is not None:
        _require_category(db, data["category_id"])

    for field, value in data.items():
        setattr(product, field, value)

    if variants is not None:
        if not payload.variants:
            raise HTTPException(
                status_code=422,
                detail="A product needs at least one variant. Set stock to 0 rather than removing them all.",
            )
        _sync_variants(db, product, payload.variants)
    if images is not None:
        _sync_images(product, payload.images or [])

    db.commit()
    db.refresh(product)

    warning = _sync_index(product)
    return ProductWriteResult(
        **ProductOut.model_validate(product).model_dump(), search_index_warning=warning
    )


@router.delete("/{product_id}", status_code=204)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin")),  # only full admins can hard-delete
):
    product = (
        db.query(Product).options(selectinload(Product.variants)).filter(Product.id == product_id).first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    variant_ids = [v.id for v in product.variants]
    if variant_ids:
        ordered = db.query(OrderItem).filter(OrderItem.variant_id.in_(variant_ids)).count()
        if ordered:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This product appears on {ordered} order line(s), so deleting it would break "
                    "that order history. Unpublish it instead to take it off the storefront."
                ),
            )

    db.delete(product)
    db.commit()
    remove_product(product_id)
