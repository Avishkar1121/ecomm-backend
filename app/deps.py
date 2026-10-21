from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from .core.database import get_db
from .core.errors import api_error
from .core.security import decode_token

# Swagger's "Authorize" button posts a form to this URL
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

_USER_SQL = text("""
    SELECT u.id, u.first_name, u.last_name, u.email, u.phone, u.avatar_url,
           (u.email_verified_at IS NOT NULL) AS email_verified,
           (u.phone_verified_at IS NOT NULL) AS phone_verified,
           COALESCE(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS roles
      FROM users u
      LEFT JOIN user_roles ur ON ur.user_id = u.id
      LEFT JOIN roles r ON r.id = ur.role_id
     WHERE u.id = :id AND u.deleted_at IS NULL AND u.is_active
     GROUP BY u.id
""")


def fetch_user(db: Session, user_id: UUID) -> dict | None:
    row = db.execute(_USER_SQL, {"id": user_id}).mappings().first()
    return dict(row) if row else None


def _resolve_user(token: str, db: Session, allowed: tuple[str, ...]) -> tuple[dict, str]:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                                 headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = decode_token(token, allowed)
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    # Roles and verification status are read from the DB on every request
    user = fetch_user(db, user_id)
    if not user:
        raise unauthorized
    return user, payload["type"]


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """Normal routes. Accepts ONLY full access tokens, and only for activated accounts
    (at least one of email/phone verified)."""
    user, _ = _resolve_user(token, db, ("access",))
    if not (user["email_verified"] or user["phone_verified"]):
        raise api_error(403, "VERIFICATION_REQUIRED", "Please verify your account to continue.")
    return user


def get_otp_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    """OTP endpoints only. Accepts the limited 'verify' token (not yet activated) as well as
    a full access token (an activated user verifying their second channel)."""
    user, token_type = _resolve_user(token, db, ("access", "verify"))
    user["token_type"] = token_type
    return user


def require_role(*allowed: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if not set(allowed) & set(user["roles"]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not enough permissions")
        return user
    return checker