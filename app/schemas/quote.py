from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.currency import SUPPORTED_CURRENCIES
from app.models.quote import QUOTE_STATUSES


class QuoteItemIn(BaseModel):
    variant_id: Optional[int] = None
    part_number: Optional[str] = Field(default=None, max_length=80)
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def quantity_sane(cls, v: int) -> int:
        if v < 1:
            raise ValueError("quantity must be at least 1")
        if v > 9999:
            raise ValueError("quantity must be 9999 or fewer per line")
        return v


class QuoteRequestCreate(BaseModel):
    """
    A buyer asking what a basket of parts costs.

    Lines may name a variant we stock or just a part number we don't — a parts
    buyer often has a list that doesn't match our catalogue exactly, and turning
    those away loses the enquiry.
    """
    full_name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=40)
    company: Optional[str] = Field(default=None, max_length=160)
    country: Optional[str] = Field(default=None, max_length=80)
    currency: str = "IRR"
    note: Optional[str] = Field(default=None, max_length=4000)
    items: list[QuoteItemIn] = Field(min_length=1)

    @field_validator("full_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("currency")
    @classmethod
    def known_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"unknown currency '{v}'; expected one of {', '.join(SUPPORTED_CURRENCIES)}"
            )
        return v


class QuoteItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    variant_id: Optional[int] = None
    quantity: int
    product_name: str
    part_number: str
    product_slug: Optional[str] = None
    stock_quantity: Optional[int] = None


class QuoteRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    company: Optional[str] = None
    country: Optional[str] = None
    currency: str
    note: Optional[str] = None
    status: str
    created_at: datetime
    items: list[QuoteItemOut] = Field(default_factory=list)


class QuoteSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    reference: str
    full_name: str
    email: EmailStr
    company: Optional[str] = None
    currency: str
    status: str
    created_at: datetime
    item_count: int = 0
    total_quantity: int = 0


class QuoteListOut(BaseModel):
    total: int
    new_count: int = 0
    items: list[QuoteSummaryOut] = Field(default_factory=list)


class QuoteStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def known_status(cls, v: str) -> str:
        if v not in QUOTE_STATUSES:
            raise ValueError(f"unknown status '{v}'; expected one of {', '.join(QUOTE_STATUSES)}")
        return v


# ---------------------------------------------------------------- currencies

class CurrencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    rate_from_base: Decimal
    minor_units: int
    is_active: bool
    sort_order: int


class CurrencyRateUpdate(BaseModel):
    rate_from_base: Decimal
    is_active: Optional[bool] = None

    @field_validator("rate_from_base")
    @classmethod
    def positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("rate must be greater than zero")
        return v
