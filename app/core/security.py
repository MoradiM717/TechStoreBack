from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.admin_user import AdminUser

# bcrypt is used directly rather than through passlib: passlib 1.7.4 is unmaintained
# and its backend probe breaks against bcrypt >= 4.1. The stored hash format ($2b$)
# is identical, so hashes written by either path stay valid.
BCRYPT_MAX_BYTES = 72  # bcrypt truncates beyond this; we reject instead of silently cutting

# Two separate token URLs so storefront customers and admin staff never share a login surface
oauth2_admin_scheme = OAuth2PasswordBearer(tokenUrl="/api/admin/auth/login")


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password is {len(encoded)} bytes; bcrypt accepts at most {BCRYPT_MAX_BYTES}. "
            "Use a shorter passphrase."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8")[:BCRYPT_MAX_BYTES],
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # Malformed/legacy hash in the row - treat as a failed login, never a 500.
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_admin(
    token: str = Depends(oauth2_admin_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    payload = decode_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(AdminUser).filter(AdminUser.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Admin user not found or inactive")
    return user


def require_role(*allowed_roles: str):
    """Dependency factory: require_role('admin', 'editor') restricts an endpoint to those roles."""

    def _check(current_admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if current_admin.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_admin

    return _check
