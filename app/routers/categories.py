"""Public category endpoints. The storefront nav, homepage and the admin product
form all need real category data — this is the single place it comes from."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.category import Category
from app.models.product import Product
from app.schemas.product import CategoryDetailOut

router = APIRouter(prefix="/api/categories", tags=["categories"])


def category_rows(db: Session) -> list[CategoryDetailOut]:
    total_counts = dict(
        db.query(Product.category_id, func.count(Product.id))
        .group_by(Product.category_id)
        .all()
    )
    published_counts = dict(
        db.query(Product.category_id, func.count(Product.id))
        .filter(Product.is_published.is_(True))
        .group_by(Product.category_id)
        .all()
    )
    categories = db.query(Category).order_by(Category.name).all()
    return [
        CategoryDetailOut(
            id=c.id,
            name=c.name,
            slug=c.slug,
            parent_id=c.parent_id,
            product_count=total_counts.get(c.id, 0),
            published_product_count=published_counts.get(c.id, 0),
        )
        for c in categories
    ]


@router.get("", response_model=list[CategoryDetailOut])
def list_categories(db: Session = Depends(get_db)):
    return category_rows(db)


@router.get("/{slug}", response_model=CategoryDetailOut)
def get_category(slug: str, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.slug == slug).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    for row in category_rows(db):
        if row.id == category.id:
            return row
    raise HTTPException(status_code=404, detail="Category not found")
