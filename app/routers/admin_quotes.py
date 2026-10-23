"""Admin side of quote requests: the sales queue."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.security import require_role
from app.models.currency import Currency
from app.models.quote import (
    QUOTE_STATUS_TRANSITIONS,
    QUOTE_STATUSES,
    QuoteRequest,
)
from app.routers.quotes import serialize_quote
from app.schemas.quote import (
    CurrencyOut,
    CurrencyRateUpdate,
    QuoteListOut,
    QuoteRequestOut,
    QuoteStatusUpdate,
    QuoteSummaryOut,
)

router = APIRouter(prefix="/api/admin/quotes", tags=["admin-quotes"])


@router.get("", response_model=QuoteListOut)
def list_quotes(
    status: str | None = Query(None),
    q: str | None = Query(None, description="Match reference, name, email or company"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    if status and status not in QUOTE_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status '{status}'. Expected one of: {', '.join(QUOTE_STATUSES)}.",
        )

    query = db.query(QuoteRequest)
    if status:
        query = query.filter(QuoteRequest.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                QuoteRequest.reference.ilike(like),
                QuoteRequest.full_name.ilike(like),
                QuoteRequest.email.ilike(like),
                QuoteRequest.company.ilike(like),
            )
        )

    total = query.count()
    new_count = (
        db.query(func.count(QuoteRequest.id)).filter(QuoteRequest.status == "new").scalar() or 0
    )

    rows = (
        query.options(selectinload(QuoteRequest.items))
        .order_by(QuoteRequest.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return QuoteListOut(
        total=total,
        new_count=new_count,
        items=[
            QuoteSummaryOut(
                id=r.id,
                reference=r.reference,
                full_name=r.full_name,
                email=r.email,
                company=r.company,
                currency=r.currency,
                status=r.status,
                created_at=r.created_at,
                item_count=len(r.items),
                total_quantity=sum(i.quantity for i in r.items),
            )
            for r in rows
        ],
    )


@router.get("/{quote_id}", response_model=QuoteRequestOut)
def get_quote(
    quote_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    quote = (
        db.query(QuoteRequest)
        .options(selectinload(QuoteRequest.items))
        .filter(QuoteRequest.id == quote_id)
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="Quote request not found")
    return serialize_quote(quote, db)


@router.get("/{quote_id}/transitions")
def allowed_transitions(
    quote_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    quote = db.query(QuoteRequest).filter(QuoteRequest.id == quote_id).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote request not found")
    return {"status": quote.status, "allowed": list(QUOTE_STATUS_TRANSITIONS.get(quote.status, ()))}


@router.patch("/{quote_id}/status", response_model=QuoteRequestOut)
def update_quote_status(
    quote_id: int,
    payload: QuoteStatusUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    quote = (
        db.query(QuoteRequest)
        .options(selectinload(QuoteRequest.items))
        .filter(QuoteRequest.id == quote_id)
        .first()
    )
    if not quote:
        raise HTTPException(status_code=404, detail="Quote request not found")

    if payload.status == quote.status:
        return serialize_quote(quote, db)

    allowed = QUOTE_STATUS_TRANSITIONS.get(quote.status, ())
    if payload.status not in allowed:
        readable = ", ".join(allowed) if allowed else "nothing — this request is closed"
        raise HTTPException(
            status_code=409,
            detail=f"A request that is '{quote.status}' can only move to: {readable}.",
        )

    quote.status = payload.status
    db.commit()
    db.refresh(quote)
    return serialize_quote(quote, db)


# ------------------------------------------------------------- currency rates

rates_router = APIRouter(prefix="/api/admin/currencies", tags=["admin-currencies"])


@rates_router.get("", response_model=list[CurrencyOut])
def list_currencies(
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    return db.query(Currency).order_by(Currency.sort_order).all()


@rates_router.patch("/{code}", response_model=CurrencyOut)
def update_currency(
    code: str,
    payload: CurrencyRateUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    currency = db.query(Currency).filter(Currency.code == code.upper()).first()
    if not currency:
        raise HTTPException(status_code=404, detail="Currency not found")

    currency.rate_from_base = payload.rate_from_base
    if payload.is_active is not None:
        currency.is_active = payload.is_active
    db.commit()
    db.refresh(currency)
    return currency
