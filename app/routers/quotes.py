"""
Storefront quote requests.

The catalogue carries no prices, so this is the buy button. A request records
what the buyer wants and how to reach them; sales replies with a figure. Nothing
is reserved and no stock moves — that only happens once a quote is accepted and
becomes an order.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.i18n import resolve_locale, translate
from app.models.product import ProductVariant
from app.models.quote import QuoteRequest, QuoteRequestItem, generate_quote_reference
from app.schemas.quote import QuoteRequestCreate, QuoteRequestOut

router = APIRouter(prefix="/api/quotes", tags=["storefront-quotes"])

MAX_LINES = 60

# Same cheap in-process throttle as the contact form; swap for a shared store if
# this ever runs on more than one worker.
_RECENT: dict[str, list[float]] = {}
MAX_PER_WINDOW = 8
WINDOW_SECONDS = 600


def _rate_limited(client_ip: str) -> bool:
    import time

    now = time.time()
    hits = [t for t in _RECENT.get(client_ip, []) if now - t < WINDOW_SECONDS]
    _RECENT[client_ip] = hits
    if len(hits) >= MAX_PER_WINDOW:
        return True
    hits.append(now)
    return False


def serialize_quote(request: QuoteRequest, db: Session) -> dict:
    variant_ids = [i.variant_id for i in request.items if i.variant_id]
    variants = (
        db.query(ProductVariant)
        .options(joinedload(ProductVariant.product))
        .filter(ProductVariant.id.in_(variant_ids or [-1]))
        .all()
    )
    by_id = {v.id: v for v in variants}

    return {
        "id": request.id,
        "reference": request.reference,
        "full_name": request.full_name,
        "email": request.email,
        "phone": request.phone,
        "company": request.company,
        "country": request.country,
        "currency": request.currency,
        "note": request.note,
        "status": request.status,
        "created_at": request.created_at,
        "items": [
            {
                "id": item.id,
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "product_name": item.product_name,
                "part_number": item.part_number,
                "product_slug": (
                    by_id[item.variant_id].product.slug
                    if item.variant_id in by_id and by_id[item.variant_id].product
                    else None
                ),
                "stock_quantity": (
                    by_id[item.variant_id].stock_quantity if item.variant_id in by_id else None
                ),
            }
            for item in request.items
        ],
    }


@router.post("", response_model=QuoteRequestOut, status_code=201)
def create_quote_request(
    payload: QuoteRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    locale = resolve_locale(accept_language)
    client_ip = request.client.host if request.client else "unknown"

    if _rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail=translate(
                "You've sent several requests already. Give us a little time to reply "
                "before sending another.",
                locale,
            ),
        )

    if len(payload.items) > MAX_LINES:
        raise HTTPException(
            status_code=422,
            detail=translate(
                f"A request can hold up to {MAX_LINES} lines. Split a longer list "
                "across two requests, or send it to us as a file.",
                locale,
            ),
        )

    # Resolve the lines that name something we stock; keep the rest as free text.
    wanted = [i.variant_id for i in payload.items if i.variant_id]
    variants = (
        db.query(ProductVariant)
        .options(joinedload(ProductVariant.product))
        .filter(ProductVariant.id.in_(wanted or [-1]))
        .all()
    )
    by_id = {v.id: v for v in variants}

    quote = QuoteRequest(
        reference=generate_quote_reference(),
        full_name=payload.full_name.strip(),
        email=str(payload.email),
        phone=(payload.phone or "").strip() or None,
        company=(payload.company or "").strip() or None,
        country=(payload.country or "").strip() or None,
        currency=payload.currency,
        note=(payload.note or "").strip() or None,
        status="new",
    )

    for line in payload.items:
        variant = by_id.get(line.variant_id) if line.variant_id else None
        if variant is not None:
            name = variant.product.name if variant.product else variant.sku
            part_number = variant.sku
        else:
            # A part number we don't list is still a lead worth capturing.
            name = (line.part_number or "").strip() or "Unlisted part"
            part_number = (line.part_number or "").strip()
            if not part_number:
                continue
        quote.items.append(
            QuoteRequestItem(
                variant_id=variant.id if variant else None,
                quantity=line.quantity,
                product_name=name[:255],
                part_number=part_number[:80],
            )
        )

    if not quote.items:
        raise HTTPException(
            status_code=422,
            detail=translate(
                "The request didn't contain anything we could read. Add at least one "
                "part number or product.",
                locale,
            ),
        )

    db.add(quote)
    db.commit()
    db.refresh(quote)
    return serialize_quote(quote, db)


@router.get("/{reference}", response_model=QuoteRequestOut)
def get_quote_request(
    reference: str,
    db: Session = Depends(get_db),
    accept_language: str | None = Header(default=None),
):
    quote = (
        db.query(QuoteRequest)
        .options(selectinload(QuoteRequest.items))
        .filter(QuoteRequest.reference == reference.upper())
        .first()
    )
    if not quote:
        raise HTTPException(
            status_code=404,
            detail=translate(
                "No request with that reference. Check the code from your confirmation screen.",
                resolve_locale(accept_language),
            ),
        )
    return serialize_quote(quote, db)
