"""
Requests for a quote.

The catalogue carries no prices today — the warehouse data this store was loaded
from has none — so the storefront's buy path is "ask us what it costs" rather
than "add to cart". A quote request is deliberately its own record and not an
Order: nothing is reserved, no stock moves, and the totals aren't known yet.
"""
import secrets

from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship

from app.core.database import Base

# new -> quoted -> won / lost; `cancelled` is for requests withdrawn by the buyer.
QUOTE_STATUSES = ("new", "in_progress", "quoted", "won", "lost", "cancelled")

QUOTE_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("in_progress", "quoted", "cancelled"),
    "in_progress": ("quoted", "cancelled"),
    "quoted": ("won", "lost"),
    "won": (),
    "lost": (),
    "cancelled": (),
}


def generate_quote_reference() -> str:
    """Public handle for a request, in the same shape as an order reference."""
    return f"RFQ-{secrets.token_hex(5).upper()}"


class QuoteRequest(Base):
    __tablename__ = "quote_requests"

    id = Column(Integer, primary_key=True)
    reference = Column(
        String(32), unique=True, nullable=False, index=True, default=generate_quote_reference
    )

    full_name = Column(String(160), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(40), nullable=True)
    company = Column(String(160), nullable=True)
    country = Column(String(80), nullable=True)

    # Which currency the buyer wants to be quoted in.
    currency = Column(String(3), nullable=False, default="IRR")
    note = Column(Text, nullable=True)

    status = Column(String(20), nullable=False, default="new", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    items = relationship(
        "QuoteRequestItem", back_populates="request", cascade="all, delete-orphan"
    )


class QuoteRequestItem(Base):
    __tablename__ = "quote_request_items"

    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey("quote_requests.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=True)

    quantity = Column(Integer, nullable=False, default=1)

    # Snapshots so a request stays readable if the catalogue changes underneath it,
    # and so a buyer can ask for a part number we don't actually list.
    product_name = Column(String(255), nullable=False, default="")
    part_number = Column(String(80), nullable=False, default="")

    request = relationship("QuoteRequest", back_populates="items")
    variant = relationship("ProductVariant")
