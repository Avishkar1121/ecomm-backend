import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import settings

_ph = PasswordHasher()
# Verified against when the email does not exist, so login takes about the same
# time either way (stops attackers discovering which emails are registered).
_DUMMY_HASH = _ph.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


# ---- access token (JWT, short-lived, sent in the Authorization header) ----
def create_access_token(user_id, extra_claims: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        **(extra_claims or {}),          # email, name, roles - display only, never trusted for auth
        # Security-critical claims come last so extra_claims can never override them.
        "sub": str(user_id),
        "type": "access",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        options={"require": ["exp", "iat", "sub", "iss"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


# ---- opaque tokens (refresh / password reset): random, only the HASH is stored ----
def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_opaque_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)