from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from .core.database import get_db
from .core.security import decode_access_token

# Swagger's "Authorize" button posts a form to this URL
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

_USER_SQL = text("""
    SELECT u.id, u.first_name, u.last_name, u.email, u.phone, u.avatar_url,
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


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> dict:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                                 headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = UUID(decode_access_token(token)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    # Roles are read from the DB on every request, so role changes apply immediately
    user = fetch_user(db, user_id)
    if not user:
        raise unauthorized
    return user


def require_role(*allowed: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        if not set(allowed) & set(user["roles"]):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not enough permissions")
        return user
    return checker