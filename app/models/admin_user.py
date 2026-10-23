from sqlalchemy import Column, Integer, String, Boolean, DateTime, func

from app.core.database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)

    # 'admin' = full access, 'editor' = manage products/orders, 'viewer' = read-only
    role = Column(String(20), nullable=False, default="editor")
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
