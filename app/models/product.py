from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    ForeignKey,
    Numeric,
    Boolean,
    DateTime,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class Product(Base):
    """
    A product is the "concept" (e.g. 'Dell PowerEdge R760 GPU Server').
    Its purchasable configurations (CPU count, RAM size, storage) live in ProductVariant.
    `specs` is a flexible JSONB bag so every category (CPU, RAM, servers, ...) can
    describe itself without needing a new DB migration each time.
    """

    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(280), unique=True, nullable=False, index=True)
    brand = Column(String(120), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)

    description = Column(Text, nullable=True)
    # Shared/base specs common to the product regardless of variant, e.g. {"socket": "LGA1700"}
    specs = Column(JSONB, nullable=False, default=dict)

    is_published = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    category = relationship("Category", back_populates="products")
    # Ordered so every surface (PDP picker, admin table, ES doc) lists configurations
    # cheapest-first instead of in whatever order the rows came back.
    variants = relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.price",
    )
    images = relationship(
        "ProductImage",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.sort_order",
    )


class ProductVariant(Base):
    """
    A specific purchasable SKU of a product, e.g. the '32GB RAM / 2TB NVMe' configuration
    of a given server. Price and stock live here, not on Product, since they differ per config.
    """

    __tablename__ = "product_variants"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    sku = Column(String(80), unique=True, nullable=False, index=True)

    # Variant-specific attributes, e.g. {"ram_gb": 32, "storage": "2TB NVMe"}
    attributes = Column(JSONB, nullable=False, default=dict)

    # Nullable on purpose: a SKU with no price is sold on request. The warehouse
    # this catalogue was loaded from carries no prices at all, so most rows start
    # here and get a figure once sales set one.
    price = Column(Numeric(18, 2), nullable=True)
    currency = Column(String(3), nullable=False, default="IRR")
    compare_at_price = Column(Numeric(18, 2), nullable=True)  # for "was X" strikethrough pricing
    stock_quantity = Column(Integer, nullable=False, default=0)

    # Commercially load-bearing for used server parts: a refurbished drive and an
    # engineering sample are not the same product to a buyer.
    condition = Column(String(20), nullable=False, default="new")

    product = relationship("Product", back_populates="variants")
    prices = relationship(
        "VariantPrice", back_populates="variant", cascade="all, delete-orphan"
    )


class VariantPrice(Base):
    """
    A price for one SKU in one currency.

    Prices are held per currency rather than converted at display time: an
    exchange rate moves, and a trade customer who was quoted a number expects
    that number. `Currency.rate_from_base` is only a fallback for currencies a
    SKU hasn't been priced in explicitly.
    """

    __tablename__ = "variant_prices"
    __table_args__ = (UniqueConstraint("variant_id", "currency", name="uq_variant_currency"),)

    id = Column(Integer, primary_key=True)
    variant_id = Column(Integer, ForeignKey("product_variants.id"), nullable=False)
    currency = Column(String(3), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)

    variant = relationship("ProductVariant", back_populates="prices")


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    url = Column(String(500), nullable=False)
    alt_text = Column(String(255), nullable=True)
    sort_order = Column(Integer, default=0)

    product = relationship("Product", back_populates="images")
