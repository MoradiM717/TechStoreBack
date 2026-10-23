"""Order management for the admin panel: list, filter, inspect, advance status."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.security import require_role
from app.models.order import (
    ORDER_STATUS_TRANSITIONS,
    ORDER_STATUSES,
    STOCK_RELEASING_STATUSES,
    Customer,
    Order,
)
from app.models.product import Product, ProductVariant
from app.routers.orders import serialize_order
from app.schemas.order import OrderListOut, OrderOut, OrderStatusUpdate, OrderSummaryOut
from app.services.search_service import index_product

router = APIRouter(prefix="/api/admin/orders", tags=["admin-orders"])


@router.get("", response_model=OrderListOut)
def list_orders(
    status: str | None = Query(None, description="Filter by a single order status"),
    q: str | None = Query(None, description="Match order reference, customer email or name"),
    sort: str = Query("newest", description="newest | oldest | total_desc | total_asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    if status and status not in ORDER_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status '{status}'. Expected one of: {', '.join(ORDER_STATUSES)}.",
        )

    query = db.query(Order).join(Customer, Order.customer_id == Customer.id)
    if status:
        query = query.filter(Order.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Order.reference.ilike(like),
                Customer.email.ilike(like),
                Customer.full_name.ilike(like),
            )
        )

    total = query.count()

    order_by = {
        "oldest": Order.created_at.asc(),
        "total_desc": Order.total_amount.desc(),
        "total_asc": Order.total_amount.asc(),
    }.get(sort, Order.created_at.desc())

    orders = (
        query.options(joinedload(Order.customer), selectinload(Order.items))
        .order_by(order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return OrderListOut(
        total=total,
        items=[
            OrderSummaryOut(
                id=o.id,
                reference=o.reference,
                status=o.status,
                total_amount=o.total_amount,
                created_at=o.created_at,
                customer_email=o.customer.email if o.customer else None,
                customer_name=o.customer.full_name if o.customer else None,
                item_count=sum(item.quantity for item in o.items),
            )
            for o in orders
        ],
    )


@router.get("/stats")
def order_stats(
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    """Counts per status for the admin dashboard tiles and order-filter chips."""
    rows = db.query(Order.status, func.count(Order.id)).group_by(Order.status).all()
    counts = {status: 0 for status in ORDER_STATUSES}
    counts.update({status: count for status, count in rows})
    revenue = (
        db.query(func.coalesce(func.sum(Order.total_amount), 0))
        .filter(Order.status.notin_(STOCK_RELEASING_STATUSES))
        .scalar()
    )
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "revenue": float(revenue or 0),
    }


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    order = (
        db.query(Order)
        .options(selectinload(Order.items), joinedload(Order.customer))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(order, db)


@router.get("/{order_id}/transitions")
def allowed_transitions(
    order_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    """The admin UI renders exactly these as buttons, so it can't offer an illegal move."""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {
        "status": order.status,
        "allowed": list(ORDER_STATUS_TRANSITIONS.get(order.status, ())),
    }


@router.patch("/{order_id}/status", response_model=OrderOut)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    order = (
        db.query(Order)
        .options(selectinload(Order.items), joinedload(Order.customer))
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if payload.status == order.status:
        return serialize_order(order, db)

    allowed = ORDER_STATUS_TRANSITIONS.get(order.status, ())
    if payload.status not in allowed:
        readable = ", ".join(allowed) if allowed else "nothing — this order is in a final state"
        raise HTTPException(
            status_code=409,
            detail=f"An order that is '{order.status}' can only move to: {readable}.",
        )

    was_released = order.status in STOCK_RELEASING_STATUSES
    now_released = payload.status in STOCK_RELEASING_STATUSES

    touched_product_ids: set[int] = set()
    if now_released and not was_released:
        # Cancelling or refunding hands the reserved units back to inventory.
        variant_ids = [item.variant_id for item in order.items]
        variants = (
            db.query(ProductVariant)
            .filter(ProductVariant.id.in_(variant_ids or [-1]))
            .with_for_update()
            .all()
        )
        by_id = {v.id: v for v in variants}
        for item in order.items:
            variant = by_id.get(item.variant_id)
            if variant is not None:
                variant.stock_quantity += item.quantity
                touched_product_ids.add(variant.product_id)

    order.status = payload.status
    db.commit()
    db.refresh(order)

    if touched_product_ids:
        products = db.query(Product).filter(Product.id.in_(touched_product_ids)).all()
        for product in products:
            if product.is_published:
                index_product(product)

    return serialize_order(order, db)
