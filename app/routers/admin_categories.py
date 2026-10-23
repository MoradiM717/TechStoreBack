"""
Category CRUD for the admin panel.

Categories were previously seeded by hand, which meant the product form had to
guess at category ids. Everything that needs a category id now reads it from here.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_role
from app.models.category import Category
from app.models.product import Product
from app.routers.categories import category_rows
from app.schemas.product import CategoryCreate, CategoryDetailOut, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/api/admin/categories", tags=["admin-categories"])

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_slug(slug: str) -> str:
    slug = slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=422,
            detail="Slug must be lowercase letters, numbers and single hyphens, e.g. 'gpu-servers'.",
        )
    return slug


def _guard_parent(db: Session, category_id: int | None, parent_id: int | None) -> None:
    """Reject parents that don't exist, self-parenting, and cycles."""
    if parent_id is None:
        return
    if category_id is not None and parent_id == category_id:
        raise HTTPException(status_code=422, detail="A category cannot be its own parent.")
    parent = db.query(Category).filter(Category.id == parent_id).first()
    if parent is None:
        raise HTTPException(status_code=422, detail="Parent category does not exist.")

    seen: set[int] = set()
    node = parent
    while node is not None:
        if node.id in seen:
            break
        seen.add(node.id)
        if category_id is not None and node.id == category_id:
            raise HTTPException(
                status_code=422,
                detail="That parent is a descendant of this category, which would create a loop.",
            )
        node = node.parent


@router.get("", response_model=list[CategoryDetailOut])
def list_categories(
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    return category_rows(db)


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    slug = _validate_slug(payload.slug)
    if db.query(Category).filter(Category.slug == slug).first():
        raise HTTPException(status_code=409, detail=f"A category with the slug '{slug}' already exists.")
    _guard_parent(db, None, payload.parent_id)

    category = Category(name=payload.name.strip(), slug=slug, parent_id=payload.parent_id)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    data = payload.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"] is not None:
        slug = _validate_slug(data["slug"])
        clash = db.query(Category).filter(Category.slug == slug, Category.id != category_id).first()
        if clash:
            raise HTTPException(status_code=409, detail=f"A category with the slug '{slug}' already exists.")
        data["slug"] = slug
    if "parent_id" in data:
        _guard_parent(db, category_id, data["parent_id"])
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()

    for field, value in data.items():
        setattr(category, field, value)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=204)
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin")),
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    product_count = db.query(Product).filter(Product.category_id == category_id).count()
    if product_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{category.name}' still has {product_count} product(s). "
                "Move them to another category first, then delete this one."
            ),
        )
    child_count = db.query(Category).filter(Category.parent_id == category_id).count()
    if child_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{category.name}' has {child_count} sub-categor{'y' if child_count == 1 else 'ies'}. "
                "Delete or re-parent those first."
            ),
        )

    db.delete(category)
    db.commit()
