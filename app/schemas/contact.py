from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.contact import CONTACT_STATUSES, CONTACT_TOPICS


class ContactMessageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    company: Optional[str] = Field(default=None, max_length=160)
    topic: str = "sales"
    message: str = Field(min_length=10, max_length=5000)

    @field_validator("name", "message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("topic")
    @classmethod
    def known_topic(cls, v: str) -> str:
        if v not in CONTACT_TOPICS:
            raise ValueError(f"unknown topic '{v}'; expected one of {', '.join(CONTACT_TOPICS)}")
        return v


class ContactMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr
    company: Optional[str] = None
    topic: str
    message: str
    status: str
    created_at: datetime


class ContactMessageListOut(BaseModel):
    total: int
    unread: int
    items: list[ContactMessageOut] = Field(default_factory=list)


class ContactStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def known_status(cls, v: str) -> str:
        if v not in CONTACT_STATUSES:
            raise ValueError(
                f"unknown status '{v}'; expected one of {', '.join(CONTACT_STATUSES)}"
            )
        return v
