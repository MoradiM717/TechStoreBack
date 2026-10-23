"""Admin inbox for storefront contact-form enquiries."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_role
from app.models.contact import CONTACT_STATUSES, ContactMessage
from app.schemas.contact import (
    ContactMessageListOut,
    ContactMessageOut,
    ContactStatusUpdate,
)

router = APIRouter(prefix="/api/admin/contact", tags=["admin-contact"])


@router.get("", response_model=ContactMessageListOut)
def list_messages(
    status: str | None = Query(None, description="Filter by a single status"),
    topic: str | None = None,
    q: str | None = Query(None, description="Match name, email, company or body"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor", "viewer")),
):
    if status and status not in CONTACT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status '{status}'. Expected one of: {', '.join(CONTACT_STATUSES)}.",
        )

    query = db.query(ContactMessage)
    if status:
        query = query.filter(ContactMessage.status == status)
    if topic:
        query = query.filter(ContactMessage.topic == topic)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                ContactMessage.name.ilike(like),
                ContactMessage.email.ilike(like),
                ContactMessage.company.ilike(like),
                ContactMessage.message.ilike(like),
            )
        )

    total = query.count()
    unread = db.query(func.count(ContactMessage.id)).filter(
        ContactMessage.status == "new"
    ).scalar()

    items = (
        query.order_by(ContactMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ContactMessageListOut(total=total, unread=unread or 0, items=items)


@router.patch("/{message_id}", response_model=ContactMessageOut)
def update_message_status(
    message_id: int,
    payload: ContactStatusUpdate,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin", "editor")),
):
    message = db.query(ContactMessage).filter(ContactMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    message.status = payload.status
    db.commit()
    db.refresh(message)
    return message


@router.delete("/{message_id}", status_code=204)
def delete_message(
    message_id: int,
    db: Session = Depends(get_db),
    _admin=Depends(require_role("admin")),
):
    message = db.query(ContactMessage).filter(ContactMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(message)
    db.commit()
