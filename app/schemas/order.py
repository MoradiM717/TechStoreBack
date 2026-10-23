from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.order import ORDER_STATUSES


# ---------- Storefront checkout ----------

class CartItemIn(BaseModel):
    variant_id: int
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        if v > 99:
            raise ValueError("quantity must be 99 or fewer per line")
        return v


class ShippingDetailsIn(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    postal_code: str
    country: str

    @field_validator("full_name", "address_line1", "city", "postal_code", "country")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    def as_block(self) -> str:
        """Flatten to the single `shipping_address` string the Order model stores."""
        lines = [
            self.full_name,
            self.address_line1,
            self.address_line2 or "",
            f"{self.postal_code} {self.city}".strip(),
            self.country,
        ]
        return "\n".join(line for line in lines if line)


class OrderCreate(BaseModel):
    """
    Prices are deliberately absent: the server re-reads every variant's current
    price from Postgres and ignores whatever the browser thought things cost.
    """
    items: list[CartItemIn] = Field(min_length=1)
    shipping: ShippingDetailsIn


# ---------- Output ----------

class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    variant_id: int
    quantity: int
    unit_price: Decimal
    product_name: str
    variant_sku: str
    # Filled in by the router so the storefront/admin can link back to the product
    product_slug: Optional[str] = None
    attributes: dict = Field(default_factory=dict)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    status: str
    total_amount: Decimal
    shipping_address: str
    created_at: datetime
    updated_at: datetime
    customer: Optional[CustomerOut] = None
    items: list[OrderItemOut] = Field(default_factory=list)


class OrderSummaryOut(BaseModel):
    """Row shape for the admin order table."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    status: str
    total_amount: Decimal
    created_at: datetime
    customer_email: Optional[str] = None
    customer_name: Optional[str] = None
    item_count: int = 0


class OrderListOut(BaseModel):
    total: int
    items: list[OrderSummaryOut] = Field(default_factory=list)


class OrderStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def known_status(cls, v: str) -> str:
        if v not in ORDER_STATUSES:
            raise ValueError(f"unknown status '{v}'; expected one of {', '.join(ORDER_STATUSES)}")
        return v
