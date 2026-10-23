"""Currencies the storefront can display prices in."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.currency import BASE_CURRENCY, Currency
from app.schemas.quote import CurrencyOut

router = APIRouter(prefix="/api/currencies", tags=["currencies"])


@router.get("")
def list_currencies(db: Session = Depends(get_db)):
    rows = (
        db.query(Currency)
        .filter(Currency.is_active.is_(True))
        .order_by(Currency.sort_order)
        .all()
    )
    return {
        "base": BASE_CURRENCY,
        "items": [CurrencyOut.model_validate(r) for r in rows],
    }
