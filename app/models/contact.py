from sqlalchemy import Column, Integer, String, Text, DateTime, func

from app.core.database import Base

# new -> read -> answered; `archived` hides it from the default admin inbox view.
CONTACT_STATUSES = ("new", "read", "answered", "archived")

# What the enquiry is about. Kept as a short vocabulary rather than free text so the
# admin inbox can filter, and so the storefront form can offer a sensible dropdown.
CONTACT_TOPICS = ("sales", "support", "order", "partnership", "other")


class ContactMessage(Base):
    """
    An enquiry submitted from the storefront's contact form.

    Deliberately not wired to outbound email: there's no mail provider configured in
    this build, so messages land in the admin inbox and an operator replies from
    their own mail client. See the README's "Deliberately stubbed" section.
    """

    __tablename__ = "contact_messages"

    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    company = Column(String(160), nullable=True)
    topic = Column(String(30), nullable=False, default="sales")
    message = Column(Text, nullable=False)

    status = Column(String(20), nullable=False, default="new", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
