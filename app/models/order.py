import secrets

from sqlalchemy import Column, Integer, String, ForeignKey, Numeric, DateTime, func
from sqlalchemy.orm import relationship

from app.core.database import Base

# pending -> paid -> processing -> shipped -> delivered
# Anything before `delivered` can still be cancelled; a paid order can be refunded.
ORDER_STATUSES = (
    "pending",
    "paid",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)

# Which transitions the admin panel is allowed to make. Keeping this next to the
# model means the API and the UI can't drift apart on what's legal.
ORDER_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("paid", "cancelled"),
    "paid": ("processing", "refunded", "cancelled"),
    "processing": ("shipped", "cancelled"),
    "shipped": ("delivered",),
    "delivered": ("refunded",),
    "cancelled": (),
    "refunded": (),
}

# Statuses that mean the reserved stock has been handed back.
STOCK_RELEASING_STATUSES = ("cancelled", "refunded")


def generate_order_reference() -> str:
    """
    Public, non-enumerable order handle. The storefront confirmation page looks an
    order up by this instead of by primary key, so guests can't walk other people's
    orders by incrementing an id.
    """
    return f"TS-{secrets.token_hex(6).upper()}"


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    # Nullable: guest checkout creates a customer record with no credentials.
    # Customer-facing accounts (register/login) are a separate, not-yet-built surface.
    hashed_password = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=True)
    phone = Column(String(30), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    reference = Column(
        String(32), unique=True, nullable=False, index=True, default=generate_order_reference
    )
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)

    status = Column(String(30), nullable=False, default="pending")
    total_amount = Column(Numeric(12, 2), nullable=False)

    shipping_address = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False)

    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False)  # snapshot at time of purchase

    # Name/SKU snapshots so an order stays readable after a product is renamed or deleted.
    product_name = Column(String(255), nullable=False, default="")
    variant_sku = Column(String(80), nullable=False, default="")

    order = relationship("Order", back_populates="items")
    variant = relationship("ProductVariant")
