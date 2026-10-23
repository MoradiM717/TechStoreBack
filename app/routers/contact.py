"""
Public contact form endpoint.

No mail is sent: there's no provider configured in this build, so an enquiry is
persisted and surfaced in the admin inbox for an operator to answer from their own
mail client. The storefront form says so rather than implying an auto-reply.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.i18n import resolve_locale, translate
from app.models.contact import ContactMessage
from app.schemas.contact import ContactMessageCreate

router = APIRouter(prefix="/api/contact", tags=["contact"])

# A single client shouldn't be able to fill the inbox. Cheap in-process throttle —
# swap for a shared store (Redis) if this ever runs on more than one worker.
_RECENT: dict[str, list[float]] = {}
MAX_PER_WINDOW = 5
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


@router.post("", status_code=201)
def submit_contact_message(
    payload: ContactMessageCreate,
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
                "You've sent several messages already. Give us a little time to reply "
                "before sending another.",
                locale,
            ),
        )

    message = ContactMessage(
        name=payload.name.strip(),
        email=str(payload.email),
        company=(payload.company or "").strip() or None,
        topic=payload.topic,
        message=payload.message.strip(),
        status="new",
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    return {
        "id": message.id,
        "received": True,
        "detail": translate(
            "Thanks — your message reached our team. We reply to enquiries within one "
            "working day.",
            locale,
        ),
    }
