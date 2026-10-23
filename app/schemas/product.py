from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------- Category ----------

class CategoryBase(BaseModel):
    name: str
    slug: str
    parent_id: Optional[int] = None


class CategoryCreate(CategoryBase):
    @field_validator("name", "slug")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    parent_id: Optional[int] = None


class CategoryOut(CategoryBase):
    model_config = ConfigDict(from_attributes=True)
    id: int


class CategoryDetailOut(CategoryOut):
    """Category plus the counts the admin list and storefront nav need."""
    product_count: int = 0
    published_product_count: int = 0


# ---------- Product Image ----------

class ProductImageCreate(BaseModel):
    url: str
    alt_text: Optional[str] = None
    sort_order: int = 0


class ProductImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    url: str
    alt_text: Optional[str] = None
    sort_order: int = 0


# ---------- Product Variant ----------

class VariantPriceIn(BaseModel):
    """One price, in one currency, for one SKU."""
    currency: str
    amount: Decimal

    @field_validator("currency")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("amount")
    @classmethod
    def not_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("price must not be negative")
        return v


class VariantPriceOut(VariantPriceIn):
    model_config = ConfigDict(from_attributes=True)


class ProductVariantBase(BaseModel):
    sku: str
    attributes: dict = Field(default_factory=dict)
    # None means "sold on request" — the catalogue imported from the warehouse
    # has no prices, so this is the common case rather than an edge case.
    price: Optional[Decimal] = None
    currency: str = "IRR"
    condition: str = "new"
    compare_at_price: Optional[Decimal] = None
    stock_quantity: int = 0

    @field_validator("sku")
    @classmethod
    def sku_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("SKU must not be empty")
        return v

    @field_validator("price")
    @classmethod
    def price_not_negative(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v < 0:
            raise ValueError("price must not be negative")
        return v

    @field_validator("currency")
    @classmethod
    def currency_code(cls, v: str) -> str:
        return (v or "IRR").strip().upper()[:3]

    @field_validator("stock_quantity")
    @classmethod
    def stock_not_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("stock quantity must not be negative")
        return v


class ProductVariantCreate(ProductVariantBase):
    """On update, an `id` marks an existing variant to keep; omitting it creates a new one."""
    id: Optional[int] = None
    prices: list[VariantPriceIn] = Field(default_factory=list)


class ProductVariantOut(ProductVariantBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    prices: list[VariantPriceOut] = Field(default_factory=list)


# ---------- Product ----------

class ProductBase(BaseModel):
    name: str
    slug: str
    brand: str
    category_id: int
    description: Optional[str] = None
    specs: dict = Field(default_factory=dict)
    is_published: bool = False


class ProductCreate(ProductBase):
    variants: list[ProductVariantCreate] = Field(default_factory=list)
    images: list[ProductImageCreate] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    """All fields optional for PATCH-style partial updates."""
    name: Optional[str] = None
    slug: Optional[str] = None
    brand: Optional[str] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    specs: Optional[dict] = None
    is_published: Optional[bool] = None
    # When present, the variant/image lists are treated as the full desired set:
    # entries with an id are updated, entries without are created, anything
    # missing from the list is removed.
    variants: Optional[list[ProductVariantCreate]] = None
    images: Optional[list[ProductImageCreate]] = None


class ProductOut(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
    category: Optional[CategoryOut] = None
    variants: list[ProductVariantOut] = Field(default_factory=list)
    images: list[ProductImageOut] = Field(default_factory=list)


class ProductListOut(BaseModel):
    """Lightweight representation for list/search views."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    brand: str
    is_published: bool
    # Cheapest variant price, computed in the router - shown as "from $X"
    starting_price: Optional[Decimal] = None
    thumbnail_url: Optional[str] = None
