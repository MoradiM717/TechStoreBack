"""
Currencies the catalogue can be priced and displayed in.

The store's books are in IRR; USD and AED exist because a large share of this
trade is quoted for export. Rates are stored rather than fetched live: a quoted
price has to be stable for the life of the quote, and nobody wants a price that
moves while a customer reads it. An operator updates them from the admin panel.
"""
from decimal import Decimal

from sqlalchemy import Column, Integer, String, Numeric, Boolean, DateTime, func

from app.core.database import Base

BASE_CURRENCY = "IRR"

# code -> (English name, minor units, display order)
SUPPORTED_CURRENCIES: dict[str, tuple[str, int, int]] = {
    "IRR": ("Iranian Rial", 0, 1),
    "USD": ("US Dollar", 2, 2),
    "AED": ("UAE Dirham", 2, 3),
}


class Currency(Base):
    __tablename__ = "currencies"

    code = Column(String(3), primary_key=True)
    name = Column(String(60), nullable=False)

    # Units of this currency per 1 unit of BASE_CURRENCY. The base row is 1.
    rate_from_base = Column(Numeric(20, 10), nullable=False, default=Decimal("1"))

    # Rial is quoted without decimals; USD and AED take two.
    minor_units = Column(Integer, nullable=False, default=2)

    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
