"""
Storefront-facing order creation.

No payment gateway is wired up in this build (see README): an order is created in
`pending` and an operator moves it to `paid` from the admin panel once payment is
confirmed out of band. The checkout UI says so explicitly rather than pretending
a card was charged.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.i18n import resolve_locale, translate
from app.models.order import Customer, Order, OrderItem, generate_order_reference
from app.models.product import Product, ProductVariant
from app.schemas.order import OrderCreate, OrderOut
from app.services.search_service import index_product

router = APIRouter(prefix="/api/orders", tags=["storefront-orders"])


def serialize_order(order: Order, db: Session) -> dict:
    """Attach the product slug/attributes the UIs need but OrderItem doesn't store."""
    variant_ids = [item.variant_id for item in order.items]
    variants = (
        db.query(ProductVariant)
        .options(joinedload(ProductVariant.product))
        .filter(ProductVariant.id.in_(variant_ids or [-1]))
        .all()
    )
    by_id = {v.id: v for v in variants}

    items = []
    for item in order.items:
        variant = by_id.get(item.variant_id)
        items.append(
            {
                "id": item.id,
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "product_name": item.product_name,
                "variant_sku": item.variant_sku,
                "product_slug": variant.product.slug if variant and variant.product else None,
                "attributes": variant.attributes if variant else {},
            }
        )

    return {
        "id": order.id,
        "reference": order.reference,
        "status": order.status,
        "total_amount": order.total_amount,
        "shipping_address": order.shipping_address,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "customer": order.customer,
        "items": items,
    }


@router.post("", response_model=OrderOut, status_code=201)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    """
    Creates an Order + OrderItems from a cart.

    Prices come from the database, never from the request body, and variant rows
    are locked for the duration so two shoppers can't buy the same last unit.
    """
    locale = resolve_locale(accept_language)

    # Collapse duplicate lines for the same variant before touching stock.
    quantities: dict[int, int] = {}
    for line in payload.items:
        quantities[line.variant_id] = quantities.get(line.variant_id, 0) + line.quantity

    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.id.in_(list(quantities)))
        .with_for_update()
        .all()
    )
    found = {v.id: v for v in variants}

    missing = [vid for vid in quantities if vid not in found]
    if missing:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=translate(
                "Some items are no longer available and were removed from sale. "
                "Remove them from your cart and try again.",
                locale,
            ),
        )

    products = (
        db.query(Product)
        .filter(Product.id.in_([v.product_id for v in variants]))
        .all()
    )
    products_by_id = {p.id: p for p in products}

    # Reject the whole order if anything is short, naming what's short by how much.
    shortfall_template = translate("{left} left, {requested} requested", locale)
    shortfalls = [
        f"{products_by_id[v.product_id].name if v.product_id in products_by_id else v.sku} "
        f"({v.sku}): "
        + shortfall_template.format(left=v.stock_quantity, requested=quantities[v.id])
        for v in variants
        if v.stock_quantity < quantities[v.id]
    ]
    if shortfalls:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=translate("Not enough stock for: ", locale) + "; ".join(shortfalls) + ".",
        )

    unpublished = [
        v
        for v in variants
        if not getattr(products_by_id.get(v.product_id), "is_published", False)
    ]
    if unpublished:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=translate(
                "Some items in your cart are no longer on sale. Remove them and try again.",
                locale,
            ),
        )

    shipping = payload.shipping
    customer = db.query(Customer).filter(Customer.email == str(shipping.email)).first()
    if customer is None:
        # Guest checkout: a customer record with no credentials. Customer-facing
        # accounts are a separate surface that doesn't exist yet.
        customer = Customer(
            email=str(shipping.email),
            hashed_password=None,
            full_name=shipping.full_name,
            phone=shipping.phone,
        )
        db.add(customer)
        db.flush()
    else:
        customer.full_name = customer.full_name or shipping.full_name
        customer.phone = customer.phone or shipping.phone

    order = Order(
        reference=generate_order_reference(),
        customer_id=customer.id,
        status="pending",
        total_amount=Decimal("0"),
        shipping_address=shipping.as_block(),
    )

    total = Decimal("0")
    for variant_id, quantity in quantities.items():
        variant = found[variant_id]
        product = products_by_id.get(variant.product_id)
        line_total = Decimal(variant.price) * quantity
        total += line_total
        variant.stock_quantity -= quantity
        order.items.append(
            OrderItem(
                variant_id=variant.id,
                quantity=quantity,
                unit_price=variant.price,
                product_name=product.name if product else variant.sku,
                variant_sku=variant.sku,
            )
        )

    order.total_amount = total
    db.add(order)

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=translate(
                "We couldn't place the order. Nothing was charged — please try again.", locale
            ),
        )
    db.refresh(order)

    # Stock changed, so the search index is now stale for those products.
    for product in products_by_id.values():
        if product.is_published:
            db.refresh(product)
            index_product(product)

    return serialize_order(order, db)


@router.get("/{reference}", response_model=OrderOut)
def get_order_by_reference(
    reference: str,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    """
    Order confirmation / lookup page. Keyed by the random reference rather than the
    primary key so orders aren't enumerable by guests.
    """
    order = (
        db.query(Order)
        .options(selectinload(Order.items), joinedload(Order.customer))
        .filter(Order.reference == reference.upper())
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=404,
            detail=translate(
                "No order with that reference. Check the code from your confirmation email.",
                resolve_locale(accept_language),
            ),
        )
    return serialize_order(order, db)
